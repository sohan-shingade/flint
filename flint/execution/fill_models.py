"""Pluggable fill models for order execution simulation.

Each model determines HOW an order fills against a candle:
what price, whether it fills at all, and with what slippage.
"""
from __future__ import annotations

import abc
from typing import Optional

from ..models import Candle, Fill, Order, OrderbookSnapshot, OrderType, Side


class FillModel(abc.ABC):
    """Determines execution price for an order."""

    @abc.abstractmethod
    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        """Attempt to fill a market order against a candle."""
        ...

    @abc.abstractmethod
    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        """Attempt to fill a limit order if price crosses the limit."""
        ...

    def check_stop_trigger(self, order: Order, candle: Candle) -> bool:
        """Check if a stop order's trigger price was hit during this candle."""
        if order.order_type == OrderType.STOP_LOSS:
            if order.side == Side.SHORT:
                # Long position stop: triggers when price drops to trigger
                return candle.low <= order.price
            else:
                # Short position stop: triggers when price rises to trigger
                return candle.high >= order.price
        elif order.order_type == OrderType.TAKE_PROFIT:
            if order.side == Side.SHORT:
                # Long position TP: triggers when price rises to target
                return candle.high >= order.price
            else:
                # Short position TP: triggers when price drops to target
                return candle.low <= order.price
        return False


class ClosePriceFill(FillModel):
    """v0.1 behavior: fill market orders at candle close price."""

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        return Fill(
            market=order.market, side=order.side, price=candle.close,
            size=order.size, fee=0.0, ts=candle.ts, order_id=order.order_id,
        )

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        if order.side == Side.LONG and candle.low <= order.price:
            fill_price = order.price
        elif order.side == Side.SHORT and candle.high >= order.price:
            fill_price = order.price
        else:
            return None
        return Fill(
            market=order.market, side=order.side, price=fill_price,
            size=order.size, fee=0.0, ts=candle.ts, order_id=order.order_id,
        )


class NextBarOpenFill(FillModel):
    """Fill market orders at the next bar's open price (more realistic)."""

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        return Fill(
            market=order.market, side=order.side, price=candle.open,
            size=order.size, fee=0.0, ts=candle.ts, order_id=order.order_id,
        )

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        if order.side == Side.LONG and candle.low <= order.price:
            fill_price = order.price
        elif order.side == Side.SHORT and candle.high >= order.price:
            fill_price = order.price
        else:
            return None
        return Fill(
            market=order.market, side=order.side, price=fill_price,
            size=order.size, fee=0.0, ts=candle.ts, order_id=order.order_id,
        )


class SlippageFill(FillModel):
    """Apply basis-point slippage to market orders."""

    def __init__(self, slippage_bps: float = 5.0):
        self.slippage_pct = slippage_bps / 10_000

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        base_price = candle.close
        if order.side == Side.LONG:
            fill_price = base_price * (1 + self.slippage_pct)
        else:
            fill_price = base_price * (1 - self.slippage_pct)
        return Fill(
            market=order.market, side=order.side, price=fill_price,
            size=order.size, fee=0.0, ts=candle.ts, order_id=order.order_id,
        )

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        if order.side == Side.LONG and candle.low <= order.price:
            fill_price = order.price
        elif order.side == Side.SHORT and candle.high >= order.price:
            fill_price = order.price
        else:
            return None
        return Fill(
            market=order.market, side=order.side, price=fill_price,
            size=order.size, fee=0.0, ts=candle.ts, order_id=order.order_id,
        )


class OrderbookFillModel(FillModel):
    """Walk the orderbook to compute realistic fill prices.

    For a market buy of 10 SOL against the ask side:
      - Ask level 1: 100.05 x 5 SOL -> fill 5 @ 100.05
      - Ask level 2: 100.10 x 8 SOL -> fill 5 @ 100.10
      - Volume-weighted avg fill: 100.075

    If order size exceeds total book depth, fills what is available (partial fill).
    Falls back to SlippageFill when no orderbook data exists at this timestamp.

    Usage:
        engine = BacktestEngine(strategy, fill_model=OrderbookFillModel(),
                                orderbook_snapshots=snapshots)
    """

    def __init__(self, fallback_slippage_bps: float = 5.0):
        self._fallback = SlippageFill(fallback_slippage_bps)
        self._current_book: Optional[OrderbookSnapshot] = None

    def set_orderbook(self, book: Optional[OrderbookSnapshot]) -> None:
        """Called by the engine/context to set the current orderbook state."""
        self._current_book = book

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        if self._current_book is None or order.market != self._current_book.market:
            return self._fallback.fill_market(order, candle)
        return self._walk_book(order, candle)

    def _walk_book(self, order: Order, candle: Candle) -> Optional[Fill]:
        """Walk the orderbook levels to compute volume-weighted average fill."""
        levels = self._current_book.asks if order.side == Side.LONG else self._current_book.bids
        if not levels:
            return self._fallback.fill_market(order, candle)

        remaining = order.size
        total_cost = 0.0
        filled = 0.0

        for level in levels:
            take = min(remaining, level.size)
            total_cost += take * level.price
            filled += take
            remaining -= take
            if remaining <= 0:
                break

        if filled <= 0:
            return self._fallback.fill_market(order, candle)

        avg_price = total_cost / filled
        return Fill(
            market=order.market, side=order.side, price=avg_price,
            size=filled, fee=0.0, ts=candle.ts, order_id=order.order_id,
        )

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        if order.side == Side.LONG and candle.low <= order.price:
            fill_price = order.price
        elif order.side == Side.SHORT and candle.high >= order.price:
            fill_price = order.price
        else:
            return None
        return Fill(
            market=order.market, side=order.side, price=fill_price,
            size=order.size, fee=0.0, ts=candle.ts, order_id=order.order_id,
        )
