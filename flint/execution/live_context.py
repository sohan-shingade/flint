"""LiveContext — ExecutionContext backed by PaperBroker for paper trading."""
from __future__ import annotations

from typing import List, Optional

from ..models import (
    AccountState, Candle, Order, OrderType, PositionInfo, Side,
)
from .context import ExecutionContext
from .paper_broker import PaperBroker


class LiveContext(ExecutionContext):
    """ExecutionContext for paper (and eventually live) trading."""

    def __init__(self, broker: PaperBroker):
        self._broker = broker
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
