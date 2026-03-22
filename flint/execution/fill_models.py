"""Pluggable fill models for order execution simulation.

Each model determines HOW an order fills against a candle:
what price, whether it fills at all, and with what slippage.
"""
from __future__ import annotations

import abc
from typing import Optional

from ..models import Candle, Fill, Order, OrderType, Side


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
            market=order.market,
            side=order.side,
            price=candle.close,
            size=order.size,
            fee=0.0,  # fee computed separately by FeeModel
            ts=candle.ts,
            order_id=order.order_id,
        )

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        # Limit buy fills if candle low <= limit price
        if order.side == Side.LONG and candle.low <= order.price:
            fill_price = order.price
        # Limit sell fills if candle high >= limit price
        elif order.side == Side.SHORT and candle.high >= order.price:
            fill_price = order.price
        else:
            return None

        return Fill(
            market=order.market,
            side=order.side,
            price=fill_price,
            size=order.size,
            fee=0.0,
            ts=candle.ts,
            order_id=order.order_id,
        )


class NextBarOpenFill(FillModel):
    """Fill market orders at the next bar's open price (more realistic)."""

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        return Fill(
            market=order.market,
            side=order.side,
            price=candle.open,
            size=order.size,
            fee=0.0,
            ts=candle.ts,
            order_id=order.order_id,
        )

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        if order.side == Side.LONG and candle.low <= order.price:
            fill_price = order.price
        elif order.side == Side.SHORT and candle.high >= order.price:
            fill_price = order.price
        else:
            return None

        return Fill(
            market=order.market,
            side=order.side,
            price=fill_price,
            size=order.size,
            fee=0.0,
            ts=candle.ts,
            order_id=order.order_id,
        )


class SlippageFill(FillModel):
    """Apply basis-point slippage to market orders."""

    def __init__(self, slippage_bps: float = 5.0):
        self.slippage_pct = slippage_bps / 10_000

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        base_price = candle.close
        # Buys slip up, sells slip down
        if order.side == Side.LONG:
            fill_price = base_price * (1 + self.slippage_pct)
        else:
            fill_price = base_price * (1 - self.slippage_pct)

        return Fill(
            market=order.market,
            side=order.side,
            price=fill_price,
            size=order.size,
            fee=0.0,
            ts=candle.ts,
            order_id=order.order_id,
        )

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        # Limit orders fill at their specified price (no slippage)
        if order.side == Side.LONG and candle.low <= order.price:
            fill_price = order.price
        elif order.side == Side.SHORT and candle.high >= order.price:
            fill_price = order.price
        else:
            return None

        return Fill(
            market=order.market,
            side=order.side,
            price=fill_price,
            size=order.size,
            fee=0.0,
            ts=candle.ts,
            order_id=order.order_id,
        )
