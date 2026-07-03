"""Import allowlist + minimal ``__builtins__`` (§8.3).

Important framing: this layer is **lint-grade UX, not the security boundary**.
The real containment is the process boundary (env-scrubbed subprocess, no
parent-memory references, RLIMIT, and — on Linux hosted — the OS layer). A
determined `getattr`/`__subclasses__` chain can still reach module objects from
inside the child; what makes that worthless for the crown jewels is that the
child's environment is empty (no secrets) and, on the hosted deployment, the
network namespace is off and the rootfs read-only. So we keep ``getattr`` and
friends — strategies legitimately use them — and rely on the boundary.

What this module *does* give you: fast, precise "you can't import requests"
errors before wasting a run, and removal of the obvious footguns (``open``,
``eval``, ``exec``, ``compile``, ``__import__``) from the strategy's builtins.
"""

from __future__ import annotations

import builtins as _builtins
from collections.abc import Sequence
from typing import Any

# Modules a strategy may import (top-level names). Mirrors §8.3: stdlib-safe +
# tabular/classic-ML. Submodules are allowed when their top-level is (numpy.linalg
# ok; os.path denied). pytorch/tensorflow/river are cut from v1 (D19).
ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        # stdlib-safe
        "math", "statistics", "collections", "dataclasses", "typing",
        "enum", "abc", "functools", "itertools", "operator",
        # the platform itself
        "flint",
        # tabular + classic ML
        "numpy", "pandas", "polars", "scipy", "statsmodels",
        "sklearn", "xgboost", "lightgbm", "pandas_ta", "talib",
    }
)

# Builtins the strategy may see. getattr/hasattr stay (contained by the boundary,
# not removed). open/eval/exec/compile/input/globals/locals/vars/breakpoint are
# absent by omission. __build_class__ and __import__ are required for `class` and
# `import` statements respectively.
_SAFE_BUILTIN_NAMES: frozenset[str] = frozenset(
    {
        "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
        "callable", "chr", "complex", "dict", "divmod", "enumerate", "filter",
        "float", "format", "frozenset", "getattr", "hasattr", "hash", "hex",
        "id", "int", "isinstance", "issubclass", "iter", "len", "list", "map",
        "max", "min", "next", "object", "oct", "ord", "pow", "print", "range",
        "repr", "reversed", "round", "set", "setattr", "slice", "sorted", "str",
        "sum", "tuple", "type", "zip", "super", "property", "staticmethod",
        "classmethod",
        # class/function machinery
        "__build_class__",
        # singletons + common exceptions
        "True", "False", "None", "NotImplemented", "Ellipsis",
        "BaseException", "Exception", "ArithmeticError", "AssertionError",
        "AttributeError", "IndexError", "KeyError", "LookupError", "NameError",
        "NotImplementedError", "OverflowError", "RuntimeError", "StopIteration",
        "TypeError", "ValueError", "ZeroDivisionError", "ImportError",
    }
)


def _guarded_import(
    name: str,
    globals: dict[str, Any] | None = None,
    locals: dict[str, Any] | None = None,
    fromlist: Sequence[str] = (),
    level: int = 0,
) -> Any:
    """A drop-in ``__import__`` that denies anything outside ``ALLOWED_MODULES``."""
    top = name.split(".", 1)[0]
    if level != 0 or top not in ALLOWED_MODULES:
        raise ImportError(f"import of {name!r} is not allowed in the sandbox")
    return _builtins.__import__(name, globals, locals, fromlist, level)


def build_safe_builtins() -> dict[str, Any]:
    """Return the restricted ``__builtins__`` dict handed to strategy code."""
    safe: dict[str, Any] = {
        n: getattr(_builtins, n)
        for n in _SAFE_BUILTIN_NAMES
        if hasattr(_builtins, n)
    }
    safe["__import__"] = _guarded_import
    return safe
