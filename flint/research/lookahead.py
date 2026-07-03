"""Look-ahead / leakage linter — a FreqAI-style causality check (§8.5, §11, D27).

A backtest that peeks at the future looks brilliant and is worthless. This linter
catches the common ways strategy code leaks future information, in two complementary
passes:

* **Static AST pass** — flags the syntactic tells without running anything:
  ``shift(-n)`` (a negative shift pulls the future back), forward ``.iloc[i+1]``
  indexing, unbounded full-frame aggregates (``mean``/``std``/``max``/``.fit`` over the
  whole history — the §8.5 feature-causality rule: features must use *rolling or
  expanding* statistics only), a degenerate (``<= 0``) label horizon, and the D27
  survivorship tell (ranking markets with ``nlargest``/``sort_values`` over full
  history instead of the point-in-time ``UniverseResolver``).
* **Truncation probe** — the dynamic check the static pass cannot do: run the feature
  computation on the full frame and again on the frame with its last rows removed, then
  diff. A value at an *earlier* row that changes when future rows disappear used the
  future — the linter reports exactly which columns moved.

**This linter proves presence, never absence.** Its result says "no leak detected",
**never** "leak-free": subtle statistical leaks, label-definition leaks, and
test-range feature selection are documented blind spots carried on every result. It is
lint-grade UX (like the sandbox's import screen, D25) — a help, not a guarantee.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

# --- finding categories -------------------------------------------------------
FUTURE_SHIFT = "future_shift"
FUTURE_INDEX = "future_index"
UNBOUNDED_AGGREGATE = "unbounded_aggregate"
DEGENERATE_LABEL_HORIZON = "degenerate_label_horizon"
UNIVERSE_LOOKAHEAD = "universe_lookahead"
TRUNCATION_DIVERGENCE = "truncation_divergence"

# Bare aggregates over a whole frame leak the future into every row unless they run on
# a bounded window (§8.5). ``fit`` is training on the full frame — the same leak.
_AGGREGATES = frozenset(
    {"mean", "std", "max", "min", "sum", "var", "median", "quantile", "corr", "cov"}
)
_WINDOW_GUARDS = frozenset({"rolling", "expanding", "ewm"})  # bounded → not a leak
_RANKERS = frozenset({"nlargest", "nsmallest", "sort_values", "rank"})

# Stated on every result — the linter's known limits (§11). Presence, not absence.
BLIND_SPOTS: tuple[str, ...] = (
    "label-definition leaks: a target() that folds the answer into a feature is "
    "invisible to static analysis and can survive the truncation probe.",
    "test-range feature/param selection: choosing features by their out-of-sample "
    "score leaks the whole study — walk-forward + DSR (§11) address it, not this pass.",
    "subtle statistical leaks (a full-window normalization that shifts values only "
    "slightly) can slip under the truncation probe's tolerance.",
    "dynamic dispatch (getattr/eval/exec, vectorized ops in C) hides calls from the AST.",
    "the static pass is lint-grade UX, not a security boundary (the sandbox is, D25).",
)


@dataclass(frozen=True)
class LeakFinding:
    """One detected (or suspected) leak: a category, a human message, and — for static
    findings — the source location and exact snippet that triggered it."""

    category: str
    message: str
    line: int | None = None
    col: int | None = None
    snippet: str | None = None


@dataclass(frozen=True)
class LookaheadResult:
    """The linter's verdict. ``findings`` is what fired; ``checks_run`` is what was
    actually attempted (so an empty result is honest about coverage); ``blind_spots``
    is always populated. :meth:`summary` says "no leak detected", never "leak-free"."""

    findings: tuple[LeakFinding, ...]
    checks_run: tuple[str, ...]
    blind_spots: tuple[str, ...] = field(default_factory=lambda: BLIND_SPOTS)

    @property
    def leak_detected(self) -> bool:
        return bool(self.findings)

    def categories(self) -> tuple[str, ...]:
        # de-duplicated, order-preserving
        seen: dict[str, None] = {}
        for f in self.findings:
            seen.setdefault(f.category, None)
        return tuple(seen)

    def summary(self) -> str:
        checks = ", ".join(self.checks_run) or "none"
        if not self.findings:
            return (
                f"no leak detected across {len(self.checks_run)} check(s): {checks}. "
                "This is not proof of absence — see blind_spots."
            )
        lines = [f"{len(self.findings)} potential leak(s) detected across: {checks}"]
        for f in self.findings:
            loc = f" (line {f.line})" if f.line is not None else ""
            lines.append(f"  [{f.category}]{loc} {f.message}")
        lines.append("Detection proves presence, not absence — see blind_spots.")
        return "\n".join(lines)


# --- static AST pass ----------------------------------------------------------


def _is_negative(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value < 0
    return isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)


def _nonpositive_constant(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and node.value <= 0


class _LeakVisitor(ast.NodeVisitor):
    """Collects static look-ahead findings, tracking the enclosing function name so
    messages can say *where* (e.g. "in features()")."""

    def __init__(self, source: str, *, check_universe: bool) -> None:
        self._source = source
        self._check_universe = check_universe
        self._fn_stack: list[str] = []
        self.findings: list[LeakFinding] = []

    # -- helpers
    def _where(self) -> str:
        return f" in {self._fn_stack[-1]}()" if self._fn_stack else ""

    def _add(self, category: str, message: str, node: ast.AST) -> None:
        self.findings.append(
            LeakFinding(
                category=category,
                message=message,
                line=getattr(node, "lineno", None),
                col=getattr(node, "col_offset", None),
                snippet=ast.get_source_segment(self._source, node),
            )
        )

    # -- scope tracking
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._fn_stack.append(node.name)
        self.generic_visit(node)
        self._fn_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._fn_stack.append(node.name)
        self.generic_visit(node)
        self._fn_stack.pop()

    # -- degenerate label horizon (assignments + dict kwargs)
    def _check_label_horizon_target(self, name: str, value: ast.expr, node: ast.AST) -> None:
        if name in ("label_horizon", "horizon") and _nonpositive_constant(value):
            self._add(
                DEGENERATE_LABEL_HORIZON,
                f"{name}={ast.literal_eval(value)} is degenerate — a label horizon must "
                "look strictly forward (>= 1 bar), or the label leaks the current bar",
                node,
            )

    def visit_Assign(self, node: ast.Assign) -> None:
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                self._check_label_horizon_target(tgt.id, node.value, node)
        self.generic_visit(node)

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg:
            self._check_label_horizon_target(node.arg, node.value, node)
        self.generic_visit(node)

    # -- calls: shift(-n), unbounded aggregate/fit, universe rankers
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            attr = func.attr
            if attr == "shift" and node.args and _is_negative(node.args[0]):
                self._add(
                    FUTURE_SHIFT,
                    f"shift() with a negative periods argument pulls future rows back{self._where()}",
                    node,
                )
            elif attr == "fit":
                self._add(
                    UNBOUNDED_AGGREGATE,
                    f"fit() trains on the whole frame handed to it{self._where()} — "
                    "training must happen per walk-forward window, never inside features()",
                    node,
                )
            elif attr in _AGGREGATES and not self._receiver_is_windowed(func.value):
                self._add(
                    UNBOUNDED_AGGREGATE,
                    f".{attr}() over an unbounded frame{self._where()} leaks the future into "
                    "every row — use a rolling()/expanding() window (§8.5)",
                    node,
                )
            elif self._check_universe and attr in _RANKERS:
                self._add(
                    UNIVERSE_LOOKAHEAD,
                    f".{attr}() ranks over the data given to it{self._where()}; ranking a "
                    "universe on full history is survivorship bias — resolve membership "
                    "point-in-time via the UniverseResolver (D27)",
                    node,
                )
        self.generic_visit(node)

    def _receiver_is_windowed(self, value: ast.expr) -> bool:
        """True when an aggregate's receiver is a rolling()/expanding()/ewm() result —
        i.e. the aggregate is bounded and not a leak."""
        return (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr in _WINDOW_GUARDS
        )

    # -- forward .iloc[i + k]
    def visit_Subscript(self, node: ast.Subscript) -> None:
        value = node.value
        if isinstance(value, ast.Attribute) and value.attr == "iloc":
            if self._slice_reaches_forward(node.slice):
                self._add(
                    FUTURE_INDEX,
                    f".iloc[...] indexes forward of the current row{self._where()} — a "
                    "positional offset ahead of 'now' reads the future",
                    node,
                )
        self.generic_visit(node)

    def _slice_reaches_forward(self, sl: ast.expr) -> bool:
        # i + k  (forward offset), or a slice whose upper bound is i + k, or a
        # negative-step slice (reverse walk over future rows).
        if isinstance(sl, ast.BinOp) and isinstance(sl.op, ast.Add):
            return True
        if isinstance(sl, ast.Slice):
            if isinstance(sl.upper, ast.BinOp) and isinstance(sl.upper.op, ast.Add):
                return True
            if sl.step is not None and _is_negative(sl.step):
                return True
        return False


def lint_source(source: str, *, check_universe: bool = True) -> list[LeakFinding]:
    """Run the static AST pass over ``source`` and return the raw findings.

    Raises ``SyntaxError`` if ``source`` does not parse — a strategy that will not run
    is the sandbox/validation layer's concern, not the leak linter's.
    """
    tree = ast.parse(source)
    visitor = _LeakVisitor(source, check_universe=check_universe)
    visitor.visit(tree)
    return visitor.findings


# --- truncation probe (dynamic) ----------------------------------------------


def _row_diff(full_row: object, trunc_row: object, *, atol: float) -> list[str]:
    """Column names (or [""] for a scalar) that differ between two aligned feature rows."""
    if isinstance(full_row, Mapping) and isinstance(trunc_row, Mapping):
        changed: list[str] = []
        for key in set(full_row) | set(trunc_row):
            if not _scalar_close(full_row.get(key), trunc_row.get(key), atol=atol):
                changed.append(str(key))
        return sorted(changed)
    return [] if _scalar_close(full_row, trunc_row, atol=atol) else [""]


def _scalar_close(a: object, b: object, *, atol: float) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= atol
    return a == b


def truncation_probe(
    feature_fn: Callable[[Sequence], Sequence],
    frame: Sequence,
    *,
    truncate: int = 1,
    atol: float = 1e-9,
) -> list[LeakFinding]:
    """Differential re-run: compute features on the full ``frame`` and on ``frame`` with
    its last ``truncate`` rows removed, then diff the overlapping prefix.

    ``feature_fn`` maps a frame to one output row per input row (scalars, or mappings of
    column -> value). Any earlier row that changes when future rows are dropped used the
    future; a finding is emitted per changed row (naming the changed columns). ``frame``
    only needs ``len()`` and prefix slicing (``frame[:k]``) — a list works, so tests need
    no market-like data (D26).
    """
    if truncate < 1:
        raise ValueError("truncate must be >= 1")
    n = len(frame)
    if n <= truncate:
        return []  # nothing overlaps to compare
    full = list(feature_fn(frame))
    trunc = list(feature_fn(frame[: n - truncate]))
    findings: list[LeakFinding] = []
    for i in range(min(len(trunc), len(full))):
        changed = _row_diff(full[i], trunc[i], atol=atol)
        if changed:
            cols = ", ".join(c for c in changed if c) or "value"
            findings.append(
                LeakFinding(
                    TRUNCATION_DIVERGENCE,
                    f"row {i} column(s) [{cols}] changed when {truncate} future row(s) were "
                    "removed — the feature depends on data ahead of that row",
                    line=None,
                    col=None,
                )
            )
    return findings


# --- label horizon (runtime-known value) --------------------------------------


def check_label_horizon(horizon: int) -> LeakFinding | None:
    """Flag a non-positive label horizon known at call time (a degenerate label that
    does not look strictly forward)."""
    if horizon <= 0:
        return LeakFinding(
            DEGENERATE_LABEL_HORIZON,
            f"label_horizon={horizon} is degenerate — must be >= 1 bar so the label "
            "looks strictly forward and never encodes the current bar",
        )
    return None


# --- the composite entry point ------------------------------------------------


def analyze(
    source: str | None = None,
    *,
    check_universe: bool = True,
    label_horizon: int | None = None,
    feature_fn: Callable[[Sequence], Sequence] | None = None,
    frame: Sequence | None = None,
    truncate: int = 1,
    atol: float = 1e-9,
) -> LookaheadResult:
    """Run every applicable check and return a single result carrying the blind spots.

    Pass ``source`` for the static AST pass (incl. the D27 universe check unless
    ``check_universe=False``), ``label_horizon`` for the degenerate-horizon check, and
    ``feature_fn`` + ``frame`` for the truncation probe. Only the checks whose inputs are
    supplied run, and ``checks_run`` records exactly which those were — so a clean result
    never overstates its coverage.
    """
    findings: list[LeakFinding] = []
    checks: list[str] = []

    if source is not None:
        findings.extend(lint_source(source, check_universe=check_universe))
        checks.append("ast_static")
        if check_universe:
            checks.append("universe_d27")
    if label_horizon is not None:
        checks.append("label_horizon")
        f = check_label_horizon(label_horizon)
        if f is not None:
            findings.append(f)
    if feature_fn is not None and frame is not None:
        checks.append("truncation_probe")
        findings.extend(truncation_probe(feature_fn, frame, truncate=truncate, atol=atol))

    return LookaheadResult(
        findings=tuple(findings),
        checks_run=tuple(checks),
        blind_spots=BLIND_SPOTS,
    )


__all__ = [
    "FUTURE_SHIFT",
    "FUTURE_INDEX",
    "UNBOUNDED_AGGREGATE",
    "DEGENERATE_LABEL_HORIZON",
    "UNIVERSE_LOOKAHEAD",
    "TRUNCATION_DIVERGENCE",
    "BLIND_SPOTS",
    "LeakFinding",
    "LookaheadResult",
    "lint_source",
    "truncation_probe",
    "check_label_horizon",
    "analyze",
]
