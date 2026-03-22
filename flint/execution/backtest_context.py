"""BacktestContext — ExecutionContext implementation for simulated backtesting.

Manages positions, pending orders, fills, and equity tracking.
"""
from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional

from ..models import (
    AccountState,
    Candle,
    Fill,
    FundingRate,
    Order,
    OrderStatus,
    OrderType,
    PositionInfo,
    Side,
)
from .context import ExecutionContext
from .fee_models import FeeModel, FlatFeeModel
from .fill_models import FillModel, ClosePriceFill

logger = logging.getLogger("flint.backtest")


class _Position:
    """Internal mutable position tracker."""

    __slots__ = ("market", "side", "size", "entry_price", "entry_ts",
                 "unrealized_pnl", "funding_paid")

    def __init__(self, market: str, side: Side, size: float,
                 entry_price: float, entry_ts: int):
        self.market = market
        self.side = side
        self.size = size  # always positive
        self.entry_price = entry_price
        self.entry_ts = entry_ts
        self.unrealized_pnl = 0.0
        self.funding_paid = 0.0

    def update_pnl(self, current_price: float) -> None:
        if self.side == Side.LONG:
            self.unrealized_pnl = (current_price - self.entry_price) * self.size
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.size

    def to_info(self) -> PositionInfo:
        return PositionInfo(
            market=self.market,
            side=self.side,
            size=self.size,
            entry_price=self.entry_price,
            unrealized_pnl=self.unrealized_pnl,
            entry_ts=self.entry_ts,
        )


