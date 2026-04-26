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
from ._position import _Position
from .borrow_ledger import BorrowLedger
from .cash_manager import CashManager
from .context import ExecutionContext
from .fee_models import FeeModel, FlatFeeModel
from .fill_models import FillModel, FillPipeline
from .fill_recorder import FillRecorder
from .funding_ledger import FundingLedger
from .market_data_feed import MarketDataFeed
from .order_queue import OrderQueue
from .position_manager import PositionManager

logger = logging.getLogger("flint.backtest")


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
        portfolio_risk=None,
        event_log_writer=None,
        event_session_id: Optional[str] = None,
        snapshot_store=None,
        snapshot_every: int = 10_000,
    ):
        self._initial_capital = initial_capital
        self._fill_model = fill_model or FillPipeline()
        self._fee_model = fee_model or FlatFeeModel()
        self._position_size_pct = position_size_pct
        self._risk_manager = risk_manager
        self._margin_engine = margin_engine  # Optional: enables margin/liquidation tracking
        self._portfolio_risk = portfolio_risk  # Optional: book-level pre-trade checks
        self._venue_fill_models: Dict[str, FillModel] = venue_fill_models or {}  # per-venue dispatch

        # D-6.4-replay slice 4: optional event log writer. When both
        # `event_log_writer` and `event_session_id` are supplied, every
        # state-mutating event (order submit/cancel, fill, funding,
        # liquidation, borrow) is appended to the log so replay can
        # reproduce the final state at any target_ts. None on either
        # side disables logging entirely (no overhead in legacy paths).
        self._event_log = event_log_writer
        self._event_session_id = event_session_id
        # D-6.4-replay auto-compaction: track events emitted since the
        # last snapshot. When the count crosses `_snapshot_every`, write
        # a fresh BookState to SnapshotStore and reset the counter.
        # `snapshot_store=None` disables auto-compaction entirely.
        self._snapshot_store = snapshot_store
        self._snapshot_every = max(1, int(snapshot_every))
        self._events_since_snapshot = 0

        # D-3.5-orchestrator: single facade composing the three pre-trade
        # check engines. None when none of margin/allocator/portfolio_risk
        # were supplied — `_check_pretrade()` short-circuits in that case.
        from ..risk.portfolio_orchestrator import PortfolioMarginEngine
        self._pme = PortfolioMarginEngine(
            margin=margin_engine,
            allocator=capital_allocator,
            portfolio=portfolio_risk,
        )

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

        # D-2.1.b Step 7: market data caches (multi-market candles +
        # orderbook + OI snapshots) owned by MarketDataFeed.
        self._mdf = MarketDataFeed()

        # D-2.1.b Step 5: funding rates owned by FundingLedger.
        # Property aliases below preserve the `self._funding_history`
        # / `self._venue_funding` access patterns used by tests and
        # legacy code paths.
        self._fl = FundingLedger()

        # D-2.1.b Step 6: Jupiter borrow rates + paid-borrow ledger
        # owned by BorrowLedger. Property aliases below preserve the
        # `self._borrow_history` / `self._total_borrow_paid` /
        # `self._borrow_payments` access patterns used by `_apply_fill`.
        self._bl = BorrowLedger()

        # Orderbook + OI history live on `self._mdf` (Step 7); read-only
        # property aliases below preserve the legacy attribute names.

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"bt-{self._order_counter}"

    def _emit(self, kind: str, payload: dict, ts: Optional[int] = None) -> None:
        """D-6.4-replay slice 4: append one event to the log when
        logging is enabled. No-op when the writer or session_id are
        unset, so legacy backtests pay zero overhead.

        Auto-compaction (slice 6): when a `_snapshot_store` is wired
        and `_events_since_snapshot` crosses `_snapshot_every`, fold
        the entire log into a fresh BookState and persist it. Replay
        from later target_ts then fast-forwards through the snapshot
        rather than re-folding from seq=0.
        """
        if self._event_log is None or self._event_session_id is None:
            return
        seq = self._event_log.append(
            self._event_session_id,
            ts=ts if ts is not None else self.timestamp,
            kind=kind,
            payload=payload,
        )
        self._events_since_snapshot += 1
        if (
            self._snapshot_store is not None
            and self._events_since_snapshot >= self._snapshot_every
        ):
            self._compact_snapshot(seq)

    def _compact_snapshot(self, seq: int) -> None:
        """Fold the full event log into a BookState and persist it as
        the latest snapshot. Called by `_emit` when the
        events-since-snapshot counter crosses the threshold."""
        from ..portfolio.replay import replay
        try:
            state = replay(
                self._event_log._store,  # noqa: SLF001 — internal store
                self._event_session_id,
                target_ts=2**62,
                initial_capital=self._initial_capital,
                use_snapshot=True,  # honor any earlier snapshot for speed
            )
            self._snapshot_store.write(
                self._event_session_id, seq=int(seq), ts=self.timestamp, state=state,
            )
            self._events_since_snapshot = 0
        except Exception as e:
            logger.debug("snapshot compaction skipped: %s", e)

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

    # --- Funding state (delegates to FundingLedger) ---
    # D-2.1.b Step 5: read-only views onto the ledger's underlying
    # dicts. No call sites mutate these directly today (the Python
    # methods are pure adders), so a read-only alias is sufficient.

    @property
    def _funding_history(self) -> Dict[str, List[FundingRate]]:
        return self._fl.history

    @property
    def _venue_funding(self) -> Dict[str, Dict[str, List[FundingRate]]]:
        return self._fl.venue_history

    # --- Borrow state (delegates to BorrowLedger) ---
    # D-2.1.b Step 6: `_apply_fill` reads `self._borrow_payments` and
    # writes `self._total_borrow_paid += borrow_cost`. Both routes
    # surface here so the existing call sites keep working unchanged.

    @property
    def _borrow_history(self) -> Dict[str, List[BorrowSnapshot]]:
        return self._bl.history

    @property
    def _borrow_payments(self) -> list:
        return self._bl.payments

    @property
    def _total_borrow_paid(self) -> float:
        return self._bl.total_paid

    @_total_borrow_paid.setter
    def _total_borrow_paid(self, value: float) -> None:
        self._bl.total_paid = value

    # --- Market data caches (delegates to MarketDataFeed) ---
    # D-2.1.b Step 7: orderbook + OI + cross-market candle history live
    # on `self._mdf`. Property aliases preserve `self._market_histories`
    # / `self._orderbook_history` / `self._oi_history` access patterns.

    @property
    def _market_histories(self) -> Dict[str, List[Candle]]:
        return self._mdf.market_histories

    @_market_histories.setter
    def _market_histories(self, value: Dict[str, List[Candle]]) -> None:
        self._mdf.market_histories = value

    @property
    def _orderbook_history(self) -> Dict[str, List]:
        return self._mdf.orderbook_history

    @property
    def _oi_history(self) -> Dict[str, List]:
        return self._mdf.oi_history

    # --- ExecutionContext properties ---

    @property
    def account(self) -> AccountState:
        unrealized = sum(p.unrealized_pnl for p in self._pm.values())
        cash = self._cm.cash
        equity = cash + unrealized

        margin_used = 0.0
        leverage = 0.0
        if self._margin_engine and self._pm:
            ms = self._margin_engine.compute_margin_state(cash, self.positions)
            margin_used = ms.total_margin_used
            leverage = ms.leverage

        return AccountState(
            equity=equity,
            cash=cash,
            unrealized_pnl=unrealized,
            margin_used=margin_used,
            free_margin=cash - margin_used,
            leverage=leverage,
        )

    @property
    def positions(self) -> List[PositionInfo]:
        return [p.to_info() for p in self._pm.values()]

    @property
    def pending_orders(self) -> List[Order]:
        return self._oq.snapshot()

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
                for (v, m), p in self._pm.items()
            )
            if not has_opposite:
                self._fr.log(
                    f"[{self.timestamp}] WARNING: reduce_only rejected — no opposite position in {market}"
                )
                return oid

        # D-3.5-orchestrator: single pre-trade check that walks
        # allocator → margin → portfolio book limits. Reduce-only orders
        # skip the gauntlet entirely (closing exposure can't fail
        # margin/book checks when the engine is doing its job).
        if not reduce_only:
            price = self._current_candle.close if self._current_candle else 0
            cash_for_check = self._cm.available(venue)
            check = self._pme.check_order(
                checked, cash_for_check, self.positions, price,
                equity=self.account.equity,
            )
            if not check:
                tag = check.component.upper() or "RISK"
                self._fr.log(f"[{self.timestamp}] {tag} REJECTED: {check.reason}")
                return oid

        self._oq.add_market(checked)
        self._emit("order.submit", {
            "order_id": oid, "market": market, "venue": venue,
            "side": side.value, "size": size, "type": "market",
            "reduce_only": reduce_only,
        })
        return oid

    # D-2.1.b caller-site migration: order placement + cancellation
    # now flow through `self._oq.add_pending` / `self._oq.cancel*`
    # rather than mutating the underlying list directly.

    def _add_pending_or_warn(self, order: Order) -> bool:
        """Push an order onto the resting queue, logging when capped."""
        if not self._oq.add_pending(order):
            self._fr.log(f"[{self.timestamp}] WARNING: 100 pending order cap reached — order dropped")
            return False
        return True

    def limit_order(self, market: str, side: Side, size: float, price: float,
                    reduce_only: bool = False, tag: str = "",
                    venue: str = "default") -> str:
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.LIMIT,
            size=size, price=price, order_id=oid, ts=self.timestamp, venue=venue,
        )
        if not self._add_pending_or_warn(order):
            return ""
        self._emit("order.submit", {
            "order_id": oid, "market": market, "venue": venue,
            "side": side.value, "size": size, "price": price, "type": "limit",
        })
        return oid

    def stop_order(self, market: str, side: Side, size: float,
                   trigger_price: float, tag: str = "",
                   venue: str = "default") -> str:
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.STOP_LOSS,
            size=size, price=trigger_price, order_id=oid, ts=self.timestamp, venue=venue,
        )
        if not self._add_pending_or_warn(order):
            return ""
        self._emit("order.submit", {
            "order_id": oid, "market": market, "venue": venue,
            "side": side.value, "size": size, "trigger_price": trigger_price,
            "type": "stop_loss",
        })
        return oid

    def take_profit_order(self, market: str, side: Side, size: float,
                          trigger_price: float, tag: str = "",
                          venue: str = "default") -> str:
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.TAKE_PROFIT,
            size=size, price=trigger_price, order_id=oid, ts=self.timestamp, venue=venue,
        )
        if not self._add_pending_or_warn(order):
            return ""
        self._emit("order.submit", {
            "order_id": oid, "market": market, "venue": venue,
            "side": side.value, "size": size, "trigger_price": trigger_price,
            "type": "take_profit",
        })
        return oid

    def cancel(self, order_id: str) -> bool:
        ok = self._oq.cancel(order_id)
        if ok:
            self._emit("order.cancel", {"order_id": order_id})
        return ok

    def cancel_all(self, market: Optional[str] = None) -> int:
        n = self._oq.cancel_all(market)
        if n > 0:
            self._emit("order.cancel", {"count": n, "market": market})
        return n

    def log(self, message: str) -> None:
        self._fr.log(f"[{self.timestamp}] {message}")

    # --- Engine-called methods ---

    def set_candle(self, candle: Candle) -> None:
        """Called by engine before strategy runs."""
        self._current_candle = candle
        # Mark every open position to its own market's most recent close.
        # PositionManager.update_pnl_for_market handles the primary
        # market in one shot; cross-market positions still need the
        # explicit history lookup against MarketDataFeed.
        self._pm.update_pnl_for_market(candle.market, candle.close)
        for (_venue, market), pos in self._pm.items():
            if market == candle.market:
                continue
            hist = self._mdf.history_for(market)
            if hist:
                pos.update_pnl(hist[-1].close)

    def set_market_histories(self, histories: Dict[str, List[Candle]]) -> None:
        """Set multi-market candle data for cross-market access."""
        self._mdf.set_histories(histories)

    def get_candles(self, market: str, lookback: int = 50) -> List[Candle]:
        """Get recent candles for any available market."""
        return self._mdf.candles(market, lookback)

    @property
    def markets(self) -> List[str]:
        """List of available markets."""
        return self._mdf.markets()

    def add_funding_rate(self, fr: FundingRate) -> None:
        """Record a funding rate for strategy access. Called by engine."""
        self._fl.add(fr)

    def get_funding_rate(self, market: Optional[str] = None) -> Optional[float]:
        """Get the most recent funding rate for a market."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return None
        return self._fl.latest(mkt)

    def get_funding_rates(self, market: Optional[str] = None, lookback: int = 24) -> list:
        """Get recent funding rate history as [(ts, rate), ...]."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return []
        return self._fl.recent(mkt, lookback)

    def get_funding_by_venue(self, market: Optional[str] = None, lookback: int = 24) -> dict:
        """Get recent funding rates grouped by venue: {venue: [(ts, rate), ...]}."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return {}
        return self._fl.by_venue(mkt, lookback)

    def get_venue_snapshots(self, market: Optional[str] = None, lookback: int = 24) -> dict:
        """Get full FundingRate objects grouped by venue (includes mark/oracle prices)."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return {}
        return self._fl.venue_snapshots(mkt, lookback)

    # --- Borrow rate data (Jupiter Perps) ---

    def add_borrow_rate(self, bs: BorrowSnapshot) -> None:
        """Record a borrow rate snapshot for strategy access."""
        self._bl.record(bs)

    def get_borrow_rate(self, market: str = None, venue: str = None) -> Optional[float]:
        """Get the most recent hourly borrow rate for a market."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return None
        return self._bl.latest(mkt)

    def get_borrow_rates(self, market: str = None, venue: str = None, lookback: int = 24) -> list:
        """Get recent borrow rate history as [(ts, rate_hourly), ...]."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return []
        return self._bl.recent(mkt, lookback)

    def get_borrow_cumulative_at(self, market: str, ts: int) -> Optional[float]:
        """Get the cumulative borrow rate at or before a timestamp. Returns None if no data."""
        return self._bl.cumulative_at(market, ts)

    def add_orderbook_snapshot(self, snapshot) -> None:
        """Record an orderbook snapshot. Called by engine."""
        self._mdf.add_orderbook(snapshot)

    def get_orderbook(self, market: Optional[str] = None):
        """Get the most recent orderbook snapshot for a market."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return None
        return self._mdf.latest_orderbook(mkt)

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
        self._mdf.add_open_interest(oi)

    def get_open_interest(self, market=None):
        """Get most recent (long_oi, short_oi) for a market."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return None
        return self._mdf.latest_oi(mkt)

    def get_open_interest_history(self, market=None, lookback: int = 24) -> list:
        """Get recent OI as [(ts, long_oi, short_oi), ...]."""
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return []
        return self._mdf.oi_recent(mkt, lookback)

    def check_liquidations(self, candle: Candle) -> list:
        """Check all positions for liquidation. Called by engine each bar.

        Returns list of LiquidationEvents. Force-closes liquidated positions.
        """
        if not self._margin_engine or not self._pm:
            return []

        # Build current prices for all markets with positions.
        prices: Dict[str, float] = {candle.market: candle.close}
        for mkt, hist in self._mdf.market_histories.items():
            if hist:
                prices[mkt] = hist[-1].close

        events = self._margin_engine.check_liquidations(
            self._pm.positions, prices, candle.ts,
        )

        for event in events:
            key = (event.venue, event.market)
            pos = self._pm.get(key)
            if pos is None:
                continue

            self._pm.record_close({
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

            # event.loss already includes the penalty.
            self._cm.credit(event.loss, event.venue)
            self._cm.add_fee(event.penalty)
            self._pm.delete(key)

            # Cancel any pending orders against this position.
            self._oq.pending = [
                o for o in self._oq.pending
                if not (o.market == event.market and o.venue == event.venue)
            ]

            self._fr.log(
                f"[{event.ts}] LIQUIDATED: {event.side} {event.size:.4f} {event.market} "
                f"on {event.venue} @ {event.liq_price:.2f} "
                f"(entry={event.entry_price:.2f}, loss=${event.loss:.2f}, penalty=${event.penalty:.2f})"
            )
            self._emit("liquidation", {
                "market": event.market, "venue": event.venue,
                "side": event.side, "size": event.size,
                "entry_price": event.entry_price,
                "liq_price": event.liq_price,
                "loss": event.loss, "penalty": event.penalty,
            }, ts=event.ts)

        return events

    def _resolve_candle(self, market: str, primary_candle: Candle) -> Optional[Candle]:
        """Get the current candle for any market.

        For the primary market, returns the primary candle.
        For other markets, returns the last candle from market histories.
        This enables cross-market execution (e.g. spot hedge + perp).
        """
        if market == primary_candle.market:
            return primary_candle
        history = self._mdf.history_for(market)
        return history[-1] if history else None

    def process_pending_orders(self, candle: Candle) -> List[Fill]:
        """Process stop/limit orders against current candles BEFORE strategy runs.

        Resolves the correct candle for each order's market, enabling
        cross-market stops (e.g. stop-loss on a spot position).
        """
        fills = []
        remaining = []

        for order in self._oq.pending:
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

        self._oq.pending = remaining
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
        for order in self._oq.drain_market():
            order_candle = self._resolve_candle(order.market, candle)
            if order_candle is None:
                self._fr.log(
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
            # Drain GTC resting orders from pipeline.
            if hasattr(fm, "drain_resting_orders"):
                for resting in fm.drain_resting_orders():
                    self._add_pending_or_warn(resting)
        return fills

    def apply_funding(self, funding_rate: FundingRate) -> float:
        """Apply funding payment to all positions in this market. Returns total paid.

        D-2.1.b caller-site migration (post-step-7): explicit
        `self._cm.debit(...)` and `self._cm.add_funding(...)` instead of
        the legacy `self._debit_cash` helper + `self._total_funding +=`
        compound assignment. Position iteration goes through
        `self._pm.items()` so the read path is also explicit.
        """
        market = funding_rate.market
        total_payment = 0.0
        for (venue, m), pos in self._pm.items():
            if m != market:
                continue
            # Use funding record's oracle_price; fall back to current candle close.
            price = funding_rate.oracle_price
            if price <= 0 and self._current_candle:
                price = self._current_candle.close
            notional = pos.size * price
            payment = notional * funding_rate.rate if pos.side == Side.LONG else -notional * funding_rate.rate
            self._cm.debit(payment, venue)
            pos.funding_paid += payment
            self._cm.add_funding(payment)
            self._emit("funding", {
                "market": market, "venue": venue,
                "rate": funding_rate.rate, "payment": payment,
            }, ts=funding_rate.ts)
            total_payment += payment
        return total_payment

    def close_all_positions(self, candle: Candle) -> List[Fill]:
        """Force-close all positions at candle close. Used at end of backtest.

        Resolves the correct candle for each position's market so that
        cross-market positions (spot + perp) close at their own prices.
        Uses per-venue fill model dispatch when venue_fill_models are configured.
        """
        fills = []
        for (venue, market) in list(self._pm.keys()):
            pos = self._pm.get((venue, market))
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

    def _realize_jupiter_borrow_cost(
        self,
        market: str,
        size: float,
        fill_price: float,
        fill_ts: int,
        cum_entry: float,
    ) -> float:
        """Compute + book Jupiter borrow cost on a (partial or full)
        close. Returns the realized cost in USD; 0 when no rate data
        or no entry snapshot."""
        if cum_entry <= 0:
            return 0.0
        cum_now = self._bl.cumulative_at(
            market, self._current_candle.ts if self._current_candle else 0
        )
        if cum_now is None:
            return 0.0
        size_usd = abs(size * fill_price)
        cost = (cum_now - cum_entry) * size_usd
        self._bl.add_paid(cost)
        self._bl.record_payment({
            "market": market,
            "ts": fill_ts,
            "cost": cost,
            "cum_entry": cum_entry,
            "cum_exit": cum_now,
        })
        # Borrow cost is a direct cash deduction, not routed through
        # the allocator (Jupiter Perps lives outside the venue allocator).
        self._cm.cash -= cost
        self._emit("borrow", {
            "market": market, "cost": cost,
            "cum_entry": cum_entry, "cum_exit": cum_now,
        }, ts=fill_ts)
        return cost

    def _new_position(
        self,
        market: str,
        venue: str,
        side: Side,
        size: float,
        entry_price: float,
        entry_ts: int,
    ) -> "_Position":
        """Construct a `_Position` and snapshot the Jupiter borrow
        cumulative at entry when applicable. Used by both opening and
        flip paths in `_apply_fill`."""
        new_pos = _Position(
            market=market, side=side, size=size,
            entry_price=entry_price, entry_ts=entry_ts, venue=venue,
        )
        if venue == "jupiter":
            cum = self._bl.cumulative_at(
                market, self._current_candle.ts if self._current_candle else 0
            )
            if cum is not None:
                new_pos.borrow_cumulative_at_entry = cum
        return new_pos

    def _apply_fill(self, fill: Fill) -> None:
        """Update positions and cash based on a fill.

        D-2.1.b caller-site migration: every state mutation here goes
        through one of the seven managers (no more `self._positions[k]`,
        `self._cash -= x`, `self._total_fees += f` etc.).
        """
        self._fr.record(fill)
        self._cm.debit(fill.fee, fill.venue)
        self._cm.add_fee(fill.fee)

        if fill.tx_cost > 0:
            self._cm.total_tx_costs += fill.tx_cost
            self._cm.debit(fill.tx_cost, fill.venue or "default")

        # D-6.4-replay slice 4: emit fill before mutating positions so
        # replay's fold sees opens/closes in the same order as live.
        self._emit("fill", {
            "order_id": fill.order_id,
            "market": fill.market,
            "venue": fill.venue or "default",
            "side": fill.side.value,
            "size": fill.size,
            "price": fill.price,
            "fee": fill.fee,
            "tx_cost": fill.tx_cost,
        }, ts=fill.ts)

        market = fill.market
        venue = fill.venue
        key = (venue, market)
        pos = self._pm.get(key)

        if pos is None:
            # Opening a new position.
            self._pm.set(key, self._new_position(
                market, venue, fill.side, fill.size, fill.price, fill.ts,
            ))
            return

        if pos.side == fill.side:
            # DCA — average entry into the existing same-side position.
            total_cost = pos.entry_price * pos.size + fill.price * fill.size
            pos.size += fill.size
            pos.entry_price = total_cost / pos.size if pos.size > 0.0001 else fill.price
            return

        # Opposite side — reducing or closing.
        if fill.size >= pos.size:
            # Full close (and possibly flip the remainder).
            if pos.side == Side.LONG:
                pnl = round((fill.price - pos.entry_price) * pos.size, 8)
            else:
                pnl = round((pos.entry_price - fill.price) * pos.size, 8)

            borrow_cost = 0.0
            if venue == "jupiter":
                borrow_cost = self._realize_jupiter_borrow_cost(
                    market, pos.size, fill.price, fill.ts,
                    pos.borrow_cumulative_at_entry,
                )

            self._cm.credit(pnl, venue)
            self._pm.record_close({
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
                "exit_order_id": fill.order_id,
            })
            remainder = fill.size - pos.size
            self._pm.delete(key)
            if remainder > 0.0001:
                # Flip — open the remainder on the opposite side.
                self._pm.set(key, self._new_position(
                    market, venue, fill.side, remainder, fill.price, fill.ts,
                ))
            return

        # Partial close — reduce the existing position in place.
        if pos.side == Side.LONG:
            pnl = round((fill.price - pos.entry_price) * fill.size, 8)
        else:
            pnl = round((pos.entry_price - fill.price) * fill.size, 8)

        borrow_cost = 0.0
        if venue == "jupiter":
            borrow_cost = self._realize_jupiter_borrow_cost(
                market, fill.size, fill.price, fill.ts,
                pos.borrow_cumulative_at_entry,
            )

        self._cm.credit(pnl, venue)
        self._pm.record_close({
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
            "exit_order_id": fill.order_id,
        })
        pos.size -= fill.size

    # --- Results ---

    @property
    def all_fills(self) -> List[Fill]:
        return self._fr.all_fills()

    @property
    def closed_trades(self) -> List[dict]:
        return self._pm.closed

    @property
    def total_fees(self) -> float:
        return self._cm.total_fees

    @property
    def total_funding(self) -> float:
        return self._cm.total_funding

    @property
    def total_tx_costs(self) -> float:
        return self._cm.total_tx_costs

    @property
    def total_borrow_paid(self) -> float:
        return self._bl.total_paid

    @property
    def borrow_payments(self) -> List[dict]:
        """Per-trade Jupiter borrow attribution rows. Used by
        `BacktestEngine.run` to thread the ledger into the result."""
        return list(self._bl.payments)

    @property
    def log_messages(self) -> List[str]:
        return self._fr.messages()

    # --- Capital allocation (venue balances + transfers) ---

    def venue_balance(self, venue: str) -> float:
        """Get available cash on a specific venue."""
        return self._cm.available(venue)

    def venue_balances(self) -> dict:
        """Get all venue balances."""
        return self._cm.balances()

    def transfer(self, from_venue: str, to_venue: str, amount: float) -> bool:
        """Transfer capital between venues. Returns True if initiated."""
        alloc = self._cm.allocator
        if alloc is None:
            return False
        t = alloc.transfer(from_venue, to_venue, amount, self.timestamp)
        if t is None:
            self._fr.log(
                f"[{self.timestamp}] WARNING: Transfer failed — insufficient balance "
                f"on {from_venue} (need ${amount:.0f}, have ${alloc.available(from_venue):.0f})"
            )
            return False
        self._cm.cash = alloc.total_cash
        self._fr.log(
            f"[{self.timestamp}] TRANSFER: ${amount:.0f} {from_venue} → {to_venue} "
            f"(arrives in {(t.arrival_ts - t.initiated_ts)//60}min, cost=${t.cost:.2f})"
        )
        return True

    def process_transfers(self, current_ts: int) -> int:
        """Process arrived transfers. Called by engine each bar. Returns count."""
        alloc = self._cm.allocator
        if alloc is None:
            return 0
        arrived = alloc.process_arrivals(current_ts)
        if arrived:
            self._cm.cash = alloc.total_cash
            for t in arrived:
                self._fr.log(f"[{current_ts}] ARRIVED: ${t.amount:.0f} → {t.to_venue}")
        return len(arrived)

    @property
    def capital_allocator(self):
        """Access the capital allocator (for metrics at end of backtest)."""
        return self._cm.allocator
