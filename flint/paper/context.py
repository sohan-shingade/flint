"""PaperContext — unified state owner + strategy-facing API for paper trading.

D-2.1.d. Replaces both `PaperBroker` (in-memory state owner: positions,
cash, fees, orders) and `LiveContext` (strategy-facing wrapper) with
one class that mirrors `BacktestContext`'s 7-manager decomposition
post-D-2.1.b.

Composition:
- `_pm` PositionManager — positions keyed by `(venue, market)` — fixes
  the same-market opposite-leg arb bug (Gap 2 of paper-multivenue-funding
  spec)
- `_cm` CashManager — cash + allocator + counters (fees, tx_costs, funding)
- `_fr` FillRecorder — fill list + diagnostic log
- `_oq` OrderQueue — pending limits/stops/TPs (no market queue — paper
  fills market orders synchronously per candle, unlike backtest's per-bar
  queue model)
- `_fl` FundingLedger — per-market + per-venue funding history (live-fed
  from `flint.paper.engine` funding loop)
- `_bl` BorrowLedger — Jupiter borrow rates + paid ledger (placeholder
  for future Jupiter integration; not actively populated by paper engine
  yet)
- `_mdf` MarketDataFeed — cross-market candle access via store-backed
  reads (the `get_candles(market, lookback)` strategy-facing helper)

Key differences from BacktestContext:
- `process_candle(candle)` is owned here (paper executes per-candle on
  live data; backtest threads through a different engine loop)
- No PortfolioMarginEngine pre-trade gauntlet by default — paper uses
  RiskGuard for *post*-execution checks. Future slice can compose the
  orchestrator if multi-strategy paper needs it.
- `mark_price` lives on `_Position` (for live tickers between candles);
  backtest computes PnL on demand from current_candle.

Public surface guarantees back-compat with code that read
`broker.positions.values()`, `broker.cash`, `broker.equity`, etc.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

from ..execution._position import _Position
from ..execution.borrow_ledger import BorrowLedger
from ..execution.cash_manager import CashManager
from ..execution.context import ExecutionContext
from ..execution.fee_models import DriftFeeModel, FeeModel, FlatFeeModel
from ..execution.fill_models import FillModel, FillPipeline, SlippageFill
from ..execution.fill_recorder import FillRecorder
from ..execution.funding_ledger import FundingLedger
from ..execution.market_data_feed import MarketDataFeed
from ..execution.order_queue import OrderQueue
from ..execution.position_manager import PositionManager
from ..models import (
    AccountState,
    Candle,
    Fill,
    Order,
    OrderType,
    PositionInfo,
    Side,
)

logger = logging.getLogger("flint.paper")

# Mirror BacktestContext's pending-order cap.
_PENDING_ORDER_CAP = 100


class PaperContext(ExecutionContext):
    """ExecutionContext for paper trading — state owner + strategy API."""

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        venue: Optional[str] = None,
        capital_allocation: Optional[dict] = None,
        fill_model: Optional[FillModel] = None,
        fee_model: Optional[FeeModel] = None,
        latency_enabled: bool = False,
        store=None,
        resolution_s: int = 3600,
        session_id: str = "",
    ):
        # Resolve the default venue lazily (post-D-1.4 default flipped
        # from "drift" to "hyperliquid" because Drift is offline post-
        # hack — see CLAUDE.md). Pass `venue="drift"` explicitly to
        # restore the old behavior when Drift returns.
        if venue is None:
            from ..config import default_venue
            venue = default_venue()
        # Strategy-facing state ----------------------------------------------
        self._store = store
        self._resolution_s = resolution_s
        self._session_id = session_id
        self._current_candle: Optional[Candle] = None
        self._order_counter = 0

        # Static config -----------------------------------------------------
        self._default_venue = venue
        self.initial_capital = initial_capital
        self._limit_timeout_bars = 24

        # Venue config (margin parameters) ----------------------------------
        self._venue_config = None
        try:
            from ..execution.venue_config import get_venue_config
            self._venue_config = get_venue_config(venue)
        except Exception:
            pass

        # Capital allocator (optional, multi-venue paper) -------------------
        allocator = None
        if capital_allocation:
            from ..execution.capital import VenueAllocator
            allocator = VenueAllocator(capital_allocation)

        # Compose the 7 managers — same shape as BacktestContext ------------
        self._pm = PositionManager()
        self._cm = CashManager(initial_capital, allocator=allocator)
        self._fr = FillRecorder()
        self._oq = OrderQueue()
        self._fl = FundingLedger()
        self._bl = BorrowLedger()
        self._mdf = MarketDataFeed()

        # Fee + fill models -------------------------------------------------
        if fee_model is not None:
            self.fee_model = fee_model
        elif self._venue_config is not None:
            self.fee_model = FlatFeeModel(fee_bps=self._venue_config.taker_fee_bps)
        else:
            self.fee_model = DriftFeeModel()

        if fill_model is not None:
            self.fill_model = fill_model
        elif latency_enabled and self._venue_config is not None:
            self.fill_model = FillPipeline(
                fallback_bps=5.0,
                base_latency_s=self._venue_config.base_latency_s,
                latency_jitter_s=self._venue_config.latency_jitter_s,
            )
        else:
            self.fill_model = SlippageFill(slippage_bps=5.0)

    # ─── ExecutionContext interface ─────────────────────────────────────────

    @property
    def account(self) -> AccountState:
        return AccountState(
            equity=self.equity,
            cash=self.cash,
            unrealized_pnl=self.unrealized_pnl_total,
            margin_used=self.margin_used,
            free_margin=self.free_margin,
            leverage=self.leverage,
        )

    @property
    def positions(self) -> List[PositionInfo]:
        """Strategy-facing positions list. Compatible with the
        ExecutionContext contract — strategies got `List[PositionInfo]`
        from LiveContext and continue to."""
        return [pos.to_info() for pos in self._pm.values()]

    @property
    def pending_orders(self) -> List[Order]:
        return list(self._oq.pending)

    @property
    def current_candle(self) -> Optional[Candle]:
        return self._current_candle

    @property
    def timestamp(self) -> int:
        return self._current_candle.ts if self._current_candle else int(time.time())

    def set_candle(self, candle: Candle) -> None:
        self._current_candle = candle

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"paper-{self._order_counter}"

    def market_order(self, market, side, size, reduce_only=False, tag="", venue="default"):
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.MARKET,
            size=size, order_id=oid, ts=self.timestamp,
        )
        self.submit_order(order)
        return oid

    def limit_order(self, market, side, size, price, reduce_only=False, tag="", venue="default"):
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.LIMIT,
            size=size, price=price, order_id=oid, ts=self.timestamp,
        )
        self.submit_order(order)
        return oid

    def stop_order(self, market, side, size, trigger_price, tag="", venue="default"):
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.STOP_LOSS,
            size=size, price=trigger_price, order_id=oid, ts=self.timestamp,
        )
        self.submit_order(order)
        return oid

    def take_profit_order(self, market, side, size, trigger_price, tag="", venue="default"):
        oid = self._next_order_id()
        order = Order(
            market=market, side=side, order_type=OrderType.TAKE_PROFIT,
            size=size, price=trigger_price, order_id=oid, ts=self.timestamp,
        )
        self.submit_order(order)
        return oid

    def cancel(self, order_id):
        return self.cancel_order(order_id)

    def log(self, message: str) -> None:
        self._fr.log(f"[{self._session_id}] {message}")
        logger.info("[%s] %s", self._session_id, message)

    # ─── Cash / equity / margin properties ───────────────────────────────

    @property
    def cash(self) -> float:
        return self._cm.cash

    @cash.setter
    def cash(self, value: float) -> None:
        # Setter exists for legacy paths that sync allocator total back
        # to broker.cash after a transfer (mirrors PaperBroker behavior).
        self._cm.cash = value

    @property
    def unrealized_pnl_total(self) -> float:
        return sum(p.unrealized_pnl for p in self._pm.values())

    @property
    def equity(self) -> float:
        return self.cash + self.unrealized_pnl_total

    @property
    def margin_used(self) -> float:
        if self._venue_config is None:
            return 0.0
        total = 0.0
        for pos in self._pm.values():
            mark = pos.mark_price if pos.mark_price > 0 else pos.entry_price
            notional = pos.size * mark
            total += notional * self._venue_config.initial_margin
        return total

    @property
    def free_margin(self) -> float:
        return max(0.0, self.equity - self.margin_used)

    @property
    def leverage(self) -> float:
        if self.equity <= 0:
            return 0.0
        total_notional = sum(
            pos.size * (pos.mark_price if pos.mark_price > 0 else pos.entry_price)
            for pos in self._pm.values()
        )
        return total_notional / self.equity

    @property
    def margin_ratio(self) -> float:
        total_notional = sum(
            pos.size * (pos.mark_price if pos.mark_price > 0 else pos.entry_price)
            for pos in self._pm.values()
        )
        if total_notional <= 0:
            return float("inf")
        return self.equity / total_notional

    # ─── Counter aliases (back-compat surface) ───────────────────────────

    @property
    def total_fees(self) -> float:
        return self._cm.total_fees

    @property
    def total_funding(self) -> float:
        return self._cm.total_funding

    @total_funding.setter
    def total_funding(self, value: float) -> None:
        # Resume path writes total_funding directly; mirror that.
        self._cm.total_funding = value

    @property
    def total_tx_costs(self) -> float:
        return self._cm.total_tx_costs

    @property
    def total_borrow_paid(self) -> float:
        return self._bl.total_paid

    @property
    def fills(self) -> List[Fill]:
        return self._fr.fills

    @property
    def closed_trades(self) -> List[dict]:
        return self._pm._closed

    @property
    def venue(self) -> str:
        return self._default_venue

    # ─── Multi-venue surface ─────────────────────────────────────────────

    def venue_balance(self, venue: str) -> float:
        if self._cm.allocator is not None:
            return self._cm.allocator.available(venue)
        return self.cash

    def venue_balances(self) -> dict:
        if self._cm.allocator is not None:
            return dict(self._cm.allocator._balances)
        return {self._default_venue: self.cash}

    def transfer(self, from_venue: str, to_venue: str, amount: float) -> bool:
        if self._cm.allocator is None:
            return False
        t = self._cm.allocator.transfer(from_venue, to_venue, amount, int(time.time()))
        if t:
            self._cm.cash = self._cm.allocator.total_cash
        return t is not None

    def venue_positions(self, venue: str) -> List[PositionInfo]:
        return [pos.to_info() for pos in self._pm.values() if pos.venue == venue]

    def can_open_position(self, size: float, price: float) -> bool:
        if self._venue_config is None:
            return True
        required = size * price * self._venue_config.initial_margin
        return self.free_margin >= required

    def get_liquidation_price(self, market: str) -> float:
        # Aggregate net position across venues for the market — paper
        # before D-2.1.d only had one position per market, so this is
        # the multi-venue-aware equivalent.
        positions = [p for p in self._pm.values() if p.market == market]
        if not positions or self._venue_config is None:
            return 0.0
        # If multiple legs, compute net direction by signed size.
        net = sum(p.size if p.side == Side.LONG else -p.size for p in positions)
        if net == 0:
            return 0.0
        # Use the largest leg's entry as a representative reference.
        rep = max(positions, key=lambda p: p.size)
        mmr = self._venue_config.maintenance_margin
        entry = rep.entry_price
        size_abs = abs(net)
        notional = size_abs * entry
        collateral = self.equity
        if net > 0:  # net long
            return entry - (collateral - mmr * notional) / size_abs if size_abs > 0 else 0
        else:  # net short
            return entry + (collateral - mmr * notional) / size_abs if size_abs > 0 else 0

    # ─── Order submission + cancellation ────────────────────────────────

    def submit_order(self, order: Order) -> str:
        if self._venue_config is not None and not getattr(order, "reduce_only", False):
            price = order.price if order.price else 0
            if price > 0 and not self.can_open_position(order.size, price):
                logger.warning(
                    "Insufficient margin for %s %s %.4f @ %.2f "
                    "(free_margin=%.2f, required=%.2f)",
                    order.side.value, order.market, order.size, price,
                    self.free_margin,
                    order.size * price * self._venue_config.initial_margin,
                )
        self._oq.add_pending(order)
        return order.order_id

    def cancel_order(self, order_id: str) -> bool:
        return self._oq.cancel(order_id)

    def cancel_all(self, market: Optional[str] = None) -> int:
        return self._oq.cancel_all(market)

    # ─── Candle processing — the paper-engine fill loop ──────────────────

    def _is_valid_price(self, price: float, market: str) -> bool:
        if price <= 0 or price > 1_000_000_000:
            return False
        # If we have an existing position on this market (any venue),
        # reject prices that move >100x from entry.
        for pos in self._pm.values():
            if pos.market == market and pos.entry_price > 0:
                ratio = price / pos.entry_price
                if ratio > 100 or ratio < 0.01:
                    return False
        return True

    def process_candle(self, candle: Candle) -> List[Fill]:
        """Expire timed-out pending orders, fill remaining ones against
        the candle, mark-to-market open positions on the candle's market."""
        if not self._is_valid_price(candle.close, candle.market):
            logger.warning(
                "Rejecting candle with suspect price: %s close=%.2f (ts=%d)",
                candle.market, candle.close, candle.ts,
            )
            return []

        self._current_candle = candle

        # Expire timed-out limit/stop orders
        if self._limit_timeout_bars > 0:
            expired = []
            for order in list(self._oq.pending):
                if order.order_type != OrderType.MARKET and order.ts > 0:
                    age_bars = (candle.ts - order.ts) // max(self._resolution_s, 1)
                    if age_bars >= self._limit_timeout_bars:
                        expired.append(order)
            for order in expired:
                self._oq.cancel(order.order_id)
                logger.debug(
                    "Order %s expired after %d bars",
                    order.order_id, self._limit_timeout_bars,
                )

        # Fill against the candle
        fills: List[Fill] = []
        remaining: List[Order] = []
        for order in list(self._oq.pending):
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
                    venue=getattr(fill, "venue", "default"),
                )
                self._apply_fill(fill, candle)
                fills.append(fill)
            else:
                remaining.append(order)

        # Atomic swap of pending list (preserves cancel semantics).
        self._oq.pending = remaining

        # Mark-to-market every position on this candle's market.
        self._pm.update_pnl_for_market(candle.market, candle.close)

        return fills

    # ─── Fill application ────────────────────────────────────────────────

    def _apply_fill(self, fill: Fill, candle: Optional[Candle] = None) -> None:
        venue = fill.venue if fill.venue and fill.venue != "default" else self._default_venue
        self._fr.record(fill)
        self._cm.debit(fill.fee, venue)
        self._cm.add_fee(fill.fee)

        key: Tuple[str, str] = (venue, fill.market)
        pos = self._pm.get(key)

        if pos is None:
            # Open new
            new_pos = _Position(
                market=fill.market, side=fill.side, size=fill.size,
                entry_price=fill.price, entry_ts=fill.ts, venue=venue,
                entry_order_id=fill.order_id,
            )
            new_pos.mark_price = fill.price
            self._pm.set(key, new_pos)
            return

        if pos.side == fill.side:
            # DCA same side
            total_cost = pos.entry_price * pos.size + fill.price * fill.size
            pos.size += fill.size
            pos.entry_price = total_cost / pos.size if pos.size else 0.0
            return

        # Opposite side — close (full or partial)
        if fill.size >= pos.size:
            # Full close (and possibly flip)
            if pos.side == Side.LONG:
                pnl = (fill.price - pos.entry_price) * pos.size
            else:
                pnl = (pos.entry_price - fill.price) * pos.size
            self._cm.credit(pnl, venue)
            self._pm.record_close({
                **pos.to_dict(),
                "exit_price": fill.price,
                "exit_ts": fill.ts,
                "pnl": pnl,
                "entry_order_id": pos.entry_order_id,
                "exit_order_id": fill.order_id,
            })
            self._pm.delete(key)
            # Flip remainder
            remainder = fill.size - pos.size
            if remainder > 0:
                flipped = _Position(
                    market=fill.market, side=fill.side, size=remainder,
                    entry_price=fill.price, entry_ts=fill.ts, venue=venue,
                    entry_order_id=fill.order_id,
                )
                flipped.mark_price = fill.price
                self._pm.set(key, flipped)
            return

        # Partial close (legacy paper behavior — no closed-trade record
        # for the partial portion; matches PaperBroker pre-D-2.1.d).
        if pos.side == Side.LONG:
            pnl = (fill.price - pos.entry_price) * fill.size
        else:
            pnl = (pos.entry_price - fill.price) * fill.size
        self._cm.credit(pnl, venue)
        pos.size -= fill.size

    # ─── Funding application — multi-venue per-leg ───────────────────────

    def apply_funding(self, market: str, rate: float, mark_price: float,
                      venue: Optional[str] = None) -> float:
        """Apply funding payment to all open legs on `market`.

        - `venue=None` → applies to every leg on the market regardless
          of venue (single-venue back-compat path; PaperBroker's
          original semantics).
        - `venue="drift"` (etc.) → applies only to the leg on that
          venue. Used by the multi-venue paper funding loop in
          `flint/paper/engine.py:_apply_session_funding`.

        Returns the total payment booked (positive=paid, negative=received).
        """
        total_payment = 0.0
        for (v, m), pos in list(self._pm.items()):
            if m != market:
                continue
            if venue is not None and v != venue:
                continue
            notional = pos.size * mark_price
            payment = notional * rate
            if pos.side == Side.LONG:
                self._cm.debit(payment, v)
                self._cm.add_funding(payment)
                pos.funding_paid += payment
                total_payment += payment
            else:
                self._cm.credit(payment, v)
                self._cm.add_funding(-payment)
                pos.funding_paid -= payment
                total_payment += -payment
        return total_payment

    # ─── Position close-out (used by RiskGuard liquidation) ──────────────

    def close_all_positions(self, mark_prices: dict) -> None:
        """Force-close all positions at their per-market mark prices."""
        now_ts = int(time.time())
        for key in list(self._pm.keys()):
            pos = self._pm.get(key)
            mark = mark_prices.get(pos.market, pos.entry_price)
            if pos.side == Side.LONG:
                pnl = (mark - pos.entry_price) * pos.size
            else:
                pnl = (pos.entry_price - mark) * pos.size
            self._cm.credit(pnl, pos.venue)
            self._pm.record_close({
                **pos.to_dict(),
                "exit_price": mark,
                "exit_ts": now_ts,
                "pnl": pnl,
                "entry_order_id": pos.entry_order_id,
                "liquidated": True,
            })
            self._pm.delete(key)

    # ─── Strategy-facing data accessors (was LiveContext) ────────────────

    def get_funding_rate(self, market=None):
        rates = self.get_funding_rates(market, lookback=1)
        return rates[-1][1] if rates else None

    def get_funding_rates(self, market=None, lookback=24):
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
        if not self._store:
            return {}
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return {}
        now = int(time.time())
        start = now - lookback * 3600
        try:
            raw = self._store.query_funding_by_venue(mkt, start, now)
            return {
                v: [(e["ts"], e["rate"]) for e in entries]
                for v, entries in raw.items()
            }
        except Exception:
            return {}

    def get_orderbook(self, market=None):
        if not self._store:
            return None
        mkt = market or (self._current_candle.market if self._current_candle else None)
        if not mkt:
            return None
        try:
            now = int(time.time())
            snapshots = self._store.query_orderbook_snapshots(
                mkt, start_ts=now - 3600, end_ts=now,
            )
            return snapshots[-1] if snapshots else None
        except Exception:
            return None

    def get_candles(self, market, lookback=50):
        if not self._store:
            return []
        try:
            candles = self._store.query_candles(market, self._resolution_s)
            return candles[-lookback:] if candles else []
        except Exception:
            return []

    # ─── Position lookup helpers (multi-venue aware) ────────────────────

    def position_at(self, venue: str, market: str) -> Optional[_Position]:
        """Look up a single leg by (venue, market). Returns None if no
        position is open on that exact pairing."""
        return self._pm.get((venue, market))

    def positions_for_market(self, market: str) -> List[_Position]:
        """All legs on `market` regardless of venue. Multi-venue arb
        strategies hold one Drift leg + one HL leg simultaneously here."""
        return [p for p in self._pm.values() if p.market == market]

    def positions_dict(self) -> Dict[Tuple[str, str], dict]:
        """Legacy dict view, keyed by (venue, market). Used by
        session_store persistence and some test assertions."""
        return {key: pos.to_dict() for key, pos in self._pm.items()}

    # ─── Equity history (RiskGuard peak tracking) ───────────────────────

    @property
    def equity_history(self) -> List[float]:
        # RiskGuard appends `broker.equity` snapshots here on every
        # candle for peak tracking. Stored on this instance directly
        # since it's purely a guard-side concern, not a manager domain.
        if not hasattr(self, "_equity_history"):
            self._equity_history = []
        return self._equity_history

    @equity_history.setter
    def equity_history(self, value: List[float]) -> None:
        self._equity_history = list(value)