class BacktestContext(ExecutionContext):
    """ExecutionContext backed by simulated execution for backtesting."""

    def __init__(
        self,
        initial_capital: float,
        fill_model: Optional[FillModel] = None,
        fee_model: Optional[FeeModel] = None,
        position_size_pct: float = 1.0,
        risk_manager=None,
    ):
        self._initial_capital = initial_capital
        self._cash = initial_capital
        self._fill_model = fill_model or ClosePriceFill()
        self._fee_model = fee_model or FlatFeeModel()
        self._position_size_pct = position_size_pct
        self._risk_manager = risk_manager

        self._positions: Dict[str, _Position] = {}
        self._pending_orders: List[Order] = []
        self._market_orders_queue: List[Order] = []  # orders placed this bar
        self._fills: List[Fill] = []
        self._closed_positions: List[dict] = []  # for result building
        self._total_fees = 0.0
        self._total_funding = 0.0
        self._log_messages: List[str] = []

        self._current_candle: Optional[Candle] = None
        self._order_counter = 0
        self._market_histories: Dict[str, List[Candle]] = {}  # multi-market candle access

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"bt-{self._order_counter}"

    # --- ExecutionContext properties ---

    @property
    def account(self) -> AccountState:
        unrealized = sum(p.unrealized_pnl for p in self._positions.values())
        return AccountState(
            equity=self._cash + unrealized,
            cash=self._cash,
            unrealized_pnl=unrealized,
        )

    @property
    def positions(self) -> List[PositionInfo]:
        return [p.to_info() for p in self._positions.values()]

    @property
    def pending_orders(self) -> List[Order]:
        return list(self._pending_orders)

    @property
    def current_candle(self) -> Optional[Candle]:
        return self._current_candle

    @property
    def timestamp(self) -> int:
        return self._current_candle.ts if self._current_candle else 0

    # --- Order placement ---

    def _check_risk(self, order: Order) -> Optional[Order]:
        """Run order through risk manager if present."""
        if self._risk_manager is None:
            return order
        return self._risk_manager.evaluate(order, self.account, self.positions)

    def market_order(self, market: str, side: Side, size: float,
                     reduce_only: bool = False, tag: str = "") -> str:
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.MARKET,
            size=size, order_id=oid, ts=self.timestamp,
        )
        checked = self._check_risk(order)
        if checked is None:
            return oid  # rejected by risk manager
        self._market_orders_queue.append(checked)
        return oid

    def _check_order_cap(self) -> bool:
        """Return False if order cap reached."""
        if len(self._pending_orders) >= 100:
            self._log_messages.append(f"[{self.timestamp}] WARNING: 100 pending order cap reached — order dropped")
            return False
        return True

    def limit_order(self, market: str, side: Side, size: float, price: float,
                    reduce_only: bool = False, tag: str = "") -> str:
        if not self._check_order_cap():
            return ""
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.LIMIT,
            size=size, price=price, order_id=oid, ts=self.timestamp,
        )
        self._pending_orders.append(order)
        return oid

    def stop_order(self, market: str, side: Side, size: float,
                   trigger_price: float, tag: str = "") -> str:
        if not self._check_order_cap():
            return ""
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.STOP_LOSS,
            size=size, price=trigger_price, order_id=oid, ts=self.timestamp,
        )
        self._pending_orders.append(order)
        return oid

    def take_profit_order(self, market: str, side: Side, size: float,
                          trigger_price: float, tag: str = "") -> str:
        if not self._check_order_cap():
            return ""
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.TAKE_PROFIT,
            size=size, price=trigger_price, order_id=oid, ts=self.timestamp,
        )
        self._pending_orders.append(order)
        return oid

    def cancel(self, order_id: str) -> bool:
        for i, o in enumerate(self._pending_orders):
            if o.order_id == order_id:
                self._pending_orders.pop(i)
                return True
        return False

    def cancel_all(self, market: Optional[str] = None) -> int:
        if market is None:
            count = len(self._pending_orders)
            self._pending_orders.clear()
            return count
        before = len(self._pending_orders)
        self._pending_orders = [o for o in self._pending_orders if o.market != market]
        return before - len(self._pending_orders)

    def log(self, message: str) -> None:
        self._log_messages.append(f"[{self.timestamp}] {message}")

    # --- Engine-called methods ---

    def set_candle(self, candle: Candle) -> None:
        """Called by engine before strategy runs."""
        self._current_candle = candle
        # Update unrealized PnL for open positions
        for pos in self._positions.values():
            pos.update_pnl(candle.close)

    def set_market_histories(self, histories: Dict[str, List[Candle]]) -> None:
        """Set multi-market candle data for cross-market access."""
        self._market_histories = histories

    def get_candles(self, market: str, lookback: int = 50) -> List[Candle]:
        """Get recent candles for any available market."""
        history = self._market_histories.get(market, [])
        return history[-lookback:] if history else []

    @property
    def markets(self) -> List[str]:
        """List of available markets."""
        return list(self._market_histories.keys())

    def process_pending_orders(self, candle: Candle) -> List[Fill]:
        """Process stop/limit orders against this candle BEFORE strategy runs.

        Called at the start of each bar so that SL/TP triggers execute
        before the strategy sees the candle.
        """
        fills = []
        remaining = []

        for order in self._pending_orders:
            if order.market != candle.market:
                remaining.append(order)
                continue

            fill = None
            if order.order_type in (OrderType.STOP_LOSS, OrderType.TAKE_PROFIT):
                if self._fill_model.check_stop_trigger(order, candle):
                    # Stop triggered — fill as market order at trigger price
                    fill = Fill(
                        market=order.market,
                        side=order.side,
                        price=order.price,
                        size=order.size,
                        fee=0.0,
                        ts=candle.ts,
                        order_id=order.order_id,
                    )
            elif order.order_type == OrderType.LIMIT:
                fill = self._fill_model.fill_limit(order, candle)

            if fill is not None:
                fee = self._fee_model.compute_fee(fill)
                fill = Fill(
                    market=fill.market, side=fill.side, price=fill.price,
                    size=fill.size, fee=fee, ts=fill.ts, order_id=fill.order_id,
                )
                self._apply_fill(fill)
                fills.append(fill)
            else:
                remaining.append(order)

        self._pending_orders = remaining
        return fills

    def process_market_orders(self, candle: Candle) -> List[Fill]:
        """Process market orders placed during this bar AFTER strategy runs."""
        fills = []
        for order in self._market_orders_queue:
            fill = self._fill_model.fill_market(order, candle)
            if fill is not None:
                fee = self._fee_model.compute_fee(fill)
                fill = Fill(
                    market=fill.market, side=fill.side, price=fill.price,
                    size=fill.size, fee=fee, ts=fill.ts, order_id=fill.order_id,
                )
                self._apply_fill(fill)
                fills.append(fill)
        self._market_orders_queue.clear()
        return fills

    def apply_funding(self, funding_rate: FundingRate) -> float:
        """Apply funding payment to an open position. Returns amount paid."""
        market = funding_rate.market
        pos = self._positions.get(market)
        if pos is None:
            return 0.0

        # Funding: longs pay shorts when rate > 0, shorts pay longs when rate < 0
        notional = pos.size * funding_rate.oracle_price
        if pos.side == Side.LONG:
            payment = notional * funding_rate.rate
        else:
            payment = -notional * funding_rate.rate

        self._cash -= payment
        pos.funding_paid += payment
        self._total_funding += payment
        return payment

    def close_all_positions(self, candle: Candle) -> List[Fill]:
        """Force-close all positions at candle close. Used at end of backtest."""
        fills = []
        for market in list(self._positions.keys()):
            pos = self._positions[market]
            opposite = Side.SHORT if pos.side == Side.LONG else Side.LONG
            order = Order(
                market=market, side=opposite, order_type=OrderType.MARKET,
                size=pos.size, order_id=self._next_order_id(), ts=candle.ts,
            )
            fill = self._fill_model.fill_market(order, candle)
            if fill:
                fee = self._fee_model.compute_fee(fill)
                fill = Fill(
                    market=fill.market, side=fill.side, price=fill.price,
                    size=fill.size, fee=fee, ts=fill.ts, order_id=fill.order_id,
                )
                self._apply_fill(fill)
                fills.append(fill)
        return fills

    # --- Internal ---

    def _apply_fill(self, fill: Fill) -> None:
        """Update positions and cash based on a fill."""
        self._fills.append(fill)
        self._cash -= fill.fee
        self._total_fees += fill.fee

        market = fill.market
        pos = self._positions.get(market)

        if pos is None:
            # Opening a new position
            self._positions[market] = _Position(
                market=market,
                side=fill.side,
                size=fill.size,
                entry_price=fill.price,
                entry_ts=fill.ts,
            )
        elif pos.side == fill.side:
            # Adding to position (DCA)
            total_cost = pos.entry_price * pos.size + fill.price * fill.size
            pos.size += fill.size
            pos.entry_price = total_cost / pos.size if pos.size > 0.0001 else fill.price
        else:
            # Reducing or closing position
            if fill.size >= pos.size:
                # Full close
                if pos.side == Side.LONG:
                    pnl = round((fill.price - pos.entry_price) * pos.size, 8)
                else:
                    pnl = round((pos.entry_price - fill.price) * pos.size, 8)
                self._cash += pnl
                self._closed_positions.append({
                    "market": market,
                    "side": pos.side.value,
                    "size": pos.size,
                    "entry_price": pos.entry_price,
                    "exit_price": fill.price,
                    "entry_ts": pos.entry_ts,
                    "exit_ts": fill.ts,
                    "pnl": pnl,
                    "funding_paid": pos.funding_paid,
                })
                remainder = fill.size - pos.size
                del self._positions[market]
                if remainder > 0.0001:  # ignore dust positions from float rounding
                    # Flip: open opposite position with remainder
                    self._positions[market] = _Position(
                        market=market,
                        side=fill.side,
                        size=remainder,
                        entry_price=fill.price,
                        entry_ts=fill.ts,
                    )
            else:
                # Partial close
                if pos.side == Side.LONG:
                    pnl = round((fill.price - pos.entry_price) * fill.size, 8)
                else:
                    pnl = round((pos.entry_price - fill.price) * fill.size, 8)
                self._cash += pnl
                pos.size -= fill.size

    # --- Results ---

    @property
    def all_fills(self) -> List[Fill]:
        return list(self._fills)

    @property
    def closed_trades(self) -> List[dict]:
        return list(self._closed_positions)

    @property
    def total_fees(self) -> float:
        return self._total_fees

    @property
    def total_funding(self) -> float:
        return self._total_funding
