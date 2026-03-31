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
    PositionInfo, Side,
)
from .context import ExecutionContext
from .order_tracker import OrderTracker
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
    ):
        self._venue = venue
        self._initial_capital = initial_capital
        self._cash = initial_capital
        self._risk_manager = risk_manager
        self._store = store
        self._session_id = session_id or str(uuid.uuid4())[:8]
        self._tick_interval_s = tick_interval_s
        self._position_sync_interval = position_sync_interval

        self._positions_cache: Dict[Tuple[str, str], PositionInfo] = {}
        self._current_candle: Optional[Candle] = None
        self._fills: List[Fill] = []
        self._order_counter = 0
        self._tick_count = 0
        self._running = False

        self._tracker = OrderTracker(
            max_retries=max_retries,
            on_failure=on_failure,
            max_orders_per_sec=max_orders_per_sec,
            max_concurrent_tx=max_concurrent_tx,
            on_fill=self._handle_fill,
            on_fail=self._handle_fail,
        )

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

    # --- Internal order routing ---

    def _submit_order(self, order: Order) -> str:
        if self._risk_manager:
            result = self._risk_manager.evaluate(
                order, self.account, self.positions,
            )
            if result is None:
                logger.info("Order %s rejected by risk guards", order.order_id)
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

    def _handle_fail(self, order_id: str, reason: str) -> None:
        logger.warning("Order %s failed: %s (policy=%s)",
                       order_id, reason, self._tracker.on_failure)
        if self._tracker.on_failure == "halt":
            self._running = False
            logger.error("Strategy halted due to order failure")

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
