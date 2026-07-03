"""Static AST pre-screen for strategy source — lint-grade UX, NOT the boundary (§8.3).

This pass reads the strategy's AST *before* a run and reports, with line and
column precision, the things §8.3 forbids: imports outside the allowlist,
non-deterministic clocks/RNGs (use ``ctx.now`` / ``ctx.rng``), the dynamic-exec
and filesystem builtins (``eval``/``exec``/``compile``/``open``/``__import__``),
the reflection escape chain (``__subclasses__``/``__globals__`` …), and unsafe
deserialization (``pickle.load`` and friends).

Framing, restated because it is the whole design thesis: **this is UX, not
security.** A determined ``getattr`` chain can construct any of these at runtime
and the screen will not see it — that is *fine*, because the process boundary
(env-scrubbed subprocess, no parent-memory references, RLIMIT, OS layer where
available) is what actually contains a strategy. The screen exists so an honest
author gets "you can't import requests on line 3" instantly instead of after a
wasted sandbox spawn. Nothing relies on it for isolation (§8.3), which is why the
runner keeps it *opt-in* and the boundary-proof escape tests deliberately run
unscreened.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .errors import SandboxError
from .policy import ALLOWED_MODULES

# Builtins that execute code, touch the filesystem, or re-open the import system.
# ``getattr``/``setattr``/``hasattr`` are intentionally NOT here — they are
# legitimate and contained by the boundary (§8.3 / policy.py).
FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {"eval", "exec", "compile", "open", "input",
     "globals", "locals", "vars", "breakpoint", "__import__"}
)

# The reflection chain a screen-only escape would climb (``object.__subclasses__``
# → a class's ``__init__.__globals__`` → ``os``). Flagged as UX; the boundary is
# what makes reaching ``os`` worthless (empty child env, no secrets).
ESCAPE_DUNDERS: frozenset[str] = frozenset(
    {"__subclasses__", "__globals__", "__bases__", "__mro__",
     "__code__", "__closure__", "__builtins__", "__base__"}
)

# Non-deterministic sources — a reproducible backtest forbids them; the strategy
# gets determinism through ``ctx.rng`` / ``ctx.now`` (§8.3, §8.5).
NONDETERMINISTIC_MODULES: dict[str, str] = {
    "random": "ctx.rng",
    "secrets": "ctx.rng",
    "time": "ctx.now",
    "datetime": "ctx.now",
}

# The #1 ML-supply-chain RCE vector — loading a model from arbitrary bytes. Models
# persist only through the managed ``ctx.model_store`` (§8.5). These modules are
# not importable anyway; the rule gives a precise message instead of a generic
# "import not allowed".
UNSAFE_DESERIALIZERS: dict[str, frozenset[str]] = {
    "pickle": frozenset({"load", "loads", "Unpickler"}),
    "_pickle": frozenset({"load", "loads", "Unpickler"}),
    "cpickle": frozenset({"load", "loads", "Unpickler"}),
    "cloudpickle": frozenset({"load", "loads"}),
    "dill": frozenset({"load", "loads"}),
    "joblib": frozenset({"load"}),
    "torch": frozenset({"load"}),
    "yaml": frozenset({"load", "unsafe_load"}),
    "marshal": frozenset({"load", "loads"}),
    "shelve": frozenset({"open"}),
}


@dataclass(frozen=True, order=True)
class ScreenViolation:
    """One line-precise reason the source failed the screen (§8.3)."""

    line: int
    col: int
    code: str
    message: str

    def __str__(self) -> str:
        return f"line {self.line}:{self.col} [{self.code}] {self.message}"


class StrategyScreenError(SandboxError):
    """The static screen rejected the source (a UX failure, raised pre-run)."""

    def __init__(self, violations: list[ScreenViolation]) -> None:
        self.violations = violations
        body = "\n".join(f"  {v}" for v in violations)
        super().__init__(
            f"strategy failed the AST/import screen ({len(violations)} issue(s)):\n{body}"
        )


class _Screener(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[ScreenViolation] = []

    def _add(self, node: ast.AST, code: str, message: str) -> None:
        self.violations.append(
            ScreenViolation(
                line=getattr(node, "lineno", 0),
                col=getattr(node, "col_offset", 0) + 1,
                code=code,
                message=message,
            )
        )

    def _check_module(self, node: ast.AST, dotted: str) -> None:
        top = dotted.split(".", 1)[0]
        if top in NONDETERMINISTIC_MODULES:
            self._add(
                node, "nondeterminism",
                f"import of {dotted!r} is non-deterministic — use "
                f"{NONDETERMINISTIC_MODULES[top]} instead (§8.3)",
            )
        elif top not in ALLOWED_MODULES:
            self._add(
                node, "import-not-allowed",
                f"import of {dotted!r} is not on the sandbox allowlist (§8.3)",
            )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(alias, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level and node.level > 0:
            self._add(
                node, "import-not-allowed",
                "relative imports are not allowed in the sandbox (§8.3)",
            )
        else:
            self._check_module(node, node.module or "")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
            self._add(
                node, "forbidden-call",
                f"{func.id}() is not available in the sandbox (§8.3)",
            )
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            mod = func.value.id.lower()
            attrs = UNSAFE_DESERIALIZERS.get(mod)
            if attrs is not None and func.attr in attrs:
                self._add(
                    node, "unsafe-deserialization",
                    f"{func.value.id}.{func.attr}() loads code from bytes — models "
                    "persist only via ctx.model_store (§8.5)",
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in ESCAPE_DUNDERS:
            self._add(
                node, "reflection-escape",
                f"attribute {node.attr!r} is a sandbox-escape reflection path (§8.3)",
            )
        self.generic_visit(node)


def screen_source(source: str, filename: str = "<strategy>") -> list[ScreenViolation]:
    """Return every screen violation in ``source``, sorted by (line, col).

    A syntax error is itself a single violation (there is nothing to run), so a
    caller always gets a structured list rather than a raw ``SyntaxError``.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [
            ScreenViolation(
                line=exc.lineno or 0,
                col=(exc.offset or 0),
                code="syntax-error",
                message=exc.msg or "invalid syntax",
            )
        ]
    screener = _Screener()
    screener.visit(tree)
    return sorted(screener.violations)


def screen_or_raise(source: str, filename: str = "<strategy>") -> None:
    """Raise :class:`StrategyScreenError` if ``source`` has any screen violations."""
    violations = screen_source(source, filename)
    if violations:
        raise StrategyScreenError(violations)
