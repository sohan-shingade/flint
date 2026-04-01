"""MultiVenueLiveContext — wraps multiple venue contexts for cross-venue strategies.

Routes orders by venue parameter, aggregates positions and equity across venues,
supports paired leg submission with timeout and optional auto-unwind.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Dict, List, Optional, Tuple

from ..models import (
    AccountState, Candle, Fill, Order, OrderLeg, LegGroup, LegGroupResult,
    OrderState, OrderType, PositionInfo, Side,
)
from .context import ExecutionContext
from .live_base import LiveExecutionContext

logger = logging.getLogger("flint.multi_venue")


class MultiVenueLiveContext(ExecutionContext):
    """Wraps multiple LiveExecutionContext instances for cross-venue trading.

    Routes orders to the correct venue based on the venue parameter.
    Aggregates positions and equity across all venues.
    """

    def __init__(
        self,
        contexts: Dict[str, LiveExecutionContext],
        primary_venue: str = "",
        tick_mode: str = "primary",
        leg_timeout_s: float = 30.0,
        auto_unwind_failed_legs: bool = False,
    ):
        self._contexts = contexts
        self._primary_venue = primary_venue or next(iter(contexts))
        self._tick_mode = tick_mode
        self._leg_timeout_s = leg_timeout_s
        self._auto_unwind = auto_unwind_failed_legs
        self._leg_groups: Dict[str, LegGroup] = {}
        self._candle_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._equity_monitor = None

        logger.info("MultiVenueLiveContext: %d venues, primary=%s, tick_mode=%s",
                     len(contexts), self._primary_venue, tick_mode)

    # --- ExecutionContext properties ---

    @property
    def account(self) -> AccountState:
        total_equity = 0.0
        total_cash = 0.0
        total_unrealized = 0.0
        for ctx in self._contexts.values():
            acct = ctx.account
            total_equity += acct.equity
            total_cash += acct.cash
            total_unrealized += acct.unrealized_pnl
        return AccountState(equity=total_equity, cash=total_cash, unrealized_pnl=total_unrealized)

    @property
    def positions(self) -> List[PositionInfo]:
        all_positions = []
        for ctx in self._contexts.values():
            all_positions.extend(ctx.positions)
        return all_positions

    @property
    def pending_orders(self) -> List[Order]:
        all_orders = []
        for ctx in self._contexts.values():
            all_orders.extend(ctx.pending_orders)
        return all_orders

    @property
    def current_candle(self) -> Optional[Candle]:
        primary = self._contexts.get(self._primary_venue)
        return primary.current_candle if primary else None

    @property
    def timestamp(self) -> int:
        return int(time.time())

    # --- Per-venue views ---

    def venue_account(self, venue: str) -> AccountState:
        ctx = self._contexts.get(venue)
        if ctx is None:
            return AccountState(equity=0, cash=0)
        return ctx.account

    def total_exposure(self, market: str) -> float:
        net = 0.0
        for pos in self.positions:
            if pos.market == market:
                if pos.side == Side.LONG:
                    net += pos.size
                else:
                    net -= pos.size
        return net

    def per_venue_pnl(self) -> Dict[str, float]:
        result = {}
        for venue, ctx in self._contexts.items():
            result[venue] = ctx.account.unrealized_pnl
        return result

    # --- Order routing ---

    def _resolve_venue(self, venue: str) -> str:
        if venue == "default" or not venue:
            return self._primary_venue
        return venue

    def market_order(self, market, side, size, reduce_only=False, tag="", venue="default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx is None:
            logger.error("Unknown venue: %s", target)
            return ""
        return ctx.market_order(market, side, size, reduce_only=reduce_only, tag=tag, venue=target)

    def limit_order(self, market, side, size, price, reduce_only=False, tag="", venue="default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx is None:
            logger.error("Unknown venue: %s", target)
            return ""
        return ctx.limit_order(market, side, size, price, reduce_only=reduce_only, tag=tag, venue=target)

    def stop_order(self, market, side, size, trigger_price, tag="", venue="default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx is None:
            logger.error("Unknown venue: %s", target)
            return ""
        return ctx.stop_order(market, side, size, trigger_price, tag=tag, venue=target)

    def take_profit_order(self, market, side, size, trigger_price, tag="", venue="default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx is None:
            logger.error("Unknown venue: %s", target)
            return ""
        return ctx.take_profit_order(market, side, size, trigger_price, tag=tag, venue=target)

    def cancel(self, order_id):
        for ctx in self._contexts.values():
            if ctx.cancel(order_id):
                return True
        return False

    def cancel_all(self, market=None):
        total = 0
        for ctx in self._contexts.values():
            total += ctx.cancel_all(market)
        return total

    def log(self, message: str) -> None:
        logger.info("[multi-venue] %s", message)
