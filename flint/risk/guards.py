"""Risk guards — rules that can reject or modify orders before execution.

Guards are chained in a RiskManager. Each guard inspects the order against
the current account state and positions, and either passes it through,
modifies it, or rejects it (returns None).
"""
from __future__ import annotations

import abc
import logging
from typing import List, Optional

from ..models import AccountState, Order, PositionInfo

logger = logging.getLogger("flint.risk")


class RiskGuard(abc.ABC):
    """A single risk rule that evaluates an order."""

    @abc.abstractmethod
    def check(
        self,
        order: Order,
        account: AccountState,
        positions: List[PositionInfo],
    ) -> Optional[Order]:
        """Evaluate an order. Return the order to pass, or None to reject."""
        ...


class MaxPositionSize(RiskGuard):
    """Reject orders whose cumulative notional exceeds max_notional."""

    def __init__(self, max_notional: float):
        self.max_notional = max_notional

    def check(self, order, account, positions):
        # Estimate price for market orders (price=0)
        price = order.price
        if price <= 0:
            for p in positions:
                if p.market == order.market and p.entry_price > 0:
                    price = p.entry_price
                    break
            if price <= 0:
                price = 1.0
        # Check cumulative notional (existing + new order)
        existing = sum(p.size * p.entry_price for p in positions if p.market == order.market)
        new_notional = order.size * price
        total = existing + new_notional
        if total > self.max_notional:
            logger.info("MaxPositionSize: rejected order %s (total_notional=%.2f > max=%.2f)",
                       order.order_id, total, self.max_notional)
            return None
        return order


class MaxOpenPositions(RiskGuard):
    """Reject new position orders if max open positions reached."""

    def __init__(self, max_positions: int = 5):
        self.max_positions = max_positions

    def check(self, order, account, positions):
        # Only block new positions, not closes/reduces
        existing_markets = {p.market for p in positions}
        if order.market in existing_markets:
            return order  # modifying existing position is ok
        if len(positions) >= self.max_positions:
            logger.info("MaxOpenPositions: rejected order %s (%d positions open)",
                       order.order_id, len(positions))
            return None
        return order


class MaxDrawdownCircuitBreaker(RiskGuard):
    """Stop all trading if drawdown exceeds threshold."""

    def __init__(self, max_drawdown_pct: float, initial_capital: float):
        self.max_drawdown_pct = max_drawdown_pct
        self.initial_capital = initial_capital
        self._peak_equity = initial_capital
        self._tripped = False

    def check(self, order, account, positions):
        if self._tripped:
            logger.info("MaxDrawdown: circuit breaker tripped, rejecting order %s",
                       order.order_id)
            return None

        self._peak_equity = max(self._peak_equity, account.equity)
        if self._peak_equity > 0:
            drawdown = (self._peak_equity - account.equity) / self._peak_equity
            if drawdown >= self.max_drawdown_pct:
                self._tripped = True
                logger.warning("MaxDrawdown: circuit breaker tripped at %.2f%% drawdown",
                             drawdown * 100)
                return None
        return order

    def reset(self) -> None:
        """Reset the circuit breaker."""
        self._tripped = False
        self._peak_equity = self.initial_capital


class DailyLossLimit(RiskGuard):
    """Stop trading after daily loss limit is hit."""

    def __init__(self, max_daily_loss: float, initial_capital: float):
        self.max_daily_loss = max_daily_loss
        self.initial_capital = initial_capital
        self._day_start_equity: float = initial_capital
        self._current_day: int = 0
        self._tripped = False

    def check(self, order, account, positions):
        # Approximate day from timestamp (order.ts / 86400)
        day = order.ts // 86400 if order.ts else 0
        if day != self._current_day:
            self._current_day = day
            self._day_start_equity = account.equity
            self._tripped = False

        if self._tripped:
            return None

        daily_pnl = account.equity - self._day_start_equity
        if daily_pnl <= -self.max_daily_loss:
            self._tripped = True
            logger.info("DailyLossLimit: tripped at %.2f daily loss", daily_pnl)
            return None
        return order


class RiskManager:
    """Chains multiple RiskGuards. An order must pass ALL guards."""

    def __init__(self, guards: Optional[List[RiskGuard]] = None):
        self.guards = guards or []
        self.rejections: List[dict] = []  # log of rejected orders

    def evaluate(
        self,
        order: Order,
        account: AccountState,
        positions: List[PositionInfo],
    ) -> Optional[Order]:
        """Run order through all guards. Returns None if rejected."""
        for guard in self.guards:
            result = guard.check(order, account, positions)
            if result is None:
                self.rejections.append({
                    "order_id": order.order_id,
                    "guard": type(guard).__name__,
                    "ts": order.ts,
                })
                return None
            order = result
        return order

    def add_guard(self, guard: RiskGuard) -> None:
        self.guards.append(guard)
