"""PaperBroker — simulates order execution with real market prices.

Used by paper trading to execute orders without touching real funds.
Same fill/fee model interface as backtest for consistency.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from ..models import (
    Candle, Fill, Order, OrderStatus, OrderType, Side,
)
from .fee_models import FeeModel, FlatFeeModel
from .fill_models import FillModel, ClosePriceFill


class PaperBroker:
    """Simulated broker that fills orders against live candle data."""

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        fill_model: Optional[FillModel] = None,
        fee_model: Optional[FeeModel] = None,
    ):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.fill_model = fill_model or ClosePriceFill()
        self.fee_model = fee_model or FlatFeeModel()

        self.positions: Dict[str, dict] = {}  # market -> position dict
        self.pending_orders: List[Order] = []
        self.fills: List[Fill] = []
        self.closed_trades: List[dict] = []
        self.total_fees = 0.0
        self.total_funding = 0.0

    @property
    def equity(self) -> float:
        unrealized = sum(p.get("unrealized_pnl", 0) for p in self.positions.values())
        return self.cash + unrealized

    def submit_order(self, order: Order) -> str:
        """Accept an order for processing."""
        if order.order_type == OrderType.MARKET:
            # Market orders get queued for immediate fill
            self.pending_orders.append(order)
        else:
            self.pending_orders.append(order)
        return order.order_id

    def process_candle(self, candle: Candle) -> List[Fill]:
        """Process all pending orders against this candle."""
        fills = []
        remaining = []

        for order in self.pending_orders:
            if order.market != candle.market:
                remaining.append(order)
                continue

            fill = None
            if order.order_type == OrderType.MARKET:
                fill = self.fill_model.fill_market(order, candle)
            elif order.order_type == OrderType.LIMIT:
                fill = self.fill_model.fill_limit(order, candle)
            elif order.order_type in (OrderType.STOP_LOSS, OrderType.TAKE_PROFIT):
                if self.fill_model.check_stop_trigger(order, candle):
                    fill = Fill(
                        market=order.market, side=order.side,
                        price=order.price, size=order.size,
                        fee=0.0, ts=candle.ts, order_id=order.order_id,
                    )

            if fill is not None:
                fee = self.fee_model.compute_fee(fill)
                fill = Fill(
                    market=fill.market, side=fill.side, price=fill.price,
                    size=fill.size, fee=fee, ts=fill.ts, order_id=fill.order_id,
                )
                self._apply_fill(fill, candle)
                fills.append(fill)
            else:
                remaining.append(order)

        self.pending_orders = remaining
        # Update unrealized PnL
        for market, pos in self.positions.items():
            if candle.market == market:
                if pos["side"] == "long":
                    pos["unrealized_pnl"] = (candle.close - pos["entry_price"]) * pos["size"]
                else:
                    pos["unrealized_pnl"] = (pos["entry_price"] - candle.close) * pos["size"]

        return fills

    def cancel_order(self, order_id: str) -> bool:
        for i, o in enumerate(self.pending_orders):
            if o.order_id == order_id:
                self.pending_orders.pop(i)
                return True
        return False

    def cancel_all(self, market: Optional[str] = None) -> int:
        if market is None:
            count = len(self.pending_orders)
            self.pending_orders.clear()
            return count
        before = len(self.pending_orders)
        self.pending_orders = [o for o in self.pending_orders if o.market != market]
        return before - len(self.pending_orders)

    def _apply_fill(self, fill: Fill, candle: Candle) -> None:
        self.fills.append(fill)
        self.cash -= fill.fee
        self.total_fees += fill.fee

        market = fill.market
        pos = self.positions.get(market)

        if pos is None:
            self.positions[market] = {
                "market": market,
                "side": fill.side.value,
                "size": fill.size,
                "entry_price": fill.price,
                "entry_ts": fill.ts,
                "unrealized_pnl": 0.0,
            }
        elif pos["side"] == fill.side.value:
            # DCA
            total_cost = pos["entry_price"] * pos["size"] + fill.price * fill.size
            pos["size"] += fill.size
            pos["entry_price"] = total_cost / pos["size"] if pos["size"] else 0
        else:
            # Close
            if fill.size >= pos["size"]:
                if pos["side"] == "long":
                    pnl = (fill.price - pos["entry_price"]) * pos["size"]
                else:
                    pnl = (pos["entry_price"] - fill.price) * pos["size"]
                self.cash += pnl
                self.closed_trades.append({
                    **pos,
                    "exit_price": fill.price,
                    "exit_ts": fill.ts,
                    "pnl": pnl,
                })
                del self.positions[market]
            else:
                if pos["side"] == "long":
                    pnl = (fill.price - pos["entry_price"]) * fill.size
                else:
                    pnl = (pos["entry_price"] - fill.price) * fill.size
                self.cash += pnl
                pos["size"] -= fill.size
