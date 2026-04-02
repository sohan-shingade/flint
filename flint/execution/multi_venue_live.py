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

    def estimate_cost(self, market: str, size: float, venue: str = "default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx:
            return ctx.estimate_cost(market, size, venue=target)
        return None

    async def submit_leg_group(self, legs: List[OrderLeg]) -> LegGroupResult:
        """Submit a group of paired orders across venues.

        Places all legs in parallel, waits for fills up to leg_timeout_s.
        If some legs don't fill, cancels them. If auto_unwind is True,
        also closes the filled legs.
        """
        group_id = str(uuid.uuid4())[:8]
        group = LegGroup(
            group_id=group_id, legs=legs,
            created_at=int(time.time()), timeout_s=self._leg_timeout_s,
        )
        self._leg_groups[group_id] = group

        # Place all legs
        for leg in legs:
            target = self._resolve_venue(leg.venue)
            ctx = self._contexts.get(target)
            if ctx is None:
                continue
            oid = ctx.market_order(leg.market, leg.side, leg.size, venue=target)
            leg.order_id = oid

        # Submit in parallel
        submit_tasks = []
        for venue, ctx in self._contexts.items():
            if any(leg.venue == venue for leg in legs):
                submit_tasks.append(ctx.submit_pending_orders())
        all_fills = []
        results = await asyncio.gather(*submit_tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                all_fills.extend(r)

        # Determine which legs filled
        filled_order_ids = {f.order_id for f in all_fills}
        filled_legs = [leg.order_id for leg in legs if leg.order_id in filled_order_ids]
        failed_legs = [leg.order_id for leg in legs if leg.order_id not in filled_order_ids]

        # If some legs didn't fill, wait up to timeout then cancel
        if failed_legs:
            await asyncio.sleep(min(self._leg_timeout_s, 0.5))
            for leg in legs:
                if leg.order_id in failed_legs:
                    target = self._resolve_venue(leg.venue)
                    ctx = self._contexts.get(target)
                    if ctx:
                        ctx.cancel(leg.order_id)

        # Auto-unwind filled legs if enabled and some failed
        unwind_ids = []
        if self._auto_unwind and failed_legs and filled_legs:
            for leg in legs:
                if leg.order_id in filled_legs:
                    opposite = Side.SHORT if leg.side == Side.LONG else Side.LONG
                    target = self._resolve_venue(leg.venue)
                    ctx = self._contexts.get(target)
                    if ctx:
                        unwind_id = ctx.market_order(leg.market, opposite, leg.size, venue=target)
                        unwind_ids.append(unwind_id)
            for venue, ctx in self._contexts.items():
                if ctx.pending_orders:
                    await ctx.submit_pending_orders()

        # Determine status
        if not failed_legs:
            status = "filled"
        elif unwind_ids:
            status = "unwound"
        elif filled_legs:
            status = "partial"
        else:
            status = "failed"

        group.status = status
        return LegGroupResult(
            group_id=group_id, status=status,
            filled_legs=filled_legs, failed_legs=failed_legs,
            unwind_order_ids=unwind_ids,
        )

    # --- Tick routing ---

    def _on_ws_candle(self, candle: Candle) -> None:
        """Called by WebSocket feeds when a candle closes."""
        if self._tick_mode == "primary":
            if candle.venue == self._primary_venue:
                self._candle_queue.put_nowait(candle)
        else:
            self._candle_queue.put_nowait(candle)

    # --- Run lifecycle ---

    async def run(self, strategy, market: str, feeds=None, fetch_candle=None) -> None:
        """Run the multi-venue tick loop."""
        self._running = True
        self._candle_queue = asyncio.Queue()

        await asyncio.gather(*[ctx.connect() for ctx in self._contexts.values()])

        feed_tasks = []
        if feeds:
            for feed in feeds:
                feed_tasks.append(asyncio.create_task(feed.start()))

        poll_tasks = [asyncio.create_task(ctx._poll_orders_loop())
                      for ctx in self._contexts.values()]

        monitor_task = None
        if self._equity_monitor:
            monitor_task = asyncio.create_task(self._equity_monitor.run())

        try:
            while self._running:
                try:
                    candle = await asyncio.wait_for(
                        self._candle_queue.get(), timeout=120,
                    )
                    primary = self._contexts.get(self._primary_venue)
                    if primary:
                        primary._current_candle = candle

                    try:
                        strategy.on_candle(candle, [], ctx=self)
                    except Exception as e:
                        logger.error("Strategy error: %s", e)

                    submit_tasks = []
                    for ctx in self._contexts.values():
                        if ctx.pending_orders:
                            submit_tasks.append(ctx.submit_pending_orders())
                    if submit_tasks:
                        await asyncio.gather(*submit_tasks)

                except asyncio.TimeoutError:
                    logger.debug("No candle within timeout")
        finally:
            if monitor_task:
                monitor_task.cancel()
            for task in feed_tasks + poll_tasks:
                task.cancel()
            for task in feed_tasks + poll_tasks + ([monitor_task] if monitor_task else []):
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            await asyncio.gather(*[ctx.disconnect() for ctx in self._contexts.values()])

    async def stop(self) -> None:
        self._running = False

    # --- Position helpers ---

    def close_position(self, market, venue="default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx is None:
            return None
        return ctx.close_position(market, target)

    def position(self, market, venue="default"):
        target = self._resolve_venue(venue)
        ctx = self._contexts.get(target)
        if ctx is None:
            return None
        return ctx.position(market, target)

    # --- Market data helpers ---

    def get_candles(self, market, lookback=50):
        primary = self._contexts.get(self._primary_venue)
        if primary:
            return primary.get_candles(market, lookback)
        return []

    def get_oracle_price(self, market=None):
        primary = self._contexts.get(self._primary_venue)
        if primary:
            return primary.get_oracle_price(market)
        return None

    def get_funding_rate(self, market=None):
        primary = self._contexts.get(self._primary_venue)
        if primary:
            return primary.get_funding_rate(market)
        return None

    def get_funding_by_venue(self, market=None, lookback=24):
        result = {}
        for venue, ctx in self._contexts.items():
            try:
                rates = ctx.get_funding_rates(market, lookback)
                if rates:
                    result[venue] = rates
            except Exception:
                pass
        return result
