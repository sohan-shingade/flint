"""LiveExecutionContext — abstract base class for all live venue implementations.

Sits between ExecutionContext ABC and venue-specific implementations
(LiveDriftContext, LiveHyperliquidContext, etc.).

Provides:
- Order routing through risk guards and OrderTracker
- Timer-based strategy tick loop
- Position state management with periodic venue reconciliation
- Store persistence for audit trail
"""
from __future__ import annotations

import abc
import asyncio
import logging
import time
import uuid
from typing import Dict, List, Optional, Tuple

from ..models import (
    AccountState, Candle, Fill, Order, OrderState, OrderType,
    PositionInfo,
)
from .borrow_ledger import BorrowLedger
from .cash_manager import CashManager
from .context import ExecutionContext
from .fill_recorder import FillRecorder
from .funding_ledger import FundingLedger
from .market_data_feed import MarketDataFeed
from .order_queue import OrderQueue
from .order_tracker import OrderTracker
from .position_manager import PositionManager
from ..risk.guards import RiskManager

logger = logging.getLogger("flint.live")


class LiveExecutionContext(ExecutionContext, abc.ABC):
    """Base class for live venue execution contexts.

    Subclasses implement the 7 abstract methods for venue-specific operations.
    This base handles order lifecycle, risk checks, position tracking, and persistence.
    """

    def __init__(
        self,
        venue: str = "default",
        initial_capital: float = 0.0,
        risk_manager: Optional[RiskManager] = None,
        store=None,
        session_id: str = "",
        max_retries: int = 3,
        on_failure: str = "drop",
        max_orders_per_sec: int = 10,
        max_concurrent_tx: int = 2,
        tick_interval_s: int = 60,
        position_sync_interval: int = 5,
        limit_order_timeout_bars: int = 10,
        tick_mode: str = "on_candle_close",
        tick_markets: Optional[List[str]] = None,
        dry_run: bool = False,
        notification_manager=None,
        tx_cost_model=None,
    ):
        self._venue = venue
        self._initial_capital = initial_capital
        self._cash = initial_capital
        self._risk_manager = risk_manager
        self._store = store
        self._session_id = session_id or str(uuid.uuid4())[:8]
        self._tx_cost_model = tx_cost_model
        self._tick_interval_s = tick_interval_s
        self._position_sync_interval = position_sync_interval
        self._limit_order_timeout_bars = limit_order_timeout_bars
        self._tick_mode = tick_mode
        self._tick_markets = tick_markets or []
        self._candle_queue: asyncio.Queue = asyncio.Queue()
        self._pyth_feed = None
        self._dry_run = dry_run
        self._notification_manager = notification_manager
        self._equity_monitor = None

        self._positions_cache: Dict[Tuple[str, str], PositionInfo] = {}
        self._current_candle: Optional[Candle] = None
        self._fills: List[Fill] = []
        self._order_counter = 0
        self._tick_count = 0
        self._running = False

        # D-2.1.c structural prep — compose the same 7 managers as
        # `BacktestContext` and `PaperContext`. They're empty placeholders
        # in this slice; the next slice (after testnet credentials are
        # available) wires venue events into them so live shares the
        # same state model as paper. Until then `_positions_cache`,
        # `_cash`, and `_fills` remain the source of truth — these
        # managers don't yet drive any behavior.
        self._pm = PositionManager()
        self._cm = CashManager(initial_capital, allocator=None)
        self._fr = FillRecorder()
        self._oq = OrderQueue()
        self._fl = FundingLedger()
        self._bl = BorrowLedger()
        self._mdf = MarketDataFeed()

        self._tracker = OrderTracker(
            max_retries=max_retries,
            on_failure=on_failure,
            max_orders_per_sec=max_orders_per_sec,
            max_concurrent_tx=max_concurrent_tx,
            on_fill=self._handle_fill,
            on_fail=self._handle_fail,
        )

        # D-4.3-websocket slice 2: optional manager. When set, fills
        # broadcast to `live:{session_id}` so subscribers see trades
        # in real time. Wired by the live runner (when implemented)
        # or by external test harnesses; None elsewhere.
        self.ws_manager = None

    # --- Abstract methods (venue subclasses implement) ---

    @abc.abstractmethod
    async def _connect(self) -> None: ...

    @abc.abstractmethod
    async def _disconnect(self) -> None: ...

    @abc.abstractmethod
    async def _place_order(self, order: Order) -> Tuple[str, Optional[int]]: ...

    @abc.abstractmethod
    async def _cancel_order(self, venue_order_id: int) -> bool: ...

    @abc.abstractmethod
    async def _fetch_positions(self) -> List[PositionInfo]: ...

    @abc.abstractmethod
    async def _fetch_balance(self) -> float: ...

    @abc.abstractmethod
    async def _poll_order_status(self, venue_order_id: int) -> OrderState: ...

    # --- Lifecycle ---

    async def connect(self) -> None:
        await self._connect()
        positions = await self._fetch_positions()
        self._reconcile_positions(positions)
        self._cash = await self._fetch_balance()
        logger.info("Connected to %s — %d positions, balance=%.2f",
                     self._venue, len(self._positions_cache), self._cash)

    async def disconnect(self) -> None:
        self._running = False
        await self._disconnect()
        logger.info("Disconnected from %s", self._venue)

    # --- Tick loop ---

    async def run(self, strategy, market: str, feeds=None, fetch_candle=None) -> None:
        """Run the strategy tick loop.

        Args:
            strategy: Strategy instance with on_candle(ctx) method.
            market: Primary market symbol (e.g. "SOL-PERP").
            feeds: Optional list of WebSocketFeed instances to start.
            fetch_candle: Optional async callable() -> Candle for timer mode fallback.
        """
        self._running = True
        self._tick_count = 0
        self._candle_queue = asyncio.Queue()

        if not self._tick_markets:
            self._tick_markets = [market]

        logger.info("Starting %s tick loop (market=%s, tick_markets=%s)",
                     self._tick_mode, market, self._tick_markets)

        feed_tasks = []
        if feeds:
            for feed in feeds:
                feed_tasks.append(asyncio.create_task(feed.start()))

        poll_task = asyncio.create_task(self._poll_orders_loop())

        monitor_task = None
        if self._equity_monitor:
            monitor_task = asyncio.create_task(self._equity_monitor.run())

        try:
            if self._tick_mode == "on_candle_close":
                await self._run_event_driven(strategy, market, fetch_candle)
            else:
                await self._run_timer(strategy, market, fetch_candle)
        finally:
            if monitor_task:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass
            for task in feed_tasks:
                task.cancel()
            poll_task.cancel()
            for task in feed_tasks + [poll_task]:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            logger.info("Tick loop stopped after %d ticks", self._tick_count)

    async def _run_event_driven(self, strategy, market: str, fetch_candle=None) -> None:
        """Event-driven loop: ticks on candle close events from WebSocket feeds."""
        while self._running:
            try:
                candle = await asyncio.wait_for(
                    self._candle_queue.get(),
                    timeout=self._tick_interval_s * 2,
                )
                self._current_candle = candle
                self._tick_count += 1
                await self._tick(strategy, market)
            except asyncio.TimeoutError:
                logger.debug("No WS candle within timeout, falling back to REST")
                self._tick_count += 1
                await self._tick(strategy, market, fetch_candle)

    async def _run_timer(self, strategy, market: str, fetch_candle=None) -> None:
        """Timer-based loop: ticks at fixed intervals (original behavior)."""
        while self._running:
            self._tick_count += 1
            try:
                await self._tick(strategy, market, fetch_candle)
            except Exception as e:
                logger.error("Tick %d failed: %s", self._tick_count, e)
            await asyncio.sleep(self._tick_interval_s)

    def _on_ws_candle(self, candle) -> None:
        """Called by CandleAggregator when a bar closes. Enqueues if market is in tick_markets."""
        venue_market = f"{candle.venue}:{candle.market}"
        if candle.market in self._tick_markets or venue_market in self._tick_markets:
            self._current_candle = candle
            self._candle_queue.put_nowait(candle)

    async def _tick(self, strategy, market: str, fetch_candle=None) -> None:
        """Execute one tick of the strategy loop."""
        if fetch_candle:
            candle = await fetch_candle()
        else:
            candle = self._fetch_candle_from_store(market)
        if candle is None:
            logger.debug("Tick %d: no candle available", self._tick_count)
            return
        self._current_candle = candle

        self._tracker.check_timeouts(
            submission_timeout_s=30,
            current_bar=self._tick_count,
            limit_timeout_bars=self._limit_order_timeout_bars,
        )

        try:
            strategy.on_candle(self)
        except Exception as e:
            logger.error("Strategy error on tick %d: %s", self._tick_count, e)

        await self.submit_pending_orders()

        if self._tick_count % self._position_sync_interval == 0:
            try:
                positions = await self._fetch_positions()
                self._reconcile_positions(positions)
                self._cash = await self._fetch_balance()
            except Exception as e:
                logger.error("Position sync failed: %s", e)

        self._persist_equity()

    def _fetch_candle_from_store(self, market: str):
        """Fetch the most recent candle from FlintStore."""
        if not self._store:
            return None
        try:
            import time as _time
            now = int(_time.time())
            candles = self._store.query_candles(market, self._tick_interval_s, start_ts=now - self._tick_interval_s * 3)
            return candles[-1] if candles else None
        except Exception:
            return None

    async def stop(self) -> None:
        """Stop the tick loop gracefully."""
        self._running = False
        logger.info("Stop requested, will halt after current tick")

    # --- Order polling loop ---

    async def _poll_orders_loop(self, poll_interval_s: float = 2.0) -> None:
        """Background loop that polls venue for order status updates."""
        logger.info("Order polling loop started (interval=%.1fs)", poll_interval_s)
        while self._running:
            try:
                await self._poll_active_orders()
            except Exception as e:
                logger.error("Order polling error: %s", e)
            await asyncio.sleep(poll_interval_s)

    async def _poll_active_orders(self) -> None:
        """Poll venue for status of all in-flight orders."""
        for tracked in self._tracker.get_submitted() + self._tracker.get_confirmed():
            if tracked.venue_order_id is None:
                continue
            try:
                new_state = await self._poll_order_status(tracked.venue_order_id)
                if new_state == tracked.state:
                    continue
                if new_state == OrderState.CONFIRMED and tracked.state == OrderState.SUBMITTED:
                    self._tracker.mark_confirmed(tracked.flint_order_id, venue_order_id=tracked.venue_order_id, bar_count=self._tick_count)
                elif new_state == OrderState.FILLED:
                    fill = Fill(
                        market=tracked.order.market, side=tracked.order.side,
                        price=tracked.order.price or 0,
                        size=tracked.order.size - tracked.filled_size,
                        fee=0, ts=int(time.time()),
                        order_id=tracked.flint_order_id,
                        tx_sig=tracked.tx_sig or "", venue=self._venue,
                    )
                    self._tracker.mark_filled(tracked.flint_order_id, fill)
                elif new_state == OrderState.CANCELLED:
                    self._tracker.mark_cancelled(tracked.flint_order_id)
                self._persist_order(tracked)
            except Exception as e:
                logger.error("Poll failed for order %s: %s", tracked.flint_order_id, e)

    # --- ExecutionContext interface ---

    @property
    def account(self) -> AccountState:
        unrealized = sum(p.unrealized_pnl for p in self._positions_cache.values())
        return AccountState(
            equity=self._cash + unrealized,
            cash=self._cash,
            unrealized_pnl=unrealized,
        )

    @property
    def positions(self) -> List[PositionInfo]:
        return list(self._positions_cache.values())

    @property
    def pending_orders(self) -> List[Order]:
        return [t.order for t in self._tracker.active_orders.values()]

    @property
    def current_candle(self) -> Optional[Candle]:
        return self._current_candle

    @property
    def timestamp(self) -> int:
        return int(time.time())

    def _next_id(self) -> str:
        self._order_counter += 1
        return f"live-{self._session_id}-{self._order_counter}"

    def market_order(self, market, side, size, reduce_only=False, tag="", venue="default"):
        order = Order(
            market=market, side=side, order_type=OrderType.MARKET,
            size=size, order_id=self._next_id(), ts=self.timestamp,
            venue=venue if venue != "default" else self._venue,
        )
        return self._submit_order(order)

    def limit_order(self, market, side, size, price, reduce_only=False, tag="", venue="default"):
        order = Order(
            market=market, side=side, order_type=OrderType.LIMIT,
            size=size, price=price, order_id=self._next_id(), ts=self.timestamp,
            venue=venue if venue != "default" else self._venue,
        )
        return self._submit_order(order)

    def stop_order(self, market, side, size, trigger_price, tag="", venue="default"):
        order = Order(
            market=market, side=side, order_type=OrderType.STOP_LOSS,
            size=size, price=trigger_price, order_id=self._next_id(), ts=self.timestamp,
            venue=venue if venue != "default" else self._venue,
        )
        return self._submit_order(order)

    def take_profit_order(self, market, side, size, trigger_price, tag="", venue="default"):
        order = Order(
            market=market, side=side, order_type=OrderType.TAKE_PROFIT,
            size=size, price=trigger_price, order_id=self._next_id(), ts=self.timestamp,
            venue=venue if venue != "default" else self._venue,
        )
        return self._submit_order(order)

    def cancel(self, order_id):
        tracked = self._tracker.get(order_id)
        if not tracked or tracked.is_terminal:
            return False
        self._tracker.mark_cancelled(order_id)
        return True

    def cancel_all(self, market=None):
        count = 0
        for oid in list(self._tracker.active_orders.keys()):
            tracked = self._tracker.active_orders[oid]
            if market and tracked.order.market != market:
                continue
            self._tracker.mark_cancelled(oid)
            count += 1
        return count

    def modify_order(self, order_id: str, new_size: Optional[float] = None, new_price: Optional[float] = None) -> str:
        """Modify an order by cancelling and replacing it.

        Returns the new order_id, or empty string if the original order
        couldn't be cancelled (already filled/terminal).
        """
        tracked = self._tracker.get(order_id)
        if not tracked or tracked.is_terminal:
            logger.warning("Cannot modify order %s: not found or terminal", order_id)
            return ""

        # Capture original order params before cancelling
        original = tracked.order
        size = new_size if new_size is not None else original.size
        price = new_price if new_price is not None else original.price

        # Cancel the original
        self._tracker.mark_cancelled(order_id)

        # Place replacement
        if original.order_type == OrderType.MARKET:
            return self.market_order(original.market, original.side, size, venue=original.venue)
        elif original.order_type == OrderType.LIMIT:
            return self.limit_order(original.market, original.side, size, price, venue=original.venue)
        elif original.order_type == OrderType.STOP_LOSS:
            return self.stop_order(original.market, original.side, size, price, venue=original.venue)
        elif original.order_type == OrderType.TAKE_PROFIT:
            return self.take_profit_order(original.market, original.side, size, price, venue=original.venue)
        return ""

    # --- Internal order routing ---

    def _submit_order(self, order: Order) -> str:
        if self._risk_manager:
            result = self._risk_manager.evaluate(
                order, self.account, self.positions,
            )
            if result is None:
                logger.info("Order %s rejected by risk guards", order.order_id)
                self._notify("risk_rejection", f"Order rejected on {order.market}: {order.side.value} {order.size:.4f}")
                return ""
            order = result
        self._tracker.submit(order)
        return order.order_id

    # --- Submission loop ---

    async def submit_pending_orders(self) -> List[Fill]:
        pending = self._tracker.get_pending()
        fills = []

        for tracked in pending:
            if not self._tracker.can_submit():
                logger.debug("Rate limit hit, deferring remaining orders")
                break

            if self._dry_run:
                price = (self._current_candle.close if self._current_candle
                         else tracked.order.price or 0)
                fill = Fill(
                    market=tracked.order.market,
                    side=tracked.order.side,
                    price=price,
                    size=tracked.order.size,
                    fee=price * tracked.order.size * 0.0005,
                    ts=int(time.time()),
                    order_id=tracked.flint_order_id,
                    tx_sig="DRY_RUN",
                    venue=self._venue,
                )
                self._tracker.mark_submitted(tracked.flint_order_id, tx_sig="DRY_RUN")
                self._tracker.mark_confirmed(tracked.flint_order_id, venue_order_id=0)
                self._tracker.mark_filled(tracked.flint_order_id, fill)
                self._persist_order(tracked)
                logger.info("[DRY RUN] %s %s %.4f %s @ %.2f",
                           tracked.order.side.value, tracked.order.market,
                           tracked.order.size, tracked.order.order_type.value, price)
                fills.append(fill)
            else:
                try:
                    tx_sig, venue_order_id = await self._place_order(tracked.order)
                    self._tracker.mark_submitted(tracked.flint_order_id, tx_sig=tx_sig)
                    if venue_order_id is not None:
                        self._tracker.mark_confirmed(tracked.flint_order_id, venue_order_id=venue_order_id)
                    self._persist_order(tracked)
                except Exception as e:
                    logger.error("Order %s submission failed: %s", tracked.flint_order_id, e)
                    if not self._tracker.increment_retry(tracked.flint_order_id):
                        pass

        return fills

    # --- Callbacks ---

    def _handle_fill(self, order_id: str, fill: Fill) -> None:
        self._fills.append(fill)
        self._update_position_from_fill(fill)
        self._persist_fill(fill)
        logger.info("Fill: %s %s %.4f @ %.2f (fee=%.4f)",
                     fill.side.value, fill.market, fill.size, fill.price, fill.fee)
        self._notify("fill", f"Fill: {fill.side.value} {fill.market} {fill.size:.4f} @ {fill.price:.2f}")

        # D-4.3-websocket slice 2b: broadcast to `live:{session_id}`
        # subscribers. Fire-and-forget: failure to broadcast must not
        # break order processing, so we route through ensure_future
        # and swallow exceptions.
        if self.ws_manager is not None and self._session_id:
            try:
                import asyncio as _asyncio
                _asyncio.ensure_future(self.ws_manager.broadcast(
                    f"live:{self._session_id}",
                    {
                        "type": "fill",
                        "order_id": order_id,
                        "market": fill.market,
                        "venue": fill.venue or self._venue,
                        "side": fill.side.value,
                        "price": fill.price,
                        "size": fill.size,
                        "fee": fill.fee,
                        "ts": fill.ts,
                    },
                ))
            except Exception as e:
                logger.debug("WS fill broadcast skipped: %s", e)

    def _handle_fail(self, order_id: str, reason: str) -> None:
        logger.warning("Order %s failed: %s (policy=%s)",
                       order_id, reason, self._tracker.on_failure)
        if self._tracker.on_failure == "halt":
            self._running = False
            logger.error("Strategy halted due to order failure")
        self._notify("order_failed", f"Order {order_id} failed: {reason}")

    def _notify(self, event_type: str, message: str, data=None) -> None:
        """Fire a notification if manager is configured."""
        if not self._notification_manager:
            return
        from ..notifications.base import TradingEvent
        import time as _time
        event = TradingEvent(
            event_type=event_type,
            message=message,
            data=data or {},
            timestamp=int(_time.time()),
        )
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._notification_manager.notify(event))
            else:
                loop.run_until_complete(self._notification_manager.notify(event))
        except Exception as e:
            logger.error("Notification failed: %s", e)

    # --- Position management ---

    def _reconcile_positions(self, venue_positions: List[PositionInfo]) -> None:
        new_cache: Dict[Tuple[str, str], PositionInfo] = {}
        for pos in venue_positions:
            key = (pos.venue or self._venue, pos.market)
            new_cache[key] = pos

        for key, local_pos in self._positions_cache.items():
            if key not in new_cache:
                logger.warning("Position %s disappeared from venue", key)
            elif new_cache[key].size != local_pos.size:
                logger.warning("Position %s size mismatch: local=%.4f venue=%.4f",
                             key, local_pos.size, new_cache[key].size)

        self._positions_cache = new_cache

    def _update_position_from_fill(self, fill: Fill) -> None:
        venue = fill.venue or self._venue
        key = (venue, fill.market)
        existing = self._positions_cache.get(key)

        if existing is None:
            self._positions_cache[key] = PositionInfo(
                market=fill.market, side=fill.side, size=fill.size,
                entry_price=fill.price, entry_ts=fill.ts, venue=venue,
            )
        else:
            if existing.side == fill.side:
                total_size = existing.size + fill.size
                avg_price = (
                    (existing.entry_price * existing.size + fill.price * fill.size)
                    / total_size
                )
                self._positions_cache[key] = PositionInfo(
                    market=fill.market, side=existing.side, size=total_size,
                    entry_price=avg_price, entry_ts=existing.entry_ts, venue=venue,
                )
            else:
                if fill.size >= existing.size:
                    del self._positions_cache[key]
                else:
                    self._positions_cache[key] = PositionInfo(
                        market=fill.market, side=existing.side,
                        size=existing.size - fill.size,
                        entry_price=existing.entry_price,
                        entry_ts=existing.entry_ts, venue=venue,
                    )

    # --- Store persistence ---

    def _persist_order(self, tracked) -> None:
        if not self._store:
            return
        try:
            self._store.upsert_live_order(
                order_id=tracked.flint_order_id,
                session_id=self._session_id,
                venue_order_id=tracked.venue_order_id,
                market=tracked.order.market,
                side=tracked.order.side.value,
                order_type=tracked.order.order_type.value,
                size=tracked.order.size,
                price=tracked.order.price,
                state=tracked.state.value,
                retry_count=tracked.retry_count,
                tx_sig=tracked.tx_sig,
                created_at=tracked.created_at,
                updated_at=int(time.time()),
                state_history=tracked.to_state_history_json(),
            )
        except Exception as e:
            logger.error("Failed to persist order %s: %s", tracked.flint_order_id, e)

    def _persist_fill(self, fill: Fill) -> None:
        if not self._store:
            return
        try:
            self._store.insert_live_fill(
                fill_id=str(uuid.uuid4())[:12],
                order_id=fill.order_id,
                session_id=self._session_id,
                market=fill.market,
                side=fill.side.value,
                price=fill.price,
                size=fill.size,
                fee=fill.fee,
                tx_sig=fill.tx_sig,
                venue=fill.venue,
                is_partial=fill.is_partial,
                ts=fill.ts,
            )
        except Exception as e:
            logger.error("Failed to persist fill: %s", e)

    def _persist_equity(self) -> None:
        if not self._store:
            return
        try:
            acct = self.account
            self._store.insert_live_equity(
                self._session_id, int(time.time()),
                acct.equity, acct.cash, acct.unrealized_pnl,
            )
        except Exception as e:
            logger.error("Failed to persist equity: %s", e)

    def log(self, message: str) -> None:
        logger.info("[%s] %s", self._session_id, message)

    def estimate_cost(self, market: str, size: float, venue: str = "default"):
        if self._tx_cost_model is None:
            return None
        price = self._current_candle.close if self._current_candle else 0
        if price <= 0:
            return None
        return self._tx_cost_model.estimate(market, size, price)

    def get_oracle_price(self, market: Optional[str] = None) -> Optional[Tuple[float, int]]:
        """Get latest oracle price. Returns (price, ts) or None."""
        if self._pyth_feed:
            mkt = market or (self._current_candle.market if self._current_candle else None)
            if mkt:
                return self._pyth_feed.get_price(mkt)
        if self._store and market:
            try:
                prices = self._store.query_oracle_prices(market)
                if prices:
                    return (prices[-1].price, prices[-1].ts)
            except Exception:
                pass
        return None
