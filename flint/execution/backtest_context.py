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

    __slots__ = ("market", "venue", "side", "size", "entry_price", "entry_ts",
                 "unrealized_pnl", "funding_paid")

    def __init__(self, market: str, side: Side, size: float,
                 entry_price: float, entry_ts: int, venue: str = "default"):
        self.market = market
        self.venue = venue
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
            venue=self.venue,
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
        margin_engine=None,
        capital_allocator=None,
    ):
        self._initial_capital = initial_capital
        self._cash = initial_capital
        self._fill_model = fill_model or ClosePriceFill()
        self._fee_model = fee_model or FlatFeeModel()
        self._position_size_pct = position_size_pct
        self._risk_manager = risk_manager
        self._margin_engine = margin_engine  # Optional: enables margin/liquidation tracking
        self._allocator = capital_allocator  # Optional: enables per-venue capital tracking
        if self._allocator:
            self._cash = self._allocator.total_cash  # sync initial

        self._positions: Dict[tuple, _Position] = {}  # key: (venue, market)
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

        # Funding rate data for strategy access
        self._funding_history: Dict[str, List[FundingRate]] = {}  # market -> [FundingRate]
        self._venue_funding: Dict[str, Dict[str, List[FundingRate]]] = {}  # market -> {venue -> [FundingRate]}

        # Orderbook data for strategy access and fill models
        self._orderbook_history: Dict[str, List] = {}  # market -> [OrderbookSnapshot]

        # Open interest data for strategy access
        self._oi_history: Dict[str, List] = {}  # market -> [OpenInterest]

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"bt-{self._order_counter}"

    # --- ExecutionContext properties ---

    @property
    def account(self) -> AccountState:
        unrealized = sum(p.unrealized_pnl for p in self._positions.values())
        equity = self._cash + unrealized

        margin_used = 0.0
        leverage = 0.0
        if self._margin_engine and self._positions:
            ms = self._margin_engine.compute_margin_state(self._cash, self.positions)
            margin_used = ms.total_margin_used
            leverage = ms.leverage

        return AccountState(
            equity=equity,
            cash=self._cash,
            unrealized_pnl=unrealized,
            margin_used=margin_used,
            free_margin=self._cash - margin_used,
            leverage=leverage,
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
                     reduce_only: bool = False, tag: str = "",
                     venue: str = "default") -> str:
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.MARKET,
            size=size, order_id=oid, ts=self.timestamp, venue=venue,
        )
        checked = self._check_risk(order)
        if checked is None:
            return oid  # rejected by risk manager

        # Enforce reduce_only: must have an opposite-side position to reduce
        if reduce_only:
            has_opposite = any(
                p.side != side and m == market
                for (v, m), p in self._positions.items()
            )
            if not has_opposite:
                self._log_messages.append(
                    f"[{self.timestamp}] WARNING: reduce_only rejected — no opposite position in {market}")
                return oid

        # Margin check (if enabled and not a reduce-only order)
        if self._margin_engine and not reduce_only:
            price = self._current_candle.close if self._current_candle else 0
            # Use venue-specific cash when allocator is present
            cash_for_margin = (
                self._allocator.available(venue) if self._allocator
                else self._cash
            )
            allowed, reason = self._margin_engine.check_can_open(
                checked, cash_for_margin, self.positions, price
            )
            if not allowed:
                self._log_messages.append(f"[{self.timestamp}] MARGIN REJECTED: {reason}")
                return oid

        self._market_orders_queue.append(checked)
        return oid

    def _check_order_cap(self) -> bool:
        """Return False if order cap reached."""
        if len(self._pending_orders) >= 100:
            self._log_messages.append(f"[{self.timestamp}] WARNING: 100 pending order cap reached — order dropped")
            return False
        return True

    def limit_order(self, market: str, side: Side, size: float, price: float,
                    reduce_only: bool = False, tag: str = "",
                    venue: str = "default") -> str:
        if not self._check_order_cap():
            return ""
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.LIMIT,
            size=size, price=price, order_id=oid, ts=self.timestamp, venue=venue,
        )
        self._pending_orders.append(order)
        return oid

    def stop_order(self, market: str, side: Side, size: float,
                   trigger_price: float, tag: str = "",
                   venue: str = "default") -> str:
        if not self._check_order_cap():
            return ""
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.STOP_LOSS,
            size=size, price=trigger_price, order_id=oid, ts=self.timestamp, venue=venue,
        )
        self._pending_orders.append(order)
        return oid

    def take_profit_order(self, market: str, side: Side, size: float,
                          trigger_price: float, tag: str = "",
                          venue: str = "default") -> str:
        if not self._check_order_cap():
            return ""
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.TAKE_PROFIT,
            size=size, price=trigger_price, order_id=oid, ts=self.timestamp, venue=venue,
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
        # Update unrealized PnL for open positions using each position's market price
        for (venue, market), pos in self._positions.items():
            if market == candle.market:
                pos.update_pnl(candle.close)
            else:
                # Use the latest candle from that market's history
                hist = self._market_histories.get(market)
                if hist:
                    pos.update_pnl(hist[-1].close)

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

    def add_funding_rate(self, fr: FundingRate) -> None:
        """Record a funding rate for strategy access. Called by engine."""
        mkt = fr.market
        if mkt not in self._funding_history:
            self._funding_history[mkt] = []
        self._funding_history[mkt].append(fr)

        venue = getattr(fr, 'source', 'drift')
        if mkt not in self._venue_funding:
            self._venue_funding[mkt] = {}
        if venue not in self._venue_funding[mkt]:
            self._venue_funding[mkt][venue] = []
        self._venue_funding[mkt][venue].append(fr)

    def get_funding_rate(self, market: Optional[str] = None) -> Optional[float]:
        """Get the most recent funding rate for a market."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return None
        history = self._funding_history.get(mkt)
        if not history:
            return None
        return history[-1].rate

    def get_funding_rates(self, market: Optional[str] = None, lookback: int = 24) -> list:
        """Get recent funding rate history as [(ts, rate), ...]."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return []
        history = self._funding_history.get(mkt, [])
        return [(fr.ts, fr.rate) for fr in history[-lookback:]]

    def get_funding_by_venue(self, market: Optional[str] = None, lookback: int = 24) -> dict:
        """Get recent funding rates grouped by venue: {venue: [(ts, rate), ...]}."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return {}
        venues = self._venue_funding.get(mkt, {})
        return {
            venue: [(fr.ts, fr.rate) for fr in rates[-lookback:]]
            for venue, rates in venues.items()
        }

    def get_venue_snapshots(self, market: Optional[str] = None, lookback: int = 24) -> dict:
        """Get full FundingRate objects grouped by venue (includes mark/oracle prices)."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return {}
        venues = self._venue_funding.get(mkt, {})
        return {
            venue: list(rates[-lookback:])
            for venue, rates in venues.items()
        }

    def add_orderbook_snapshot(self, snapshot) -> None:
        """Record an orderbook snapshot. Called by engine."""
        mkt = snapshot.market
        if mkt not in self._orderbook_history:
            self._orderbook_history[mkt] = []
        self._orderbook_history[mkt].append(snapshot)

    def get_orderbook(self, market: Optional[str] = None):
        """Get the most recent orderbook snapshot for a market."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return None
        history = self._orderbook_history.get(mkt)
        if not history:
            return None
        return history[-1]

    def get_impact_price(self, market: Optional[str] = None, side=None, size: float = 0) -> Optional[float]:
        """Walk the current orderbook to estimate fill price for a given size."""
        book = self.get_orderbook(market)
        if book is None or side is None or size <= 0:
            return None

        levels = book.asks if side == Side.LONG else book.bids
        if not levels:
            return None

        remaining = size
        total_cost = 0.0
        filled = 0.0
        for level in levels:
            take = min(remaining, level.size)
            total_cost += take * level.price
            filled += take
            remaining -= take
            if remaining <= 0:
                break
        if filled == 0:
            return None
        return total_cost / filled

    def add_open_interest(self, oi) -> None:
        """Record an OI snapshot. Called by engine."""
        mkt = oi.market
        if mkt not in self._oi_history:
            self._oi_history[mkt] = []
        self._oi_history[mkt].append(oi)

    def get_open_interest(self, market=None):
        """Get most recent (long_oi, short_oi) for a market."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return None
        history = self._oi_history.get(mkt)
        if not history:
            return None
        oi = history[-1]
        return (oi.long_oi, oi.short_oi)

    def get_open_interest_history(self, market=None, lookback: int = 24) -> list:
        """Get recent OI as [(ts, long_oi, short_oi), ...]."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return []
        history = self._oi_history.get(mkt, [])
        return [(oi.ts, oi.long_oi, oi.short_oi) for oi in history[-lookback:]]

    def check_liquidations(self, candle: Candle) -> list:
        """Check all positions for liquidation. Called by engine each bar.

        Returns list of LiquidationEvents. Force-closes liquidated positions.
        """
        if not self._margin_engine or not self._positions:
            return []

        # Build current prices for all markets with positions
        prices: Dict[str, float] = {candle.market: candle.close}
        for mkt, hist in self._market_histories.items():
            if hist:
                prices[mkt] = hist[-1].close

        events = self._margin_engine.check_liquidations(
            self._positions, prices, candle.ts
        )

        # Force-close liquidated positions
        for event in events:
            key = (event.venue, event.market)
            pos = self._positions.get(key)
            if pos is None:
                continue

            # Record the closed position with liquidation loss
            self._closed_positions.append({
                "market": event.market,
                "venue": event.venue,
                "side": pos.side.value,
                "size": pos.size,
                "entry_price": pos.entry_price,
                "exit_price": event.liq_price,
                "entry_ts": pos.entry_ts,
                "exit_ts": event.ts,
                "pnl": event.loss,
                "funding_paid": pos.funding_paid,
                "liquidated": True,
            })

            # Apply the loss to venue cash (event.loss already includes penalty)
            self._credit_cash(event.loss, event.venue)
            self._total_fees += event.penalty
            del self._positions[key]

            # Cancel any pending orders for this position
            self._pending_orders = [
                o for o in self._pending_orders
                if not (o.market == event.market and o.venue == event.venue)
            ]

            self._log_messages.append(
                f"[{event.ts}] LIQUIDATED: {event.side} {event.size:.4f} {event.market} "
                f"on {event.venue} @ {event.liq_price:.2f} "
                f"(entry={event.entry_price:.2f}, loss=${event.loss:.2f}, penalty=${event.penalty:.2f})"
            )

        return events

    def _resolve_candle(self, market: str, primary_candle: Candle) -> Optional[Candle]:
        """Get the current candle for any market.

        For the primary market, returns the primary candle.
        For other markets, returns the last candle from market histories.
        This enables cross-market execution (e.g. spot hedge + perp).
        """
        if market == primary_candle.market:
            return primary_candle
        history = self._market_histories.get(market)
        if history:
            return history[-1]
        return None

    def process_pending_orders(self, candle: Candle) -> List[Fill]:
        """Process stop/limit orders against current candles BEFORE strategy runs.

        Resolves the correct candle for each order's market, enabling
        cross-market stops (e.g. stop-loss on a spot position).
        """
        fills = []
        remaining = []

        for order in self._pending_orders:
            order_candle = self._resolve_candle(order.market, candle)
            if order_candle is None:
                remaining.append(order)
                continue

            fill = None
            if order.order_type in (OrderType.STOP_LOSS, OrderType.TAKE_PROFIT):
                if self._fill_model.check_stop_trigger(order, order_candle):
                    fill = Fill(
                        market=order.market,
                        side=order.side,
                        price=order.price,
                        size=order.size,
                        fee=0.0,
                        ts=order_candle.ts,
                        order_id=order.order_id,
                        venue=order.venue,
                    )
            elif order.order_type == OrderType.LIMIT:
                fill = self._fill_model.fill_limit(order, order_candle)

            if fill is not None:
                fee = self._fee_model.compute_fee(fill)
                fill = Fill(
                    market=fill.market, side=fill.side, price=fill.price,
                    size=fill.size, fee=fee, ts=fill.ts, order_id=fill.order_id,
                    venue=order.venue,
                )
                self._apply_fill(fill)
                fills.append(fill)
            else:
                remaining.append(order)

        self._pending_orders = remaining
        return fills

    def process_market_orders(self, candle: Candle) -> List[Fill]:
        """Process market orders placed during this bar AFTER strategy runs.

        Resolves the correct candle for each order's market, enabling
        cross-market orders (e.g. buy spot while selling perp).
        """
        fills = []
        for order in self._market_orders_queue:
            order_candle = self._resolve_candle(order.market, candle)
            if order_candle is None:
                self._log_messages.append(
                    f"[{self.timestamp}] WARNING: No candle data for {order.market} — "
                    f"order dropped. Download this market's data first."
                )
                continue
            fill = self._fill_model.fill_market(order, order_candle)
            if fill is not None:
                fee = self._fee_model.compute_fee(fill)
                fill = Fill(
                    market=fill.market, side=fill.side, price=fill.price,
                    size=fill.size, fee=fee, ts=fill.ts, order_id=fill.order_id,
                    venue=order.venue,
                )
                self._apply_fill(fill)
                fills.append(fill)
        self._market_orders_queue.clear()
        return fills

    def apply_funding(self, funding_rate: FundingRate) -> float:
        """Apply funding payment to all positions in this market. Returns total paid."""
        market = funding_rate.market
        total_payment = 0.0
        for (v, m), pos in self._positions.items():
            if m != market:
                continue
            # Use funding record's oracle_price; fall back to current candle close
            price = funding_rate.oracle_price
            if price <= 0 and self._current_candle:
                price = self._current_candle.close
            notional = pos.size * price
            if pos.side == Side.LONG:
                payment = notional * funding_rate.rate
            else:
                payment = -notional * funding_rate.rate
            self._debit_cash(payment, v)
            pos.funding_paid += payment
            self._total_funding += payment
            total_payment += payment
        return total_payment

    def close_all_positions(self, candle: Candle) -> List[Fill]:
        """Force-close all positions at candle close. Used at end of backtest.

        Resolves the correct candle for each position's market so that
        cross-market positions (spot + perp) close at their own prices.
        """
        fills = []
        for (venue, market) in list(self._positions.keys()):
            pos = self._positions[(venue, market)]
            opposite = Side.SHORT if pos.side == Side.LONG else Side.LONG
            close_candle = self._resolve_candle(market, candle) or candle
            order = Order(
                market=market, side=opposite, order_type=OrderType.MARKET,
                size=pos.size, order_id=self._next_order_id(), ts=close_candle.ts,
                venue=venue,
            )
            fill = self._fill_model.fill_market(order, close_candle)
            if fill:
                fee = self._fee_model.compute_fee(fill)
                fill = Fill(
                    market=fill.market, side=fill.side, price=fill.price,
                    size=fill.size, fee=fee, ts=fill.ts, order_id=fill.order_id,
                    venue=venue,
                )
                self._apply_fill(fill)
                fills.append(fill)
        return fills

    # --- Internal ---

    def _debit_cash(self, amount: float, venue: str = "default") -> None:
        """Deduct cash — routes through allocator if present."""
        if self._allocator:
            if not self._allocator.debit(venue, amount):
                self._log_messages.append(
                    f"[{self.timestamp}] WARNING: Venue {venue} debit of {amount:.2f} failed — insufficient balance")
            self._cash = self._allocator.total_cash
        else:
            self._cash -= amount

    def _credit_cash(self, amount: float, venue: str = "default") -> None:
        """Add cash — routes through allocator if present."""
        if self._allocator:
            self._allocator.credit(venue, amount)
            self._allocator.track_pnl(venue, amount)
            self._cash = self._allocator.total_cash
        else:
            self._cash += amount

    def _apply_fill(self, fill: Fill) -> None:
        """Update positions and cash based on a fill."""
        self._fills.append(fill)
        self._debit_cash(fill.fee, fill.venue)
        self._total_fees += fill.fee

        market = fill.market
        venue = fill.venue
        key = (venue, market)
        pos = self._positions.get(key)

        if pos is None:
            # Opening a new position
            self._positions[key] = _Position(
                market=market,
                side=fill.side,
                size=fill.size,
                entry_price=fill.price,
                entry_ts=fill.ts,
                venue=venue,
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
                self._credit_cash(pnl, venue)
                self._closed_positions.append({
                    "market": market,
                    "venue": venue,
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
                del self._positions[key]
                if remainder > 0.0001:  # ignore dust positions from float rounding
                    # Flip: open opposite position with remainder
                    self._positions[key] = _Position(
                        market=market,
                        side=fill.side,
                        size=remainder,
                        entry_price=fill.price,
                        entry_ts=fill.ts,
                        venue=venue,
                    )
            else:
                # Partial close
                if pos.side == Side.LONG:
                    pnl = round((fill.price - pos.entry_price) * fill.size, 8)
                else:
                    pnl = round((pos.entry_price - fill.price) * fill.size, 8)
                self._credit_cash(pnl, venue)
                self._closed_positions.append({
                    "market": market,
                    "venue": venue,
                    "side": pos.side.value,
                    "size": fill.size,
                    "entry_price": pos.entry_price,
                    "exit_price": fill.price,
                    "entry_ts": pos.entry_ts,
                    "exit_ts": fill.ts,
                    "pnl": pnl,
                    "funding_paid": 0.0,
                    "partial": True,
                })
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

    @property
    def log_messages(self) -> List[str]:
        return list(self._log_messages)

    # --- Capital allocation (venue balances + transfers) ---

    def venue_balance(self, venue: str) -> float:
        """Get available cash on a specific venue."""
        if self._allocator:
            return self._allocator.available(venue)
        return self._cash

    def venue_balances(self) -> dict:
        """Get all venue balances."""
        if self._allocator:
            return self._allocator.balances
        return {"default": self._cash}

    def transfer(self, from_venue: str, to_venue: str, amount: float) -> bool:
        """Transfer capital between venues. Returns True if initiated."""
        if not self._allocator:
            return False
        t = self._allocator.transfer(from_venue, to_venue, amount, self.timestamp)
        if t is None:
            self._log_messages.append(
                f"[{self.timestamp}] WARNING: Transfer failed — insufficient balance "
                f"on {from_venue} (need ${amount:.0f}, have ${self._allocator.available(from_venue):.0f})"
            )
            return False
        self._cash = self._allocator.total_cash
        self._log_messages.append(
            f"[{self.timestamp}] TRANSFER: ${amount:.0f} {from_venue} → {to_venue} "
            f"(arrives in {(t.arrival_ts - t.initiated_ts)//60}min, cost=${t.cost:.2f})"
        )
        return True

    def process_transfers(self, current_ts: int) -> int:
        """Process arrived transfers. Called by engine each bar. Returns count."""
        if not self._allocator:
            return 0
        arrived = self._allocator.process_arrivals(current_ts)
        if arrived:
            self._cash = self._allocator.total_cash
            for t in arrived:
                self._log_messages.append(
                    f"[{current_ts}] ARRIVED: ${t.amount:.0f} → {t.to_venue}"
                )
        return len(arrived)

    @property
    def capital_allocator(self):
        """Access the capital allocator (for metrics at end of backtest)."""
        return self._allocator
