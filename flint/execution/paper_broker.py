"""PaperBroker — simulates order execution with real market prices.

Used by paper trading to execute orders without touching real funds.
Same fill/fee model interface as backtest for consistency.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from ..models import (
    Candle, Fill, Order, OrderStatus, OrderType, Side,
)
from .fee_models import FeeModel, FlatFeeModel, DriftFeeModel
from .fill_models import FillModel, ClosePriceFill, SlippageFill

logger = logging.getLogger("flint.paper")


class PaperBroker:
    """Simulated broker that fills orders against live candle data."""

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        fill_model: Optional[FillModel] = None,
        fee_model: Optional[FeeModel] = None,
        venue: str = "drift",
    ):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.fill_model = fill_model or SlippageFill(slippage_bps=5.0)
        self.fee_model = fee_model or DriftFeeModel()
        self.venue = venue

        self.positions: Dict[str, dict] = {}  # market -> position dict
        self.pending_orders: List[Order] = []
        self.fills: List[Fill] = []
        self.closed_trades: List[dict] = []
        self.total_fees = 0.0
        self.total_funding = 0.0

        self._limit_timeout_bars = 24  # expire limit/stop orders after N bars
        self._resolution_s = 3600      # candle resolution for age calculation

        # Margin tracking via venue config
        self._venue_config = None
        try:
            from .venue_config import get_venue_config
            self._venue_config = get_venue_config(venue)
        except Exception:
            pass

    @property
    def equity(self) -> float:
        unrealized = sum(p.get("unrealized_pnl", 0) for p in self.positions.values())
        return self.cash + unrealized

    @property
    def margin_used(self) -> float:
        """Total margin allocated to open positions."""
        if not self._venue_config:
            return 0.0
        total = 0.0
        for pos in self.positions.values():
            mark = pos.get("mark_price", pos.get("entry_price", 0))
            notional = pos["size"] * mark
            total += notional * self._venue_config.initial_margin
        return total

    @property
    def free_margin(self) -> float:
        """Available margin for new positions."""
        return max(0, self.equity - self.margin_used)

    @property
    def leverage(self) -> float:
        """Current leverage: total notional / equity."""
        if self.equity <= 0:
            return 0.0
        total_notional = sum(
            pos["size"] * pos.get("mark_price", pos.get("entry_price", 0))
            for pos in self.positions.values()
        )
        return total_notional / self.equity if self.equity > 0 else 0.0

    @property
    def margin_ratio(self) -> float:
        """Equity / total notional. Below maintenance_margin = liquidation."""
        total_notional = sum(
            pos["size"] * pos.get("mark_price", pos.get("entry_price", 0))
            for pos in self.positions.values()
        )
        if total_notional <= 0:
            return float("inf")
        return self.equity / total_notional

    def can_open_position(self, size: float, price: float) -> bool:
        """Check if there's enough free margin to open a position."""
        if not self._venue_config:
            return True  # no margin enforcement without config
        required = size * price * self._venue_config.initial_margin
        return self.free_margin >= required

    def get_liquidation_price(self, market: str) -> float:
        """Compute liquidation price for a position."""
        pos = self.positions.get(market)
        if not pos or not self._venue_config:
            return 0.0
        mmr = self._venue_config.maintenance_margin
        entry = pos["entry_price"]
        size = pos["size"]
        collateral = self.equity  # simplified: full equity backs position
        notional = size * entry
        if pos["side"] == "long":
            return entry - (collateral - mmr * notional) / size if size > 0 else 0
        else:
            return entry + (collateral - mmr * notional) / size if size > 0 else 0

    def apply_funding(self, market: str, rate: float, mark_price: float) -> float:
        """Apply funding rate to an open position.

        Positive rate = longs pay shorts.
        Negative rate = shorts pay longs.
        Returns the payment amount (positive = paid, negative = received).
        """
        pos = self.positions.get(market)
        if pos is None:
            return 0.0

        notional = pos["size"] * mark_price
        payment = notional * rate

        if pos["side"] == "long":
            self.cash -= payment
            self.total_funding += payment
        else:
            self.cash += payment
            self.total_funding -= payment
            payment = -payment

        return payment

    def close_all_positions(self, mark_prices: dict) -> None:
        """Force-close all positions at mark prices. Used by liquidation."""
        import time as _time
        for market in list(self.positions.keys()):
            pos = self.positions[market]
            mark = mark_prices.get(market, pos["entry_price"])
            if pos["side"] == "long":
                pnl = (mark - pos["entry_price"]) * pos["size"]
            else:
                pnl = (pos["entry_price"] - mark) * pos["size"]
            self.cash += pnl
            self.closed_trades.append({
                **pos, "exit_price": mark, "exit_ts": int(_time.time()), "pnl": pnl,
            })
        self.positions.clear()

    def submit_order(self, order: Order) -> str:
        """Accept an order for processing."""
        # Margin check: warn if insufficient margin for new positions
        if self._venue_config and not getattr(order, "reduce_only", False):
            price = order.price if order.price else 0
            # For market orders, price is unknown until fill; use 0 to skip check
            if price > 0 and not self.can_open_position(order.size, price):
                logger.warning(
                    "Insufficient margin for %s %s %.4f @ %.2f "
                    "(free_margin=%.2f, required=%.2f)",
                    order.side.value, order.market, order.size, price,
                    self.free_margin,
                    order.size * price * self._venue_config.initial_margin,
                )
        if order.order_type == OrderType.MARKET:
            # Market orders get queued for immediate fill
            self.pending_orders.append(order)
        else:
            self.pending_orders.append(order)
        return order.order_id

    def process_candle(self, candle: Candle) -> List[Fill]:
        """Process all pending orders against this candle."""
        # Expire timed-out limit/stop orders
        if self._limit_timeout_bars > 0:
            expired = []
            for order in self.pending_orders:
                if order.order_type != OrderType.MARKET and order.ts > 0:
                    age_bars = (candle.ts - order.ts) // max(self._resolution_s, 1)
                    if age_bars >= self._limit_timeout_bars:
                        expired.append(order)
            for order in expired:
                self.pending_orders.remove(order)
                logger.debug("Order %s expired after %d bars", order.order_id, self._limit_timeout_bars)

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
        # Update unrealized PnL and mark price
        for market, pos in self.positions.items():
            if candle.market == market:
                pos["mark_price"] = candle.close
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
