"""``flint.engine.nautilus`` — the Nautilus-backed simulation substrate (§A3, D29).

Importing this package pulls in ``nautilus_trader`` (via :mod:`._compat`, which
asserts the exact pin). That is intentional and **lazy**: nothing in
``flint/engine`` imports this package at module load — only
:func:`flint.engine.select.engine_for` imports it, inside the ``"nautilus"``
branch, so candle-only users never pay the wheel's import cost.
"""

from __future__ import annotations

from .dataconv import FlintFundingRate
from .engine import NautilusEngine

__all__ = ["NautilusEngine", "FlintFundingRate"]
