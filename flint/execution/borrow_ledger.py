"""BorrowLedger — Jupiter Perps borrow history + paid-borrow ledger.

D-2.1.b Step 6 (Wave 2). Owns the per-market `BorrowSnapshot` history
plus the running `total_paid` counter and the per-trade payment ledger
that BacktestContext keeps for tearsheet attribution.

The two halves are colocated because both are about the same Jupiter
Perps product: rates come in via `record(snapshot)`, and when a
position is realized the borrow cost is computed from
`cumulative_at(market, ts)` and pushed into the payment ledger via
`record_payment(...)` for downstream attribution.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..models import BorrowSnapshot


class BorrowLedger:
    __slots__ = ("_history", "_payments", "total_paid")

    def __init__(self) -> None:
        self._history: Dict[str, List[BorrowSnapshot]] = {}
        self._payments: List[Dict[str, Any]] = []
        self.total_paid: float = 0.0

    # --- record ------------------------------------------------------------

    def record(self, snapshot: BorrowSnapshot) -> None:
        self._history.setdefault(snapshot.market, []).append(snapshot)

    def record_payment(self, payment: Dict[str, Any]) -> None:
        self._payments.append(payment)

    def add_paid(self, amount: float) -> None:
        self.total_paid += amount

    # --- read --------------------------------------------------------------

    def latest(self, market: str) -> Optional[float]:
        history = self._history.get(market)
        return history[-1].rate_hourly if history else None

    def recent(self, market: str, lookback: int = 24) -> List[Tuple[int, float]]:
        history = self._history.get(market, [])
        sliced = history[-lookback:] if lookback < len(history) else history
        return [(bs.ts, bs.rate_hourly) for bs in sliced]

    def cumulative_at(self, market: str, ts: int) -> Optional[float]:
        """Cumulative-rate value at or before `ts`. Snapshots in
        `_history` are append-ordered by arrival, which matches
        time order in the engine — same scan strategy as the
        pre-extraction code."""
        history = self._history.get(market, [])
        result: Optional[float] = None
        for bs in history:
            if bs.ts <= ts:
                result = bs.cumulative_rate
            else:
                break
        return result

    # --- raw access for legacy aliases -----------------------------------

    @property
    def history(self) -> Dict[str, List[BorrowSnapshot]]:
        return self._history

    @property
    def payments(self) -> List[Dict[str, Any]]:
        return self._payments
