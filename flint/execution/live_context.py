"""LiveContext — ExecutionContext backed by PaperBroker for paper trading."""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from ..models import (
    AccountState, Candle, Order, OrderType, PositionInfo, Side,
)
from .context import ExecutionContext
from .paper_broker import PaperBroker

_logger = logging.getLogger("flint.paper.strategy")


class LiveContext(ExecutionContext):
    """ExecutionContext for paper (and eventually live) trading."""

    def __init__(self, broker: PaperBroker, store=None, resolution_s: int = 3600, session_id: str = ""):
        self._broker = broker
        self._store = store
        self._resolution_s = resolution_s
        self._session_id = session_id
        self._current_candle: Optional[Candle] = None
        self._order_counter = 0

    def _next_id(self) -> str:
        self._order_counter += 1
        return f"paper-{self._order_counter}"

    @property
    def account(self) -> AccountState:
        unrealized = sum(
            p.get("unrealized_pnl", 0) for p in self._broker.positions.values()
        )
        return AccountState(
            equity=self._broker.equity,
            cash=self._broker.cash,
            unrealized_pnl=unrealized,
            margin_used=self._broker.margin_used,
            free_margin=self._broker.free_margin,
            leverage=self._broker.leverage,
        )

    @property
    def positions(self) -> List[PositionInfo]:
        result = []
        for pos in self._broker.positions.values():
            result.append(PositionInfo(
                market=pos["market"],
                side=Side.LONG if pos["side"] == "long" else Side.SHORT,
                size=pos["size"],
                entry_price=pos["entry_price"],
                unrealized_pnl=pos.get("unrealized_pnl", 0),
                entry_ts=pos.get("entry_ts", 0),
                venue=pos.get("venue", self._broker.venue),
            ))
        return result

    @property
    def pending_orders(self) -> List[Order]:
        return list(self._broker.pending_orders)

    @property
    def current_candle(self) -> Optional[Candle]:
        return self._current_candle

    @property
    def timestamp(self) -> int:
        return self._current_candle.ts if self._current_candle else 0

    def set_candle(self, candle: Candle) -> None:
        self._current_candle = candle

    def market_order(self, market, side, size, reduce_only=False, tag="", venue="default"):
        oid = self._next_id()
        order = Order(market=market, side=side, order_type=OrderType.MARKET,
                      size=size, order_id=oid, ts=self.timestamp)
        self._broker.submit_order(order)
        return oid

    def limit_order(self, market, side, size, price, reduce_only=False, tag="", venue="default"):
        oid = self._next_id()
        order = Order(market=market, side=side, order_type=OrderType.LIMIT,
                      size=size, price=price, order_id=oid, ts=self.timestamp)
        self._broker.submit_order(order)
        return oid

    def stop_order(self, market, side, size, trigger_price, tag="", venue="default"):
        oid = self._next_id()
        order = Order(market=market, side=side, order_type=OrderType.STOP_LOSS,
                      size=size, price=trigger_price, order_id=oid, ts=self.timestamp)
        self._broker.submit_order(order)
        return oid

    def take_profit_order(self, market, side, size, trigger_price, tag="", venue="default"):
        oid = self._next_id()
        order = Order(market=market, side=side, order_type=OrderType.TAKE_PROFIT,
                      size=size, price=trigger_price, order_id=oid, ts=self.timestamp)
        self._broker.submit_order(order)
        return oid

    def cancel(self, order_id):
        return self._broker.cancel_order(order_id)

    def cancel_all(self, market=None):
        return self._broker.cancel_all(market)

    # --- Store-backed data access ---

    def get_funding_rate(self, market=None):
        """Get the most recent funding rate for a market."""
        rates = self.get_funding_rates(market, lookback=1)
        return rates[-1][1] if rates else None

    def get_funding_rates(self, market=None, lookback=24):
        """Get recent funding rate history as [(ts, rate), ...]."""
        if not self._store:
            return []
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return []
        now = int(time.time())
        start = now - lookback * 3600
        try:
            funding = self._store.query_funding_rates(mkt, start, now)
            return [(f.ts, f.rate) for f in funding] if funding else []
        except Exception:
            return []

    def get_funding_by_venue(self, market=None, lookback=24):
        """Get recent funding rates grouped by venue: {venue: [(ts, rate), ...]}."""
        if not self._store:
            return {}
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return {}
        now = int(time.time())
        start = now - lookback * 3600
        try:
            raw = self._store.query_funding_by_venue(mkt, start, now)
            # query_funding_by_venue returns {venue: [{"ts": ..., "rate": ...}, ...]}
            # Convert to {venue: [(ts, rate), ...]} to match BacktestContext interface
            return {
                venue: [(entry["ts"], entry["rate"]) for entry in entries]
                for venue, entries in raw.items()
            }
        except Exception:
            return {}

    def get_orderbook(self, market=None):
        """Get the most recent orderbook snapshot for a market."""
        if not self._store:
            return None
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return None
        try:
            # Query the last hour of snapshots and return the most recent
            now = int(time.time())
            snapshots = self._store.query_orderbook_snapshots(mkt, start_ts=now - 3600, end_ts=now)
            return snapshots[-1] if snapshots else None
        except Exception:
            return None

    def get_candles(self, market, lookback=50):
        """Get recent candles for any available market."""
        if not self._store:
            return []
        try:
            candles = self._store.query_candles(market, self._resolution_s)
            return candles[-lookback:] if candles else []
        except Exception:
            return []

    def venue_balance(self, venue: str) -> float:
        """Get available cash on a specific venue."""
        if hasattr(self._broker, '_allocator') and self._broker._allocator:
            return self._broker._allocator.available(venue)
        return self._broker.cash

    def venue_balances(self) -> dict:
        """Get all venue balances."""
        if hasattr(self._broker, '_allocator') and self._broker._allocator:
            return dict(self._broker._allocator._balances)
        return {"default": self._broker.cash}

    def transfer(self, from_venue: str, to_venue: str, amount: float) -> bool:
        """Initiate a capital transfer between venues. Returns True if successful."""
        if not hasattr(self._broker, '_allocator') or not self._broker._allocator:
            return False
        import time as _time
        t = self._broker._allocator.transfer(from_venue, to_venue, amount, int(_time.time()))
        if t:
            self._broker.cash = self._broker._allocator.total_cash
        return t is not None

    def venue_positions(self, venue: str) -> list:
        """Get positions for a specific venue."""
        return [
            PositionInfo(
                market=p["market"],
                side=Side.LONG if p["side"] == "long" else Side.SHORT,
                size=p["size"],
                entry_price=p["entry_price"],
                unrealized_pnl=p.get("unrealized_pnl", 0),
                entry_ts=p.get("entry_ts", 0),
            )
            for p in self._broker.positions.values()
            if p.get("venue", self._broker.venue) == venue
        ]

    def log(self, message):
        """Log a message from the strategy."""
        _logger.info("[%s] %s", self._session_id, message)
