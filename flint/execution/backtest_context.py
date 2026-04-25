"""BacktestContext — ExecutionContext implementation for simulated backtesting.

Manages positions, pending orders, fills, and equity tracking.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..models import (
    AccountState,
    BorrowSnapshot,
    Candle,
    Fill,
    FundingRate,
    Order,
    OrderType,
    PositionInfo,
    Side,
    TimeInForce,
)
from .cash_manager import CashManager
from .context import ExecutionContext
from .fee_models import FeeModel, FlatFeeModel
from .fill_models import FillModel, FillPipeline
from .fill_recorder import FillRecorder
from .order_queue import OrderQueue
from .position_manager import PositionManager

logger = logging.getLogger("flint.backtest")


class _Position:
    """Internal mutable position tracker."""

    __slots__ = ("market", "venue", "side", "size", "entry_price", "entry_ts",
                 "unrealized_pnl", "funding_paid", "borrow_cumulative_at_entry")

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
        self.borrow_cumulative_at_entry = 0.0

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
        venue_fill_models: Optional[Dict[str, FillModel]] = None,
    ):
        self._initial_capital = initial_capital
        self._fill_model = fill_model or FillPipeline()
        self._fee_model = fee_model or FlatFeeModel()
        self._position_size_pct = position_size_pct
        self._risk_manager = risk_manager
        self._margin_engine = margin_engine  # Optional: enables margin/liquidation tracking
        self._venue_fill_models: Dict[str, FillModel] = venue_fill_models or {}  # per-venue dispatch

        # D-2.1.b Step 2: cash + running-counters owned by CashManager.
        # `self._cash`, `self._total_fees`, `self._total_tx_costs`,
        # `self._total_funding`, `self._allocator` all remain as
        # property aliases below so compound assignments (-=/+=) at
        # existing call sites keep routing through the manager.
        self._cm = CashManager(initial_capital, allocator=capital_allocator)

        # D-2.1.b Step 1: position state owned by PositionManager.
        # `self._positions` and `self._closed_positions` remain as
        # property aliases below so existing call sites keep working
        # while we migrate Steps 5–7.
        self._pm = PositionManager()

        # D-2.1.b Step 4: pending + market-this-bar order queues owned
        # by OrderQueue. `self._pending_orders` and
        # `self._market_orders_queue` remain as property aliases with
        # setters so existing reassignment idioms (filtering on cancel,
        # swapping during process_pending_orders) keep working.
        self._oq = OrderQueue()

        # D-2.1.b Step 3: recorded fills + diagnostic log messages
        # owned by FillRecorder. Existing call sites use
        # `self._fills.append(...)` and `self._log_messages.append(...)`,
        # which still work through the property aliases below.
        self._fr = FillRecorder()

        self._current_candle: Optional[Candle] = None
        self._order_counter = 0
        self._market_histories: Dict[str, List[Candle]] = {}  # multi-market candle access

        # Funding rate data for strategy access
        self._funding_history: Dict[str, List[FundingRate]] = {}  # market -> [FundingRate]
        self._venue_funding: Dict[str, Dict[str, List[FundingRate]]] = {}  # market -> {venue -> [FundingRate]}

        # Borrow rate data for Jupiter Perps
        self._borrow_history: Dict[str, List[BorrowSnapshot]] = {}  # market -> [BorrowSnapshot]
        self._total_borrow_paid: float = 0.0
        self._borrow_payments: list = []

        # Orderbook data for strategy access and fill models
        self._orderbook_history: Dict[str, List] = {}  # market -> [OrderbookSnapshot]

        # Open interest data for strategy access
        self._oi_history: Dict[str, List] = {}  # market -> [OpenInterest]

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"bt-{self._order_counter}"

    # --- Position state (delegates to PositionManager) ---
    # D-2.1.b Step 1: existing call sites use `self._positions[...]` /
    # `del self._positions[...]` / `self._closed_positions.append(...)`.
    # These properties return the underlying mutable dict/list so those
    # mutations still work. Steps 3–7 migrate the call sites to
    # `self._pm.set/delete/record_close`.

    @property
    def _positions(self) -> Dict[tuple, "_Position"]:
        return self._pm.positions

    @property
    def _closed_positions(self) -> List[dict]:
        return self._pm._closed  # noqa: SLF001 — internal aliasing during step 1

    # --- Cash state (delegates to CashManager) ---
    # D-2.1.b Step 2: existing call sites use `self._cash -= x`,
    # `self._total_fees += f`, etc. These properties read/write the
    # manager's fields directly so compound assignments continue to
    # work without touching every call site.

    @property
    def _cash(self) -> float:
        return self._cm.cash

    @_cash.setter
    def _cash(self, value: float) -> None:
        self._cm.cash = value

    @property
    def _allocator(self):
        return self._cm.allocator

    @property
    def _total_fees(self) -> float:
        return self._cm.total_fees

    @_total_fees.setter
    def _total_fees(self, value: float) -> None:
        self._cm.total_fees = value

    @property
    def _total_tx_costs(self) -> float:
        return self._cm.total_tx_costs

    @_total_tx_costs.setter
    def _total_tx_costs(self, value: float) -> None:
        self._cm.total_tx_costs = value

    @property
    def _total_funding(self) -> float:
        return self._cm.total_funding

    @_total_funding.setter
    def _total_funding(self, value: float) -> None:
        self._cm.total_funding = value

    # --- Fill / log state (delegates to FillRecorder) ---
    # D-2.1.b Step 3: existing call sites do `self._fills.append(...)`
    # and `self._log_messages.append(...)`. These properties return the
    # underlying mutable lists so those mutations still work.

    @property
    def _fills(self) -> List[Fill]:
        return self._fr.fills

    @property
    def _log_messages(self) -> List[str]:
        return self._fr.logs

    # --- Order queues (delegates to OrderQueue) ---
    # D-2.1.b Step 4: existing call sites both append (`.append(order)`)
    # and reassign (`self._pending_orders = [...filtered...]`) on these
    # lists. The properties below provide read+write access so both
    # idioms route through the manager unchanged.

    @property
    def _pending_orders(self) -> List[Order]:
        return self._oq.pending

    @_pending_orders.setter
    def _pending_orders(self, new_list: List[Order]) -> None:
        self._oq.pending = new_list

    @property
    def _market_orders_queue(self) -> List[Order]:
        return self._oq.market_queue

    @_market_orders_queue.setter
    def _market_orders_queue(self, new_list: List[Order]) -> None:
        self._oq.market_queue = new_list

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
                     venue: str = "default",
                     time_in_force=None) -> str:
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.MARKET,
            size=size, order_id=oid, ts=self.timestamp, venue=venue,
            time_in_force=time_in_force or TimeInForce.IOC,
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

    # --- Borrow rate data (Jupiter Perps) ---

    def add_borrow_rate(self, bs: BorrowSnapshot) -> None:
        """Record a borrow rate snapshot for strategy access."""
        mkt = bs.market
        if mkt not in self._borrow_history:
            self._borrow_history[mkt] = []
        self._borrow_history[mkt].append(bs)

    def get_borrow_rate(self, market: str = None, venue: str = None) -> Optional[float]:
        """Get the most recent hourly borrow rate for a market."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return None
        history = self._borrow_history.get(mkt)
        if not history:
            return None
        return history[-1].rate_hourly

    def get_borrow_rates(self, market: str = None, venue: str = None, lookback: int = 24) -> list:
        """Get recent borrow rate history as [(ts, rate_hourly), ...]."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return []
        history = self._borrow_history.get(mkt, [])
        sliced = history[-lookback:] if lookback < len(history) else history
        return [(bs.ts, bs.rate_hourly) for bs in sliced]

    def get_borrow_cumulative_at(self, market: str, ts: int) -> Optional[float]:
        """Get the cumulative borrow rate at or before a timestamp. Returns None if no data."""
        history = self._borrow_history.get(market, [])
        result = None
        for bs in history:
            if bs.ts <= ts:
                result = bs.cumulative_rate
            else:
                break
        return result

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
        """Estimate fill price for a given size, using the same model as the fill pipeline.

        Uses orderbook walk if book data exists, otherwise falls back to the
        sqrt participation model (same tiers as FillPipeline). This ensures
        the pre-trade estimate matches what the fill pipeline will charge.
        """
        if side is None or size <= 0:
            return None

        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return None

        # Use the fill pipeline's impact stage if available
        if hasattr(self._fill_model, '_impact'):
            candle = self._current_candle
            if candle is None:
                return None
            book = self.get_orderbook(mkt)
            from ..models import Order, OrderType
            order = Order(market=mkt, side=side, order_type=OrderType.MARKET,
                          size=size, order_id="estimate")
            result = self._fill_model._impact.compute(order, candle, book)
            return result.fill_price

        # Legacy fallback: orderbook-only walk
        book = self.get_orderbook(mkt)
        if book is None:
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

            fm = self._resolve_fill_model(order.venue)
            fill = None
            if order.order_type in (OrderType.STOP_LOSS, OrderType.TAKE_PROFIT):
                if fm.check_stop_trigger(order, order_candle):
                    # Stop triggers become market orders — fill at candle close,
                    # not the trigger price. In a gap-down the fill can be worse.
                    fill_price = order_candle.close
                    if order.order_type == OrderType.STOP_LOSS:
                        # Stops fill at the worse of trigger or close (slippage through)
                        if order.side == Side.SHORT:  # long stop: selling
                            fill_price = min(order.price, order_candle.close)
                        else:  # short stop: buying
                            fill_price = max(order.price, order_candle.close)
                    else:
                        # Take-profits fill at the better of trigger or close
                        if order.side == Side.SHORT:  # long TP: selling
                            fill_price = max(order.price, order_candle.close)
                        else:  # short TP: buying
                            fill_price = min(order.price, order_candle.close)
                    fill = Fill(
                        market=order.market,
                        side=order.side,
                        price=fill_price,
                        size=order.size,
                        fee=0.0,
                        ts=order_candle.ts,
                        order_id=order.order_id,
                        venue=order.venue,
                    )
            elif order.order_type == OrderType.LIMIT:
                fill = fm.fill_limit(order, order_candle)

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

    def _resolve_fill_model(self, venue: str) -> FillModel:
        """Look up the fill model for a venue, falling back to the default."""
        return self._venue_fill_models.get(venue, self._fill_model)

    def process_market_orders(self, candle: Candle) -> List[Fill]:
        """Process market orders placed during this bar AFTER strategy runs.

        Resolves the correct candle for each order's market, enabling
        cross-market orders (e.g. buy spot while selling perp).

        When venue_fill_models are configured, each order is dispatched to
        its venue-specific fill model. Orders whose venue is not in the map
        fall back to the default fill model.
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
            fm = self._resolve_fill_model(order.venue)
            fill = fm.fill_market(order, order_candle)
            if fill is not None:
                fee = self._fee_model.compute_fee(fill)
                fill = Fill(
                    market=fill.market, side=fill.side, price=fill.price,
                    size=fill.size, fee=fee, ts=fill.ts, order_id=fill.order_id,
                    venue=order.venue,
                    is_partial=fill.is_partial,
                    latency_ms=fill.latency_ms,
                    impact_bps=fill.impact_bps,
                    tx_cost=fill.tx_cost,
                )
                self._apply_fill(fill)
                fills.append(fill)
            # Drain GTC resting orders from pipeline
            if hasattr(fm, 'drain_resting_orders'):
                for resting in fm.drain_resting_orders():
                    if self._check_order_cap():
                        self._pending_orders.append(resting)
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
        Uses per-venue fill model dispatch when venue_fill_models are configured.
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
            fm = self._resolve_fill_model(venue)
            fill = fm.fill_market(order, close_candle)
            if fill:
                fee = self._fee_model.compute_fee(fill)
                fill = Fill(
                    market=fill.market, side=fill.side, price=fill.price,
                    size=fill.size, fee=fee, ts=fill.ts, order_id=fill.order_id,
                    venue=venue,
                    is_partial=fill.is_partial,
                    latency_ms=fill.latency_ms,
                    impact_bps=fill.impact_bps,
                    tx_cost=fill.tx_cost,
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

        if fill.tx_cost > 0:
            self._total_tx_costs += fill.tx_cost
            if self._allocator:
                self._allocator.debit(fill.venue or "default", fill.tx_cost)
            else:
                self._cash -= fill.tx_cost

        market = fill.market
        venue = fill.venue
        key = (venue, market)
        pos = self._positions.get(key)

        if pos is None:
            # Opening a new position
            new_pos = _Position(
                market=market,
                side=fill.side,
                size=fill.size,
                entry_price=fill.price,
                entry_ts=fill.ts,
                venue=venue,
            )
            # Snapshot cumulative borrow rate for Jupiter Perps positions
            if venue == "jupiter":
                cum = self.get_borrow_cumulative_at(
                    market, self._current_candle.ts if self._current_candle else 0)
                if cum is not None:
                    new_pos.borrow_cumulative_at_entry = cum
            self._positions[key] = new_pos
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

                # Realize Jupiter borrow cost on close
                borrow_cost = 0.0
                if venue == "jupiter" and pos.borrow_cumulative_at_entry > 0:
                    cum_now = self.get_borrow_cumulative_at(
                        market, self._current_candle.ts if self._current_candle else 0)
                    if cum_now is not None:
                        size_usd = abs(pos.size * fill.price)
                        borrow_cost = (cum_now - pos.borrow_cumulative_at_entry) * size_usd
                        self._total_borrow_paid += borrow_cost
                        self._borrow_payments.append({
                            "market": market,
                            "ts": fill.ts,
                            "cost": borrow_cost,
                            "cum_entry": pos.borrow_cumulative_at_entry,
                            "cum_exit": cum_now,
                        })
                        self._cash -= borrow_cost

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
                    "borrow_paid": borrow_cost,
                })
                remainder = fill.size - pos.size
                del self._positions[key]
                if remainder > 0.0001:  # ignore dust positions from float rounding
                    # Flip: open opposite position with remainder
                    new_pos = _Position(
                        market=market,
                        side=fill.side,
                        size=remainder,
                        entry_price=fill.price,
                        entry_ts=fill.ts,
                        venue=venue,
                    )
                    # Snapshot cumulative borrow for the new flipped position
                    if venue == "jupiter":
                        cum = self.get_borrow_cumulative_at(
                            market, self._current_candle.ts if self._current_candle else 0)
                        if cum is not None:
                            new_pos.borrow_cumulative_at_entry = cum
                    self._positions[key] = new_pos
            else:
                # Partial close
                if pos.side == Side.LONG:
                    pnl = round((fill.price - pos.entry_price) * fill.size, 8)
                else:
                    pnl = round((pos.entry_price - fill.price) * fill.size, 8)

                # Proportional Jupiter borrow cost on partial close
                borrow_cost = 0.0
                if venue == "jupiter" and pos.borrow_cumulative_at_entry > 0:
                    cum_now = self.get_borrow_cumulative_at(
                        market, self._current_candle.ts if self._current_candle else 0)
                    if cum_now is not None:
                        size_usd = abs(fill.size * fill.price)
                        borrow_cost = (cum_now - pos.borrow_cumulative_at_entry) * size_usd
                        self._total_borrow_paid += borrow_cost
                        self._borrow_payments.append({
                            "market": market,
                            "ts": fill.ts,
                            "cost": borrow_cost,
                            "cum_entry": pos.borrow_cumulative_at_entry,
                            "cum_exit": cum_now,
                        })
                        self._cash -= borrow_cost

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
                    "borrow_paid": borrow_cost,
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
    def total_tx_costs(self) -> float:
        return self._total_tx_costs

    @property
    def total_borrow_paid(self) -> float:
        return self._total_borrow_paid

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
