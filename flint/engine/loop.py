"""The per-bar backtest loop — the honest engine's spine (§6.1).

A backtest walks candles in time order. For each bar the loop runs a **locked**
sequence (§6.1) — the order is correctness, not incidental:

1. **Set the three prices** for the bar (§2.4): the oracle/index drives funding,
   the mark drives liquidation and PnL, the last/close anchors fills.
2. **Fill T+1 market orders at this bar's open.** A market order decided on bar
   *t* becomes eligible at bar *t+1* and executes at its open — never at the
   close of the bar the strategy could see (the classic look-ahead).
3. **Settle ALL funding whose ts falls in this bar, each in ts order** — a wide
   bar over hourly funding applies each hourly settlement individually, never one
   lumped payment.
4. **Check liquidation on the MARK, after funding.** Funding settles first so a
   funding *receipt* can rescue a position a naive close-price check would have
   wrongly liquidated — and a *debit* can correctly push one under (§6.1 worked
   example). This ordering is the whole reason the sequence is locked.
5. **Process resting stop / limit / take-profit orders**, with the intrabar
   adverse-extreme-first policy on Tier-C (OHLCV-only) segments — the pessimistic
   path assumption, and every intrabar-triggered event flagged
   ``intrabar_ambiguous`` in the log so the tearsheet shows how much PnL rests on
   it.
6. **Build ``ctx``, call the strategy, route its signals** through the *shared*
   fill path (the same path paper trading uses), queuing market orders for the
   next bar's open (T+1).
7. **Record + emit** — every fill, funding payment and liquidation is an event on
   the append-only log (§2.10), so the run replays exactly (slice 3.5).

Slice 3.1 ships this spine with an honest Tier-C fill (``NaiveFillModel``), a
flat-maintenance liquidation trigger, and single-settlement funding. Slices 3.2
(CLOB fill fidelity), 3.3 (predicted/final funding + caps + hard gate) and 3.4
(margin tiers, cross/isolated) deepen the steps *without* reordering them — the
sequence above is the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import Callable, Protocol

from flint.core.models import (
    Candle,
    FundingRate,
    MarkSnapshot,
    Order,
    OrderbookSnapshot,
    OrderType,
    Position,
    Side,
    Signal,
    TimeInForce,
)
from flint.core.time import bar_end, last_before
from flint.engine.portfolio import (
    FILL,
    FUNDING,
    LIQUIDATION,
    ORDER_CANCELLED,
    ORDER_PLACED,
    ORDER_REJECTED,
    RUN_FINISHED,
    RUN_STARTED,
    EventLog,
    apply_fill_delta,
)
# EQUITY is imported from the submodule (not re-exported by portfolio/__init__ yet)
# to keep this slice's edits within its fence.
from flint.engine.portfolio.events import EQUITY
from flint.venues import HYPERLIQUID, VenueSpec

from .fills import (
    FillContext,
    FillModel,
    FillResult,
    LatencyModel,
    TradePrint,
    fill_model_for,
)
from .context import AccountView, OpenInterestSnapshot
from .orders import OrderRecord, OrderStatus
from .funding.settlement import (
    clamp_funding_rate,
    settlement_payment,
    settlement_price_for,
)
from .liquidation.cascade import (
    LiquidationDecision,
    adverse_mark,
    plan_liquidations,
)
from .money import ZERO, money
from .signals import (
    SignalValidationError,
    _UsdIntent,
    materialize_usd_intent,
    route_signals,
)
from .state import PortfolioState

# ``SignalValidationError`` and ``_UsdIntent`` moved to ``signals.py`` (§6.0, D29);
# re-exported here so ``from flint.engine.loop import SignalValidationError`` and the
# ``flint.engine`` re-export keep working unchanged.
__all__ = [
    "BacktestEngine",
    "EngineConfig",
    "EngineContext",
    "NoopStrategy",
    "SignalValidationError",
    "Strategy",
]


@dataclass(frozen=True)
class EngineConfig:
    """The engine's locked-but-calibratable defaults for a run.

    ``maint_frac`` is retained only for back-compat: since slice 3.4 the
    liquidation check reads the venue's size-tiered maintenance off
    ``VenueSpec.liquidation`` (§6.5, D14), so this flat fraction is no longer
    consulted. Fees, latency, tick, and the oracle band are likewise venue law on
    the ``VenueSpec``, not here. ``rng_seed`` seeds ``ctx.rng`` — and the
    fill-model latency draws — so a run is deterministic.
    """

    maint_frac: float = 0.025  # superseded by VenueSpec.liquidation tiers (§6.5)
    rng_seed: int = 0


class Strategy(Protocol):
    """The minimal strategy seam the loop calls (slice 3.6 formalises it).

    ``on_candle`` receives the just-closed candle and a read-only ``ctx`` and
    returns a list of ``Signal``s (empty = do nothing). Slice 3.6 builds the full
    ``ctx`` visibility contract (§8.2) and the Signal→Order conversion rules
    (§8.1); 3.1 needs only the call seam so the loop is end-to-end.
    """

    def on_candle(self, candle: Candle, ctx: "EngineContext") -> list[Signal]:
        ...


class NoopStrategy:
    """A strategy that never trades — the default for engine-mechanics tests."""

    def on_candle(self, candle: Candle, ctx: "EngineContext") -> list[Signal]:
        return []


@dataclass
class EngineContext:
    """The read-only window a strategy sees, under the §8.2 visibility contract.

    Every accessor obeys one rule: **strictly less than bar start, closed data
    only, ``None`` over stale, never synthetic** (D26). ``now`` is the bar start,
    and each accessor returns the most recent datum with ``ts < now`` — what a
    trader actually knew when deciding this bar. A missing/stale datum is ``None``
    (a fidelity gap), never a forward-filled or invented value. The ctx is a value
    object: it holds no path to config, secrets, or the store (§8.3); ``submit_order``
    reaches the engine only through a narrow order-submitting callable.
    """

    state: PortfolioState
    default_venue: str
    now: int
    rng: Random
    venue_spec: VenueSpec
    submit_order_fn: Callable[[Order], object]
    funding: dict[str, list[FundingRate]] = field(default_factory=dict)
    marks: dict[str, list[MarkSnapshot]] = field(default_factory=dict)
    books: dict[str, list[OrderbookSnapshot]] = field(default_factory=dict)
    oi: dict[str, list[OpenInterestSnapshot]] = field(default_factory=dict)
    candle_history: dict[str, list[Candle]] = field(default_factory=dict)

    def position(self, market: str, venue: str | None = None) -> Position | None:
        """The open position on ``(venue or default, market)``, or ``None``."""
        return self.state.position(venue or self.default_venue, market)

    def account(self, venue: str | None = None) -> AccountView:
        """A per-venue equity snapshot + the §8.1 sizing helpers (§8.2, §6.5/§6.6).

        Equity and the cross-margin pool are valued on marks known at bar start
        (the visibility contract); a position whose market has no as-of mark simply
        contributes no unrealized (it cannot be valued this bar).
        """
        v = venue or self.default_venue
        as_of = self._marks_as_of(v)
        return AccountView(
            venue=v,
            equity=self.state.equity(v, as_of),
            cross_margin_available=self.state.cross_margin_available(v, as_of),
        )

    def funding_rate(
        self, market: str, venue: str | None = None
    ) -> FundingRate | None:
        """Last **published predicted** funding rate knowable at bar start (§6.4).

        Returns the most recent ``predicted`` row with ``ts < now`` — what a trader
        knew when deciding. It **never** returns the ``final`` rate that settles
        later in the bar: leaking the settled rate inflates funding-arb backtests,
        and blocking that leak here (where a look-ahead linter can't see it) is the
        whole point of the predicted/final split. ``None`` if none published yet.
        """
        predicted = [
            r for r in self.funding.get(market, []) if r.rate_type == "predicted"
        ]
        return last_before(predicted, self.now, key=lambda r: r.ts)

    def basis_bps(self, market: str, venue: str | None = None) -> float | None:
        """Perp premium vs index from the last MarkSnapshot before bar start (§8.2).

        ``None`` when no mark is knowable yet (Tier-C / gap) — never a stale or
        synthetic premium.
        """
        snap = last_before(self.marks.get(market, []), self.now, key=lambda m: m.ts)
        return snap.basis_bps if snap is not None else None

    def open_interest(self, market: str, venue: str | None = None) -> float | None:
        """Last open-interest reading before bar start, or ``None`` (§8.2)."""
        snap = last_before(self.oi.get(market, []), self.now, key=lambda o: o.ts)
        return snap.open_interest if snap is not None else None

    def orderbook(
        self, market: str, venue: str | None = None
    ) -> OrderbookSnapshot | None:
        """Last L2 snapshot before bar start, **within** the staleness threshold (§8.2/§6.3).

        A snapshot older than the venue's ``book_staleness_s`` at bar start is
        treated as absent → ``None`` (a fidelity gap), never a stale book handed
        out as if fresh.
        """
        snap = last_before(self.books.get(market, []), self.now, key=lambda b: b.ts)
        if snap is None:
            return None
        if (self.now - snap.ts) > self.venue_spec.book_staleness_s * 1000:
            return None  # stale beyond threshold → absent, not silently fresh
        return snap

    def candles(
        self, market: str, lookback: int, venue: str | None = None
    ) -> list[Candle]:
        """The last ``lookback`` **closed** bars for ``market`` (bar_end ≤ now, §8.2).

        Closed bars only — the current, not-yet-closed bar is excluded, and gaps
        show up as missing bars, never forward-filled.
        """
        closed = [
            c
            for c in self.candle_history.get(market, [])
            if bar_end(c.ts, c.resolution_s) <= self.now
        ]
        return closed[-lookback:] if lookback > 0 else []

    def submit_order(
        self,
        market: str,
        venue: str,
        side: Side,
        size: float,
        *,
        type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        tif: TimeInForce | None = None,
        margin_mode: str = "cross",
        reduce_only: bool = False,
        client_order_id: str = "",
    ) -> object:
        """The imperative escape hatch (§8.1, D21): submit an order directly.

        Same Order model, same engine fill path, same caps as a Signal — but the
        look-ahead linter reasons about Signals, not this, so imperative strategies
        earn a tearsheet note. Routes through the engine's idempotent submit seam.
        """
        order = Order(
            market=market,
            venue=venue,
            side=side,
            type=type,
            size=size,
            price=price,
            client_order_id=client_order_id,
            tif=tif or (TimeInForce.IOC if type is OrderType.MARKET else TimeInForce.GTC),
            margin_mode=margin_mode,
            reduce_only=reduce_only,
        )
        return self.submit_order_fn(order)

    def _marks_as_of(self, venue: str) -> dict[str, float]:
        """As-of mark price per market this venue holds a position in (bar start).

        Prefers the last MarkSnapshot before ``now``; falls back to the last closed
        candle's close. Markets with neither are omitted (unvaluable this bar).
        """
        out: dict[str, float] = {}
        for (pos_venue, market) in self.state.positions:
            if pos_venue != venue or market in out:
                continue
            snap = last_before(self.marks.get(market, []), self.now, key=lambda m: m.ts)
            if snap is not None:
                out[market] = snap.mark_price
                continue
            candle = last_before(
                self.candle_history.get(market, []), self.now, key=lambda c: c.ts
            )
            if candle is not None:
                out[market] = candle.close
        return out


class BacktestEngine:
    """Walks candles through the locked per-bar sequence (§6.1)."""

    def __init__(
        self,
        event_log: EventLog,
        *,
        config: EngineConfig | None = None,
        fill_model: FillModel | None = None,
        state: PortfolioState | None = None,
        venue_spec: VenueSpec | None = None,
    ) -> None:
        self._log = event_log
        self._cfg = config or EngineConfig()
        self._spec = venue_spec or HYPERLIQUID
        # The fill model is chosen by the venue's market structure (§6.3); a
        # caller may inject one (tests pin NaiveFillModel for pure mechanics).
        self._fill = fill_model or fill_model_for(self._spec.structure)
        self._latency = LatencyModel(self._spec.latency)
        self.state = state or PortfolioState()
        self._rng = Random(self._cfg.rng_seed)
        # Recorded microstructure + observation fixtures, keyed by market (set on
        # run()); ctx exposes them under the §8.2 as-of visibility contract.
        self._books: dict[str, list[OrderbookSnapshot]] = {}
        self._trades: dict[str, list[TradePrint]] = {}
        self._marks: dict[str, list[MarkSnapshot]] = {}
        self._oi: dict[str, list[OpenInterestSnapshot]] = {}
        # Closed-bar history per market, grown as bars finish (ctx.candles reads it).
        self._candle_history: dict[str, list[Candle]] = {}
        # Market orders wait one bar and fill at the next bar's open (T+1).
        self._pending_market: list[Order] = []
        # size_usd intents wait for the execution bar, where they size at its OPEN
        # (§8.1 rule 1 — sizing at the decision bar's close is a hidden look-ahead).
        self._pending_usd: list[_UsdIntent] = []
        # Resting stop/limit/TP orders live until triggered, cancelled, or the
        # run ends (GTC).
        self._resting: list[Order] = []
        # The persisted order state machine (§6.2), keyed by client_order_id — the
        # idempotency ledger: a re-submitted id is recognized here, never doubled.
        self._orders: dict[str, OrderRecord] = {}
        self._coid = 0  # auto client-order-id counter for engine-originated orders
        # Last representative (bar-closing) mark per (venue, market) — lets the
        # cross-pool liquidation check value positions on markets other than the
        # one whose candle is being processed this bar (§6.5).
        self._last_mark: dict[tuple[str, str], float] = {}

    # -- public entry point ----------------------------------------------------

    def run(
        self,
        candles: list[Candle],
        *,
        marks: dict[str, list[MarkSnapshot]] | None = None,
        funding: dict[str, list[FundingRate]] | None = None,
        books: dict[str, list[OrderbookSnapshot]] | None = None,
        trades: dict[str, list[TradePrint]] | None = None,
        oi: dict[str, list[OpenInterestSnapshot]] | None = None,
        strategy: Strategy | None = None,
    ) -> PortfolioState:
        """Run the loop over ``candles`` and return the final portfolio state.

        ``marks``/``funding``/``books``/``trades``/``oi`` are keyed by market, each
        ascending by ts. All are plain in-memory fixtures — the engine is pure
        domain and does no I/O (§6): data arrives already loaded. Supplying
        ``books`` (and ``trades``) lifts fills to Tier B (A); with only candles,
        fills are the honest Tier-C parametric model. ``marks``/``oi`` also feed the
        strategy's §8.2 accessors under the as-of visibility contract.
        """
        self._marks = marks or {}
        funding = funding or {}
        self._books = books or {}
        self._trades = trades or {}
        self._oi = oi or {}
        strat = strategy or NoopStrategy()
        self._log.emit(RUN_STARTED, {"engine": "backtest"})
        for candle in candles:
            self._process_bar(candle, self._marks, funding, strat)
        self._cancel_working_orders_at_run_end()
        self._log.emit(RUN_FINISHED, {"engine": "backtest"})
        return self.state

    def _cancel_working_orders_at_run_end(self) -> None:
        """Cancel every still-working order so none is left non-terminal (§8.1/§19.2).

        GTC limits rest "until cancelled or the run ends" (§8.1); an unfilled T+1
        market order whose execution bar never arrived is likewise dangling. Both
        are cancelled to a terminal state, and any unmaterialized size_usd intent
        is dropped — so the state machine closes and the §19.2 conservation
        invariant holds.
        """
        for order in list(self._resting) + list(self._pending_market):
            self._cancel(order, reason="run_ended")
        self._resting = []
        self._pending_market = []
        self._pending_usd = []

    # -- the locked per-bar sequence (§6.1) ------------------------------------

    def _process_bar(
        self,
        candle: Candle,
        marks: dict[str, list[MarkSnapshot]],
        funding: dict[str, list[FundingRate]],
        strategy: Strategy,
    ) -> None:
        venue = candle.venue
        market = candle.market
        start = candle.ts
        end = bar_end(start, candle.resolution_s)
        market_marks = marks.get(market, [])
        bar_marks = [m for m in market_marks if start <= m.ts < end]

        # (2) T+1: market orders decided last bar fill at this bar's open.
        self._fill_pending_market(candle, market_marks)
        # (3) Funding: every settlement in [start, end), each in ts order.
        self._settle_funding(candle, funding.get(market, []), market_marks, end)
        # (4) Liquidation on the mark, AFTER funding.
        self._check_liquidations(candle, bar_marks)
        # (5) Resting stop/limit/TP — adverse-extreme-first on Tier-C.
        self._process_resting(candle, bar_marks, market_marks)
        # (6) Strategy sees a read-only ctx, returns signals; route them. The ctx
        # exposes every accessor as-of ``start`` (bar start) — closed data only.
        ctx = EngineContext(
            state=self.state,
            default_venue=venue,
            now=start,
            rng=self._rng,
            venue_spec=self._spec,
            submit_order_fn=self._submit,
            funding=funding,
            marks=self._marks,
            books=self._books,
            oi=self._oi,
            candle_history=self._candle_history,
        )
        signals = strategy.on_candle(candle, ctx)
        self._route(signals, venue)
        # (7) This bar is now closed — add it to history so a LATER bar's ctx can
        # see it (never the current bar, which is still open at decision time).
        self._candle_history.setdefault(market, []).append(candle)
        # (8) Emit the per-bar equity snapshot. Placed LAST in the locked sequence:
        # every cash/position move for this bar (T+1 fills, funding, liquidation,
        # resting fills) has settled, and step (6) only queued future orders. The
        # event is additive/derived — it moves no cash and replay.fold ignores it.
        self._emit_equity(candle.ts)

    def _emit_equity(self, ts: int) -> None:
        """Emit the §6.1 per-bar EQUITY snapshot from ``PortfolioState`` (additive).

        Portfolio-wide totals at the bar boundary: ``cash`` (free collateral across
        accounts), ``unrealized`` (mark-to-market of open positions at each market's
        bar-closing mark), ``accrued_funding`` (cumulative funding settled to date —
        already inside ``cash``, see ``events.EQUITY``), and ``equity = cash +
        unrealized``. Money is ``Decimal``-as-str; unrealized is a float snapshot
        (§5: prices/mark-to-market are float) converted once here at the boundary.
        A position on a market with no recorded mark yet contributes zero (it cannot
        be valued this bar — never invented, D26).
        """
        cash = ZERO
        accrued_funding = ZERO
        for acct in self.state.accounts.values():
            cash += acct.cash
            accrued_funding += acct.funding_paid
        unrealized = 0.0
        for key, pos in self.state.positions.items():
            mark = self._last_mark.get(key)
            if mark is not None:
                unrealized += pos.unrealized_pnl(mark)
        unrealized_money = money(unrealized)
        self._log.emit(
            EQUITY,
            {
                "equity": str(cash + unrealized_money),
                "unrealized": str(unrealized_money),
                "accrued_funding": str(accrued_funding),
                "cash": str(cash),
            },
            ts=ts,
        )

    # -- (2) T+1 market fills --------------------------------------------------

    def _fill_pending_market(
        self, candle: Candle, market_marks: list[MarkSnapshot]
    ) -> None:
        """Fill queued market orders at this bar's open (their T+1 moment).

        size_usd intents are *materialized* here first — sized to base units at
        this (execution) bar's open, then placed — so a USD-notional order fills
        alongside base-sized T+1 orders at the same open (§8.1 rule 1).
        """
        self._materialize_usd_intents(candle)
        if not self._pending_market:
            return
        still_pending: list[Order] = []
        for order in self._pending_market:
            if order.market != candle.market or order.venue != candle.venue:
                still_pending.append(order)  # a different market's bar
                continue
            ctx = self._fill_ctx(candle, candle.open, order, market_marks)
            result = self._fill.fill(order, ctx)
            if result is None:  # nothing filled (FOK unmet, zero-vol, band) → reject
                self._reject(order, reason="no_fill")
                continue
            self._apply_fill(order, result)
            # Market orders are IOC: any remainder left after a partial fill is
            # cancelled, carrying the order to a terminal state (§6.2).
            rec = self._orders.get(order.client_order_id)
            if rec is not None and rec.status is OrderStatus.PARTIAL:
                self._cancel(order, reason="ioc_remainder")
        self._pending_market = still_pending

    # -- (3) funding -----------------------------------------------------------

    def _settle_funding(
        self,
        candle: Candle,
        rates: list[FundingRate],
        market_marks: list[MarkSnapshot],
        end: int,
    ) -> None:
        """Settle each *final* funding row whose ts falls in [bar_start, bar_end).

        Only ``final`` rates settle — the ``predicted`` series is what the strategy
        saw when deciding (``ctx.funding_rate``) and never becomes a payment (§6.4
        predicted/final contract). Each settlement in the bar applies individually
        in ts order (a wide bar over hourly funding is never one lumped payment).
        The rate is clamped to the venue cap, priced on the ``price_basis`` price
        at the settlement second, and scaled by the settlement's own ``interval_s``.
        """
        key = (candle.venue, candle.market)
        settlements = sorted(
            (
                r
                for r in rates
                if candle.ts <= r.ts < end and r.rate_type == "final"
            ),
            key=lambda r: r.ts,
        )
        for rate in settlements:
            pos = self.state.positions.get(key)
            if pos is None:
                continue  # only positions open at the settlement ts are charged
            price = settlement_price_for(rate, market_marks)  # hard-gate: no silent close
            capped_rate, was_capped = clamp_funding_rate(
                rate.rate_hourly, self._spec.rate_cap_hourly
            )
            amount = settlement_payment(pos, capped_rate, price, rate.interval_s)
            acct = self.state.account(candle.venue)
            acct.credit(amount)
            acct.funding_paid += amount
            self._log.emit(
                FUNDING,
                {
                    "market": candle.market,
                    "venue": candle.venue,
                    "rate_hourly": rate.rate_hourly,
                    "settled_rate_hourly": capped_rate,
                    "rate_capped": was_capped,
                    "interval_s": rate.interval_s,
                    "price_basis": rate.price_basis,
                    "rate_type": rate.rate_type,
                    "oracle_price": price,
                    "size": pos.size,
                    "amount": str(amount),
                },
                ts=rate.ts,
            )

    # -- (4) liquidation -------------------------------------------------------

    def _check_liquidations(
        self, candle: Candle, bar_marks: list[MarkSnapshot]
    ) -> None:
        """Mark-based liquidation on this venue, after funding (§6.5).

        Builds this venue's ``key -> (mark, path-ambiguous)`` map — the current
        market at its adverse in-bar extreme, every other position at its carried
        close mark — then delegates the isolated close-whole + cross-pool cascade to
        the pure :func:`plan_liquidations` (§6.5) and applies each decision in order.
        The engine stays the thin applier; the §6.5 economics live in
        ``liquidation/cascade.py``, shared verbatim with the Nautilus engine (§6.0).
        """
        venue = candle.venue
        curkey = (venue, candle.market)
        # Best-available mark (+ path-ambiguity) per position on this venue.
        marks: dict[tuple[str, str], tuple[float, bool]] = {}
        curpos = self.state.positions.get(curkey)
        if curpos is not None:
            marks[curkey] = adverse_mark(curpos.side, candle, bar_marks)
        for key in self.state.positions:
            if key[0] == venue and key != curkey and key in self._last_mark:
                marks[key] = (self._last_mark[key], True)  # carried → path assumed

        decisions = plan_liquidations(
            self.state.positions,
            self.state.account(venue).cash,
            marks,
            self._spec,
            venue,
            candle.ts,
        )
        for decision in decisions:
            self._apply_liquidation(decision)

        # Carry a representative (bar-closing) mark forward for next bar.
        self._last_mark[curkey] = (
            bar_marks[-1].mark_price if bar_marks else candle.close
        )

    def _apply_liquidation(self, decision: LiquidationDecision) -> None:
        """Apply one planned liquidation: credit the loss, drop the position, emit.

        The realized amount and the full LIQUIDATION payload were computed purely by
        :func:`plan_liquidations`; this only mutates the account + position book and
        writes the event — the same three effects the legacy inline ``_liquidate``
        had, in the same order (§6.5, §19.2).
        """
        venue = decision.key[0]
        acct = self.state.account(venue)
        acct.credit(money(decision.realized))
        acct.realized_pnl += money(decision.realized)
        del self.state.positions[decision.key]
        self._log.emit(LIQUIDATION, decision.payload, ts=decision.ts)

    # -- (5) resting orders ----------------------------------------------------

    def _process_resting(
        self,
        candle: Candle,
        bar_marks: list[MarkSnapshot],
        market_marks: list[MarkSnapshot],
    ) -> None:
        """Trigger resting stop/limit/TP orders, adverse (stops) first on Tier-C."""
        if not self._resting:
            return
        ambiguous = not bar_marks  # OHLCV-only segment → path assumed
        triggered = [
            o
            for o in self._resting
            if o.market == candle.market
            and o.venue == candle.venue
            and self._is_triggered(o, candle)
        ]
        # Adverse-extreme-first: a protective stop is assumed to fire before a
        # favourable take-profit / limit within the same bar (§6.1 D11).
        triggered.sort(key=lambda o: 0 if o.type is OrderType.STOP else 1)
        for order in triggered:
            ctx = self._fill_ctx(candle, order.price, order, market_marks)
            result = self._fill.fill(order, ctx)
            if result is None:
                continue  # e.g. a maker whose queue has not cleared → keeps resting
            filled = result.fill.size
            if filled < order.size - 1e-12:  # partial: shrink and keep resting
                order.size -= filled
            else:
                self._resting.remove(order)
            self._apply_fill(order, result, intrabar_ambiguous=ambiguous)

    @staticmethod
    def _is_triggered(order: Order, candle: Candle) -> bool:
        """Tier-C price-cross trigger test for a resting order."""
        long_side = order.side is Side.LONG
        if order.type is OrderType.LIMIT:
            return candle.low <= order.price if long_side else candle.high >= order.price
        if order.type is OrderType.STOP:
            # buy-stop triggers above; sell-stop (protective long) triggers below.
            return candle.high >= order.price if long_side else candle.low <= order.price
        if order.type is OrderType.TAKE_PROFIT:
            return candle.low <= order.price if long_side else candle.high >= order.price
        return False

    # -- (6) routing signals ---------------------------------------------------

    def _route(self, signals: list[Signal], default_venue: str) -> None:
        """Route a bar's signals through the pure §8.1 conversion rules.

        :func:`route_signals` owns the loud validation and the signal→order/intent
        mapping (§8.1); this applies its ordered decisions — submitting resolved
        orders (assigning coids exactly as before) and queuing deferred ``size_usd``
        intents. The routing logic is shared verbatim with the Nautilus bar-lane
        shim (§6.0); only the submission side stays engine-owned.
        """
        for decision in route_signals(
            signals,
            default_venue,
            lambda venue, market: self.state.positions.get((venue, market)),
        ):
            if isinstance(decision, _UsdIntent):
                self._pending_usd.append(decision)
            else:
                self._submit(self._new_order(**decision.as_order_kwargs()))

    def _materialize_usd_intents(self, candle: Candle) -> None:
        """Size + place any size_usd intents for this bar's market at its OPEN (§8.1).

        :func:`materialize_usd_intent` does the pure sizing (floor to the venue lot,
        never grown past the USD budget, sub-lot residual returned); this places the
        resolved order and records the residual on ORDER_PLACED, rejecting a
        zero-size intent (no size). Market intents join this bar's fill queue; limit
        intents rest.
        """
        if not self._pending_usd:
            return
        remaining: list[_UsdIntent] = []
        for intent in self._pending_usd:
            if intent.market != candle.market or intent.venue != candle.venue:
                remaining.append(intent)
                continue
            request, residual = materialize_usd_intent(
                intent, candle.open, self._spec.size_decimals
            )
            order = self._new_order(**request.as_order_kwargs())
            self._place(order, size_usd=intent.size_usd, size_residual=residual)
            if request.size <= 0:
                self._reject(order, reason="size_rounds_to_zero")
            elif intent.limit_price > 0:
                self._resting.append(order)
            else:
                self._pending_market.append(order)
        self._pending_usd = remaining

    # -- the persisted order state machine (§6.2) ------------------------------

    def _submit(self, order: Order) -> OrderRecord:
        """Register ``order`` through the persisted state machine, idempotently (§6.2).

        A ``client_order_id`` already known is recognized and returned unchanged —
        never re-queued or double-filled (the paper-reconnect guard). A fresh order
        is placed (ORDER_PLACED) and joins the shared fill path: a market order
        waits for the next bar's open (T+1); a resting order lives until triggered,
        cancelled, or the run ends.
        """
        existing = self._orders.get(order.client_order_id)
        if existing is not None:
            return existing  # idempotent duplicate — already in the machine
        rec = self._place(order)
        if order.type is OrderType.MARKET:
            self._pending_market.append(order)
        else:
            self._resting.append(order)
        return rec

    def _place(self, order: Order, **placed_extra: object) -> OrderRecord:
        """Accept an order onto the machine: pending → placed, ORDER_PLACED emitted.

        Placement only — the caller queues the order (``_submit`` for signals,
        ``_materialize_usd_intents`` for USD orders it fills the same bar).
        """
        rec = OrderRecord(
            client_order_id=order.client_order_id,
            market=order.market,
            venue=order.venue,
            side=order.side,
            type=order.type,
            size=order.size,
            price=order.price,
        )
        self._orders[order.client_order_id] = rec
        rec.transition(OrderStatus.PLACED)
        self._emit_placed(order, **placed_extra)
        return rec

    def _reject(self, order: Order, *, reason: str) -> None:
        """Carry ``order`` to the terminal ``rejected`` state and emit (§6.2)."""
        rec = self._orders.get(order.client_order_id)
        if rec is None or rec.is_terminal:
            return
        rec.transition(OrderStatus.REJECTED, reason=reason)
        self._emit_order_terminal(ORDER_REJECTED, order, reason)

    def _cancel(self, order: Order, *, reason: str) -> None:
        """Carry ``order`` to the terminal ``cancelled`` state and emit (§6.2)."""
        rec = self._orders.get(order.client_order_id)
        if rec is None or rec.is_terminal:
            return
        rec.transition(OrderStatus.CANCELLED, reason=reason)
        self._emit_order_terminal(ORDER_CANCELLED, order, reason)

    def _emit_order_terminal(self, kind: str, order: Order, reason: str) -> None:
        self._log.emit(
            kind,
            {
                "client_order_id": order.client_order_id,
                "market": order.market,
                "venue": order.venue,
                "reason": reason,
            },
        )

    # -- shared fill application -----------------------------------------------

    def _apply_fill(
        self, order: Order, result: FillResult, *, intrabar_ambiguous: bool = False
    ) -> None:
        """Apply a fill to cash + the position book and emit a FILL event.

        The position math is ``apply_fill_delta`` — the *same* reducer
        ``replay.fold`` uses — so the live book and a folded book cannot diverge
        (§2.10). The fill also advances the order's state machine (§6.2) and the
        FILL event carries ``margin_mode`` (v2) so fold rebuilds the position's
        cross/isolated tag from the event alone.
        """
        fill = result.fill
        acct = self.state.account(fill.venue)
        acct.cash -= money(fill.fee)
        acct.fees_paid += money(fill.fee)
        realized = apply_fill_delta(
            self.state.positions,
            (fill.venue, fill.market),
            side=fill.side,
            size=fill.size,
            price=fill.price,
            margin_mode=order.margin_mode,
        )
        if realized:
            acct.credit(money(realized))
            acct.realized_pnl += money(realized)
        rec = self._orders.get(fill.client_order_id)
        if rec is not None and not rec.is_terminal:
            rec.apply_fill(fill.size)
        self._log.emit(
            FILL,
            {
                "client_order_id": fill.client_order_id,
                "market": fill.market,
                "venue": fill.venue,
                "side": fill.side,
                "price": fill.price,
                "size": fill.size,
                "fee": fill.fee,
                "liquidity": fill.liquidity,
                "fidelity_tier": fill.fidelity_tier,
                "slippage_bps": fill.slippage_bps,
                "is_partial": fill.is_partial,
                "margin_mode": order.margin_mode,
                "realized_pnl": str(money(realized)),
                "intrabar_ambiguous": intrabar_ambiguous,
                "flags": list(result.flags),
            },
            ts=fill.ts,
            event_version=2,
        )

    # -- small helpers ---------------------------------------------------------

    def _fill_ctx(
        self,
        candle: Candle,
        reference_price: float,
        order: Order,
        market_marks: list[MarkSnapshot],
    ) -> FillContext:
        """Assemble the fill context at the order's effective time (§6.3).

        Effective time = submit + a seeded latency draw; the book and oracle are
        selected as-of that time, so the fill sees the market as it had moved, not
        as the strategy saw it. With no recorded book the model runs Tier-C.
        """
        spec = self._spec
        submit_ts = candle.ts
        effective_ts = submit_ts + int(self._latency.draw_ms(self._rng))
        book, stale = self._book_as_of(order.market, effective_ts)
        end = bar_end(candle.ts, candle.resolution_s)
        return FillContext(
            reference_price=reference_price,
            ts=submit_ts,
            effective_ts=effective_ts,
            taker_fee_rate=spec.taker_fee_rate,
            maker_fee_rate=spec.maker_fee_rate,
            book=book,
            trades=self._trades_in_bar(order.market, candle.ts, end),
            oracle_price=self._oracle_as_of(market_marks, effective_ts, candle.close),
            oracle_band_bps=spec.oracle_band_bps,
            stale_book=stale,
            queue_ahead=self._queue_ahead(book, order),
            bar_dollar_volume=candle.volume * candle.open,
            half_spread_bps=spec.default_half_spread_bps,
            impact_k=spec.tier_c_impact_k,
            price_sig_figs=spec.price_sig_figs,
        )

    def _book_as_of(
        self, market: str, effective_ts: int
    ) -> tuple[OrderbookSnapshot | None, bool]:
        """The book at-or-before ``effective_ts`` and whether it is stale (§6.3)."""
        snap = last_before(
            self._books.get(market, []), effective_ts + 1, key=lambda b: b.ts
        )
        if snap is None:
            return None, False
        stale = (effective_ts - snap.ts) > self._spec.book_staleness_s * 1000
        return snap, stale

    def _trades_in_bar(
        self, market: str, start: int, end: int
    ) -> tuple[TradePrint, ...]:
        return tuple(
            t for t in self._trades.get(market, []) if start <= t.ts < end
        )

    @staticmethod
    def _oracle_as_of(
        market_marks: list[MarkSnapshot], effective_ts: int, fallback: float
    ) -> float:
        snap = last_before(market_marks, effective_ts + 1, key=lambda m: m.ts)
        return snap.index_price if snap is not None else fallback

    @staticmethod
    def _queue_ahead(book: OrderbookSnapshot | None, order: Order) -> float:
        """Resting size ahead of a maker limit at its price (Tier-A/B queue)."""
        if book is None or order.type is not OrderType.LIMIT:
            return 0.0
        levels = book.bids if order.side is Side.LONG else book.asks
        return sum(size for price, size in levels if price == order.price)

    def _new_order(self, **kwargs) -> Order:
        coid = kwargs.pop("client_order_id", "") or self._next_coid()
        return Order(client_order_id=coid, **kwargs)

    def _next_coid(self) -> str:
        self._coid += 1
        return f"coid-{self._coid}"

    def _emit_placed(self, order: Order, **extra: object) -> None:
        """Emit ORDER_PLACED. ``extra`` carries USD-order provenance (size_usd +
        sub-lot residual) so the tearsheet shows sizing was honest, never grown."""
        payload = {
            "client_order_id": order.client_order_id,
            "market": order.market,
            "venue": order.venue,
            "side": order.side,
            "type": order.type,
            "size": order.size,
            "price": order.price,
            "tif": order.tif,
            "reduce_only": order.reduce_only,
        }
        payload.update(extra)
        self._log.emit(ORDER_PLACED, payload)
