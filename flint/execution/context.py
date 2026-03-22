"""ExecutionContext — the core abstraction for backtest-live symmetry.

Strategies interact with the market exclusively through this interface.
BacktestContext and LiveContext implement it differently, but strategy
code is identical in both modes.
"""
from __future__ import annotations

import abc
from typing import List, Optional

from ..models import (
    AccountState,
    Candle,
    Order,
    PositionInfo,
    Side,
)


class ExecutionContext(abc.ABC):
    """Interface between strategy logic and order execution."""

    # --- State access ---

    @property
    @abc.abstractmethod
    def account(self) -> AccountState:
        """Current account state: equity, cash, unrealized PnL."""
        ...

    @property
    @abc.abstractmethod
    def positions(self) -> List[PositionInfo]:
        """All open positions."""
        ...

    @property
    @abc.abstractmethod
    def pending_orders(self) -> List[Order]:
        """All unfilled orders."""
        ...

    @property
    @abc.abstractmethod
    def current_candle(self) -> Optional[Candle]:
        """The candle currently being processed."""
        ...

    @property
    @abc.abstractmethod
    def timestamp(self) -> int:
        """Current simulation or live timestamp (unix seconds)."""
        ...

    # --- Order placement ---

    @abc.abstractmethod
    def market_order(
        self,
        market: str,
        side: Side,
        size: float,
        reduce_only: bool = False,
        tag: str = "",
    ) -> str:
        """Submit a market order. Returns order_id."""
        ...

    @abc.abstractmethod
    def limit_order(
        self,
        market: str,
        side: Side,
        size: float,
        price: float,
        reduce_only: bool = False,
        tag: str = "",
    ) -> str:
        """Submit a limit order. Returns order_id."""
        ...

    @abc.abstractmethod
    def stop_order(
        self,
        market: str,
        side: Side,
        size: float,
        trigger_price: float,
        tag: str = "",
    ) -> str:
        """Submit a stop-loss order. Triggers a market sell at trigger_price. Returns order_id."""
        ...

    @abc.abstractmethod
    def take_profit_order(
        self,
        market: str,
        side: Side,
        size: float,
        trigger_price: float,
        tag: str = "",
    ) -> str:
        """Submit a take-profit order. Returns order_id."""
        ...

    # --- Order management ---

    @abc.abstractmethod
    def cancel(self, order_id: str) -> bool:
        """Cancel a pending order. Returns True if cancelled."""
        ...

    @abc.abstractmethod
    def cancel_all(self, market: Optional[str] = None) -> int:
        """Cancel all pending orders, optionally filtered by market. Returns count cancelled."""
        ...

    # --- Convenience ---

    def close_position(self, market: str) -> Optional[str]:
        """Close the entire position for a market. Returns order_id or None if no position."""
        for pos in self.positions:
            if pos.market == market:
                opposite = Side.SHORT if pos.side == Side.LONG else Side.LONG
                return self.market_order(
                    market, opposite, pos.size, reduce_only=True, tag="close"
                )
        return None

    def position(self, market: str) -> Optional[PositionInfo]:
        """Get position for a specific market, or None."""
        for pos in self.positions:
            if pos.market == market:
                return pos
        return None

    def get_candles(self, market: str, lookback: int = 50) -> list:
        """Get recent candles for any market (multi-market backtesting).

        Returns up to `lookback` most recent candles for the given market.
        Default: returns empty list. Override in subclasses that support multi-market.
        """
        return []

    @property
    def markets(self) -> list:
        """List of available markets in this context. Default: empty."""
        return []

    def log(self, message: str) -> None:
        """Log a message from the strategy. Default: no-op. Override in subclasses."""
        pass
