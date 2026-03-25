"""Dynamic strategy loader with AST validation.

This is intentionally unsandboxed. Flint is a local-first, single-user tool —
the user runs their own code on their own machine with full process privileges.
Same model as Jupyter notebooks and Freqtrade.
"""
from __future__ import annotations

import ast
from typing import Any, Dict, List

from .base import Strategy

APPROVED_MODULES = frozenset({
    "flint", "numpy", "math", "statistics", "collections", "dataclasses",
    "typing", "enum", "abc", "functools", "itertools", "operator",
})

# Builtins that must never appear in user strategy code
BLOCKED_BUILTINS = frozenset({
    "exec", "eval", "compile", "__import__", "breakpoint",
    "globals", "locals", "vars", "open",
})

# Attribute accesses that indicate dangerous system-level operations
BLOCKED_ATTRS = frozenset({
    "system", "popen", "spawn", "call", "check_output",
    "getenv", "environ", "listdir", "remove", "rmdir", "unlink",
})


class StrategyLoadError(Exception):
    pass


def validate_strategy_code(code: str) -> Dict[str, Any]:
    """Validate strategy code via AST analysis.

    Returns {"valid": bool, "error": str | None, "warnings": list[str]}
    """
    warnings: List[str] = []

    # 1. Parse — catch syntax errors
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"valid": False, "error": f"Syntax error on line {e.lineno}: {e.msg}", "warnings": []}

    # 2. Find class that references Strategy
    strategy_classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ""
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name == "Strategy":
                    strategy_classes.append(node)

    if not strategy_classes:
        return {"valid": False, "error": "No class subclassing Strategy found", "warnings": []}

    # 3. Check required methods on first Strategy subclass
    cls = strategy_classes[0]
    method_names = set()
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method_names.add(item.name)

    missing = []
    if "on_candle" not in method_names:
        missing.append("on_candle")
    if "reset" not in method_names:
        missing.append("reset")
    if "name" not in method_names:
        missing.append("name")

    if missing:
        return {"valid": False, "error": f"Strategy class missing required methods: {', '.join(missing)}", "warnings": []}

    # 4. Check imports — BLOCK non-approved modules
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in APPROVED_MODULES:
                    return {"valid": False,
                            "error": f"Blocked import: '{alias.name}' — only these modules are "
                                     f"allowed: {', '.join(sorted(APPROVED_MODULES))}",
                            "warnings": warnings}
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top not in APPROVED_MODULES:
                    return {"valid": False,
                            "error": f"Blocked import: '{node.module}' — only these modules are "
                                     f"allowed: {', '.join(sorted(APPROVED_MODULES))}",
                            "warnings": warnings}

    # 5. Check for dangerous builtins and attribute access
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in BLOCKED_BUILTINS:
            return {"valid": False,
                    "error": f"Blocked builtin: '{node.id}' — not allowed in strategy code",
                    "warnings": warnings}
        if isinstance(node, ast.Attribute) and node.attr in BLOCKED_ATTRS:
            return {"valid": False,
                    "error": f"Blocked attribute: '.{node.attr}' — not allowed in strategy code",
                    "warnings": warnings}

    return {"valid": True, "error": None, "warnings": warnings}


def load_user_strategy(code: str, params: Dict[str, Any] = None) -> Strategy:
    """Load a user strategy from source code."""
    result = validate_strategy_code(code)
    if not result["valid"]:
        raise StrategyLoadError(result["error"])

    namespace: Dict[str, Any] = {}
    try:
        exec(code, namespace)  # noqa: S102
    except Exception as e:
        raise StrategyLoadError(f"Error executing strategy code: {e}") from e

    strategy_cls = None
    for obj in namespace.values():
        if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
            strategy_cls = obj
            break

    if strategy_cls is None:
        raise StrategyLoadError("No Strategy subclass found after execution")

    try:
        if params:
            return strategy_cls(**params)
        return strategy_cls()
    except TypeError as e:
        raise StrategyLoadError(f"Error instantiating strategy: {e}") from e
