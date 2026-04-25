"""PositionManager — owns the open + closed position dicts.

D-2.1.b Step 1 (Wave 1) of breaking up `BacktestContext`. This file
extracts only the position-state container, not the fill-application
logic — that stays in `BacktestContext._apply_fill` because it threads
through cash, fees, allocator, and Jupiter borrow accounting (those
splits are Steps 2–7 of the same deferred item).

The manager exposes:
  - `get(key)`, `set(key, position)`, `delete(key)` — single-position ops
  - `__contains__`, `__iter__`, `__len__`, `keys()`, `values()`, `items()`
    — works like a dict so callers using `in self._positions` etc.
    don't need to learn a new API
  - `update_pnl_for_market(market, price)` — bulk update unrealized PnL
  - `record_close(record)` — append to the closed-positions ledger
  - `closed` property — read-only copy for results

`_Position` itself stays in `backtest_context.py` (it's a backtest-
private mutable dataclass). When Step 4 lands and PaperContext starts
sharing this manager, `_Position` will get its own module too.
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, List, Tuple


class PositionManager:
    """Container around the (venue, market) → position dict + closed log."""

    __slots__ = ("_positions", "_closed")

    def __init__(self) -> None:
        # _Position is held by reference; backtest_context.py keeps the type.
        self._positions: Dict[Tuple[str, str], Any] = {}
        self._closed: List[Dict[str, Any]] = []

    # --- single-position ops ----------------------------------------------

    def get(self, key: Tuple[str, str]):
        return self._positions.get(key)

    def set(self, key: Tuple[str, str], position) -> None:
        self._positions[key] = position

    def delete(self, key: Tuple[str, str]) -> None:
        del self._positions[key]

    # --- dict-like surface ------------------------------------------------

    def __contains__(self, key) -> bool:
        return key in self._positions

    def __iter__(self) -> Iterator:
        return iter(self._positions)

    def __len__(self) -> int:
        return len(self._positions)

    def __bool__(self) -> bool:
        return bool(self._positions)

    def keys(self):
        return self._positions.keys()

    def values(self):
        return self._positions.values()

    def items(self):
        return self._positions.items()

    @property
    def positions(self) -> Dict[Tuple[str, str], Any]:
        """Direct dict access — used where the margin engine needs to
        mutate or pass the underlying dict to Rust. Treat as private
        unless your caller is one of those tight integrations."""
        return self._positions

    # --- bulk ops ---------------------------------------------------------

    def update_pnl_for_market(self, market: str, price: float) -> None:
        """Mark every position in `market` to `price` and refresh
        unrealized PnL. Other markets are untouched — callers loop
        over multi-market data themselves."""
        for (_venue, m), pos in self._positions.items():
            if m == market:
                pos.update_pnl(price)

    # --- closed-trade ledger ---------------------------------------------

    def record_close(self, record: Dict[str, Any]) -> None:
        self._closed.append(record)

    @property
    def closed(self) -> List[Dict[str, Any]]:
        return list(self._closed)
