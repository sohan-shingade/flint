"""Internal mutable position tracker shared by BacktestContext + PaperContext.

Originally lived inside `backtest_context.py`. Extracted so PaperContext
(D-2.1.d) can use the exact same `_Position` shape — same field names,
same `update_pnl` semantics — and `PositionManager.update_pnl_for_market`
behaves identically across both contexts. Keeping it private (leading
underscore) signals "tight integration with the managers; not a public
strategy-facing type" — strategies see `PositionInfo` (in `models.py`).
"""
from __future__ import annotations

from ..models import PositionInfo, Side


class _Position:
    """Internal mutable position tracker.

    Single-position state. Mutated in-place by the fill, funding, and
    borrow paths in BacktestContext / PaperContext. Strategies never
    see this type — they see the immutable `PositionInfo` returned by
    `to_info()`.
    """

    __slots__ = ("market", "venue", "side", "size", "entry_price", "entry_ts",
                 "unrealized_pnl", "funding_paid", "borrow_cumulative_at_entry",
                 "mark_price", "entry_order_id")

    def __init__(self, market: str, side: Side, size: float,
                 entry_price: float, entry_ts: int, venue: str = "default",
                 entry_order_id: str = ""):
        self.market = market
        self.venue = venue
        self.side = side
        self.size = size  # always positive
        self.entry_price = entry_price
        self.entry_ts = entry_ts
        self.unrealized_pnl = 0.0
        self.funding_paid = 0.0
        self.borrow_cumulative_at_entry = 0.0
        # Used by paper trading (live mark price between candles); the
        # backtest path computes PnL on demand from the current candle.
        self.mark_price = entry_price
        # Order ID of the fill that opened this position. Used by
        # `SharedCapitalPortfolioEngine` to attribute closed-trade PnL
        # to the strategy that opened the leg, not just the one that
        # closed it (D-6.1 attribution-by-trade refinement).
        self.entry_order_id = entry_order_id

    def update_pnl(self, current_price: float) -> None:
        if self.side == Side.LONG:
            self.unrealized_pnl = (current_price - self.entry_price) * self.size
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.size
        self.mark_price = current_price

    def to_info(self) -> PositionInfo:
        return PositionInfo(
            market=self.market,
            side=self.side,
            size=self.size,
            entry_price=self.entry_price,
            unrealized_pnl=self.unrealized_pnl,
            entry_ts=self.entry_ts,
            venue=self.venue,
        )

    def to_dict(self) -> dict:
        """Legacy paper-trading dict shape — used by session_store
        persistence + the live monitor UI snapshot. Mirrors what
        PaperBroker used to store directly."""
        return {
            "market": self.market,
            "venue": self.venue,
            "side": self.side.value if hasattr(self.side, "value") else self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "entry_ts": self.entry_ts,
            "unrealized_pnl": self.unrealized_pnl,
            "mark_price": self.mark_price,
            "funding_paid": self.funding_paid,
        }
