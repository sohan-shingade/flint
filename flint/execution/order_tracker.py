"""OrderTracker — manages the full lifecycle of live orders.

Tracks orders from creation through submission, on-chain confirmation,
and fill detection. Handles rate limiting, timeouts, and retries.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..models import Fill, Order, OrderState, OrderType

logger = logging.getLogger("flint.order_tracker")

_TERMINAL_STATES = {
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.EXPIRED,
    OrderState.FAILED,
}

_IN_FLIGHT_STATES = {
    OrderState.SUBMITTED,
}


@dataclass
class TrackedOrder:
    """Wraps a Flint Order with lifecycle tracking metadata."""

    order: Order
    state: OrderState = OrderState.PENDING
    venue_order_id: Optional[int] = None
    tx_sig: Optional[str] = None
    retry_count: int = 0
    submitted_at: Optional[int] = None
    confirmed_at_bar: int = 0
    state_history: List[Tuple[OrderState, int]] = field(default_factory=list)
    fills: List[Fill] = field(default_factory=list)
    created_at: int = field(default_factory=lambda: int(time.time()))

    def __post_init__(self):
        if not self.state_history:
            self.state_history.append((OrderState.PENDING, self.created_at))

    @property
    def flint_order_id(self) -> str:
        return self.order.order_id

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    @property
    def filled_size(self) -> float:
        return sum(f.size for f in self.fills)

    def transition(
        self,
        new_state: OrderState,
        tx_sig: Optional[str] = None,
        venue_order_id: Optional[int] = None,
    ) -> None:
        old_state = self.state
        self.state = new_state
        self.state_history.append((new_state, int(time.time())))
        if tx_sig is not None:
            self.tx_sig = tx_sig
        if venue_order_id is not None:
            self.venue_order_id = venue_order_id
        if new_state == OrderState.SUBMITTED and self.submitted_at is None:
            self.submitted_at = int(time.time())
        logger.debug(
            "Order %s: %s → %s", self.flint_order_id, old_state.value, new_state.value
        )

    def add_fill(self, fill: Fill) -> None:
        self.fills.append(fill)

    def to_state_history_json(self) -> str:
        import json
        return json.dumps([[s.value, ts] for s, ts in self.state_history])


class OrderTracker:
    """Manages active and completed orders with rate limiting.

    Args:
        max_retries: Max submission retry attempts before marking failed.
        on_failure: "drop" (log and continue) or "halt" (stop strategy loop).
        max_orders_per_sec: Rate limit for order submissions.
        max_concurrent_tx: Max in-flight (submitted, not yet confirmed) orders.
        on_fill: Callback(order_id, fill) when a fill is received.
        on_fail: Callback(order_id, reason) when an order fails.
        on_cancel: Callback(order_id) when an order is cancelled.
        on_state_change: Callback(order_id, old_state, new_state) on any transition.
    """

    def __init__(
        self,
        max_retries: int = 3,
        on_failure: str = "drop",
        max_orders_per_sec: int = 10,
        max_concurrent_tx: int = 2,
        on_fill: Optional[Callable] = None,
        on_fail: Optional[Callable] = None,
        on_cancel: Optional[Callable] = None,
        on_state_change: Optional[Callable] = None,
    ):
        self.max_retries = max_retries
        self.on_failure = on_failure
        self.max_orders_per_sec = max_orders_per_sec
        self.max_concurrent_tx = max_concurrent_tx

        self._on_fill = on_fill
        self._on_fail = on_fail
        self._on_cancel = on_cancel
        self._on_state_change = on_state_change

        self.active_orders: Dict[str, TrackedOrder] = {}
        self.completed_orders: Dict[str, TrackedOrder] = {}

        self._submission_timestamps: deque = deque()

    def submit(self, order: Order) -> TrackedOrder:
        tracked = TrackedOrder(order=order)
        self.active_orders[order.order_id] = tracked
        logger.info("Tracking order %s: %s %s %.4f %s",
                     order.order_id, order.side.value, order.market,
                     order.size, order.order_type.value)
        return tracked

    def get(self, order_id: str) -> Optional[TrackedOrder]:
        return self.active_orders.get(order_id) or self.completed_orders.get(order_id)

    def get_pending(self) -> List[TrackedOrder]:
        return [
            t for t in self.active_orders.values()
            if t.state == OrderState.PENDING
        ]

    @property
    def in_flight_count(self) -> int:
        return sum(1 for t in self.active_orders.values() if t.state in _IN_FLIGHT_STATES)

    def can_submit(self) -> bool:
        if self.in_flight_count >= self.max_concurrent_tx:
            return False
        now = time.time()
        while self._submission_timestamps and self._submission_timestamps[0] < now - 1.0:
            self._submission_timestamps.popleft()
        return len(self._submission_timestamps) < self.max_orders_per_sec

    def record_submission(self) -> None:
        self._submission_timestamps.append(time.time())

    def mark_submitted(self, order_id: str, tx_sig: str) -> None:
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return
        old = tracked.state
        tracked.transition(OrderState.SUBMITTED, tx_sig=tx_sig)
        self.record_submission()
        if self._on_state_change:
            self._on_state_change(order_id, old, OrderState.SUBMITTED)

    def mark_confirmed(self, order_id: str, venue_order_id: int, bar_count: int = 0) -> None:
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return
        old = tracked.state
        tracked.transition(OrderState.CONFIRMED, venue_order_id=venue_order_id)
        tracked.confirmed_at_bar = bar_count
        if self._on_state_change:
            self._on_state_change(order_id, old, OrderState.CONFIRMED)

    def mark_filled(self, order_id: str, fill: Fill) -> None:
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return
        tracked.add_fill(fill)
        old = tracked.state
        if tracked.filled_size >= tracked.order.size:
            tracked.transition(OrderState.FILLED)
            self._move_to_completed(order_id)
        else:
            tracked.transition(OrderState.PARTIALLY_FILLED)
        if self._on_fill:
            self._on_fill(order_id, fill)
        if self._on_state_change:
            self._on_state_change(order_id, old, tracked.state)

    def mark_cancelled(self, order_id: str) -> None:
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return
        old = tracked.state
        tracked.transition(OrderState.CANCELLED)
        self._move_to_completed(order_id)
        if self._on_cancel:
            self._on_cancel(order_id)
        if self._on_state_change:
            self._on_state_change(order_id, old, OrderState.CANCELLED)

    def mark_expired(self, order_id: str) -> None:
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return
        old = tracked.state
        tracked.transition(OrderState.EXPIRED)
        self._move_to_completed(order_id)
        if self._on_state_change:
            self._on_state_change(order_id, old, OrderState.EXPIRED)

    def mark_failed(self, order_id: str, reason: str) -> None:
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return
        old = tracked.state
        tracked.transition(OrderState.FAILED)
        self._move_to_completed(order_id)
        logger.warning("Order %s failed: %s", order_id, reason)
        if self._on_fail:
            self._on_fail(order_id, reason)
        if self._on_state_change:
            self._on_state_change(order_id, old, OrderState.FAILED)

    def increment_retry(self, order_id: str) -> bool:
        tracked = self.active_orders.get(order_id)
        if not tracked:
            return False
        tracked.retry_count += 1
        tracked.transition(OrderState.PENDING)
        if tracked.retry_count > self.max_retries:
            self.mark_failed(order_id, reason=f"max retries ({self.max_retries}) exceeded")
            return False
        logger.info("Order %s retry %d/%d", order_id, tracked.retry_count, self.max_retries)
        return True

    def get_submitted(self) -> List[TrackedOrder]:
        """Get all orders in SUBMITTED state (awaiting on-chain confirmation)."""
        return [t for t in self.active_orders.values() if t.state == OrderState.SUBMITTED]

    def get_confirmed(self) -> List[TrackedOrder]:
        """Get all orders in CONFIRMED or PARTIALLY_FILLED state (awaiting fill)."""
        return [t for t in self.active_orders.values()
                if t.state in (OrderState.CONFIRMED, OrderState.PARTIALLY_FILLED)]

    def check_timeouts(self, submission_timeout_s: int = 30, current_bar: int = 0, limit_timeout_bars: int = 10) -> List[str]:
        """Check for timed-out orders. Returns list of order_ids that timed out.

        Args:
            submission_timeout_s: Seconds before a SUBMITTED order is considered timed out.
            current_bar: Current tick/bar count (for limit order timeout).
            limit_timeout_bars: Number of bars before an unfilled limit order expires.
        """
        now = int(time.time())
        timed_out = []
        for oid, tracked in list(self.active_orders.items()):
            # Submission timeout: SUBMITTED but no confirmation within timeout
            if tracked.state == OrderState.SUBMITTED and tracked.submitted_at:
                if now - tracked.submitted_at >= submission_timeout_s:
                    logger.warning("Order %s submission timeout after %ds", oid, now - tracked.submitted_at)
                    if not self.increment_retry(oid):
                        timed_out.append(oid)
                    else:
                        timed_out.append(oid)

            # Limit order timeout: CONFIRMED limit order not filled within N bars
            if (tracked.state in (OrderState.CONFIRMED, OrderState.PARTIALLY_FILLED)
                    and tracked.order.order_type != OrderType.MARKET
                    and tracked.confirmed_at_bar > 0
                    and current_bar - tracked.confirmed_at_bar >= limit_timeout_bars):
                logger.warning("Order %s expired after %d bars", oid, current_bar - tracked.confirmed_at_bar)
                self.mark_expired(oid)
                timed_out.append(oid)

        return timed_out

    def _move_to_completed(self, order_id: str) -> None:
        tracked = self.active_orders.pop(order_id, None)
        if tracked:
            self.completed_orders[order_id] = tracked
