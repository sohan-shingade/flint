"""``flint.engine.nautilus`` — the Nautilus-backed simulation substrate (§A3, D29).

This package init imports **nothing eagerly**. Pure submodules (``timeconv``) must
be importable in an environment without the ``nautilus`` extra — e.g.
``from flint.engine.nautilus import timeconv`` triggers this ``__init__``, and an
eager ``from .engine import NautilusEngine`` here would drag ``nautilus_trader``
(via :mod:`._compat`) into every such import and break test collection in
extras-less environments. The public names are therefore resolved lazily via
module-level ``__getattr__`` (PEP 562): the ``nautilus_trader`` wheel (and its
exact-pin assert in ``_compat``) is paid only when :class:`NautilusEngine` or
:class:`FlintFundingRate` is actually touched — which
:func:`flint.engine.select.engine_for` does only inside its ``"nautilus"`` branch,
so candle-only users never pay the import cost.
"""

from __future__ import annotations

from typing import Any

__all__ = ["NautilusEngine", "FlintFundingRate"]

_LAZY = {
    "NautilusEngine": ".engine",
    "FlintFundingRate": ".dataconv",
}


def __getattr__(name: str) -> Any:
    """Resolve the public surface lazily (PEP 562) — see the module docstring."""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(target, __name__), name)
