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
        venue: str = "default",
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
        venue: str = "default",
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
        venue: str = "default",
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
        venue: str = "default",
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

    def close_position(self, market: str, venue: str = "default") -> Optional[str]:
        """Close the entire position for a market+venue. Returns order_id or None."""
        for pos in self.positions:
            if pos.market == market and pos.venue == venue:
                opposite = Side.SHORT if pos.side == Side.LONG else Side.LONG
                return self.market_order(
                    market, opposite, pos.size, reduce_only=True, tag="close",
                    venue=venue,
                )
        return None

    def position(self, market: str, venue: str = "default") -> Optional[PositionInfo]:
        """Get position for a specific market+venue, or None."""
        for pos in self.positions:
            if pos.market == market and pos.venue == venue:
                return pos
        return None

    def venue_positions(self, venue: str) -> list:
        """Get all positions for a specific venue."""
        return [p for p in self.positions if p.venue == venue]

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

    def get_funding_rate(self, market: Optional[str] = None) -> Optional[float]:
        """Get the most recent funding rate for a market.

        Returns the hourly funding rate, or None if no data available.
        In backtest: returns the rate at the current candle timestamp.
        In paper/live: returns the latest observed rate.
        """
        return None

    def get_funding_rates(self, market: Optional[str] = None, lookback: int = 24) -> list:
        """Get recent funding rate history for a market.

        Returns list of (ts, rate) tuples, most recent last.
        Default lookback=24 gives 24 hours of hourly rates.
        """
        return []

    def get_funding_by_venue(self, market: Optional[str] = None, lookback: int = 24) -> dict:
        """Get recent funding rates grouped by venue.

        Returns {venue: [(ts, rate), ...]} for the given market.
        Enables cross-venue funding arbitrage strategies.
        """
        return {}

    def get_orderbook(self, market: Optional[str] = None):
        """Get the most recent orderbook snapshot for a market.

        Returns an OrderbookSnapshot or None if no data available.
        """
        return None

    def get_impact_price(self, market: Optional[str] = None, side: Optional[Side] = None, size: float = 0) -> Optional[float]:
        """Estimate the average fill price for a given order size.

        Walks the current orderbook to compute the volume-weighted
        average price. Returns None if no orderbook data available.

        Strategies use this to check execution cost before placing orders:
            impact = ctx.get_impact_price("SOL-PERP", Side.LONG, 100)
            mid = ctx.current_candle.close
            slippage_bps = (impact - mid) / mid * 10000
        """
        return None

    def venue_balance(self, venue: str) -> float:
        """Get available cash on a specific venue. Returns total cash if no allocator."""
        return self.account.cash

    def transfer(self, from_venue: str, to_venue: str, amount: float) -> bool:
        """Transfer capital between venues. Returns True if initiated.

        Transfers are not instant — capital arrives after a venue-specific delay.
        Check pending_transfers() to see in-flight capital.
        """
        return False

    def venue_balances(self) -> dict:
        """Get all venue balances. Returns {"default": total_cash} if no allocator."""
        return {"default": self.account.cash}

    def get_open_interest(self, market: Optional[str] = None) -> Optional[tuple]:
        """Get the most recent open interest for a market.

        Returns (long_oi, short_oi) or None if no data.
        Useful for OI-weighted benchmark computation.
        """
        return None

    def get_open_interest_history(self, market: Optional[str] = None, lookback: int = 24) -> list:
        """Get recent OI history as [(ts, long_oi, short_oi), ...].

        Use for crowding detection and benchmark weighting.
        """
        return []

    def get_venue_snapshots(self, market: Optional[str] = None, lookback: int = 24) -> dict:
        """Get full funding rate snapshots grouped by venue.

        Returns {venue: [FundingRate, ...]} with mark_price, oracle_price intact.
        Use for basis computation: (mark_price - oracle_price) / oracle_price.
        """
        return {}

    def get_oracle_price(self, market: Optional[str] = None) -> Optional[tuple]:
        """Get latest oracle price for a market. Returns (price, timestamp) or None."""
        return None

    def log(self, message: str) -> None:
        """Log a message from the strategy. Default: no-op. Override in subclasses."""
        pass

    def estimate_cost(self, market: str, size: float, venue: str = "default"):
        """Estimate total execution cost. Returns CostEstimate or None."""
        return None
