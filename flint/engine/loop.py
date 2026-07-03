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

from dataclasses import dataclass, field, replace
from random import Random
from typing import Protocol

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
    ORDER_PLACED,
    RUN_FINISHED,
    RUN_STARTED,
    EventLog,
)
from flint.venues import HYPERLIQUID, VenueSpec

from .fills import (
    FillContext,
    FillModel,
    FillResult,
    LatencyModel,
    TradePrint,
    fill_model_for,
)
from .funding.settlement import (
    clamp_funding_rate,
    settlement_payment,
    settlement_price_for,
)
from .liquidation.check import (
    bankruptcy_price,
    liquidation_price,
    tiered_maintenance,
)
from .money import money
from .state import PortfolioState


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
    """The read-only window a strategy sees (minimal in 3.1; §8.2 in 3.6).

    Slice 3.1 exposes only position lookup — enough to route ``close`` signals
    and prove the seam. The full accessor set (funding_rate, orderbook, account
    sizing helpers) and the strict as-of visibility contract land in slice 3.6.
    """

    state: PortfolioState
    default_venue: str
    now: int
    rng: Random
    funding: dict[str, list[FundingRate]] = field(default_factory=dict)

    def position(self, market: str, venue: str | None = None) -> Position | None:
        """The open position on ``(venue or default, market)``, or ``None``."""
        return self.state.position(venue or self.default_venue, market)

    def funding_rate(
        self, market: str, venue: str | None = None
    ) -> FundingRate | None:
        """Last **published predicted** funding rate knowable at bar start (§6.4).

        Returns the most recent ``predicted`` row with ``ts < now`` (``now`` is the
        bar start) — what a trader actually knew when deciding this bar. It **never**
        returns the ``final`` rate that settles later in the bar: leaking the settled
        rate inflates funding-arb backtests dramatically, and blocking that leak
        here (where a look-ahead linter can't see it) is the whole point of the
        predicted/final split. ``None`` if no predicted rate has been published yet.
        """
        rows = self.funding.get(market, [])
        predicted = [r for r in rows if r.rate_type == "predicted"]
        return last_before(predicted, self.now, key=lambda r: r.ts)


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
        # Recorded microstructure fixtures, keyed by market (set on run()).
        self._books: dict[str, list[OrderbookSnapshot]] = {}
        self._trades: dict[str, list[TradePrint]] = {}
        # Market orders wait one bar and fill at the next bar's open (T+1).
        self._pending_market: list[Order] = []
        # Resting stop/limit/TP orders live until triggered, cancelled, or the
        # run ends (GTC).
        self._resting: list[Order] = []
        self._coid = 0  # client-order-id counter (slice 3.5 makes it idempotent)
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
        strategy: Strategy | None = None,
    ) -> PortfolioState:
        """Run the loop over ``candles`` and return the final portfolio state.

        ``marks``/``funding``/``books``/``trades`` are keyed by market, each
        ascending by ts. All are plain in-memory fixtures — the engine is pure
        domain and does no I/O (§6): data arrives already loaded. Supplying
        ``books`` (and ``trades``) lifts fills to Tier B (A); with only candles,
        fills are the honest Tier-C parametric model.
        """
        marks = marks or {}
        funding = funding or {}
        self._books = books or {}
        self._trades = trades or {}
        strat = strategy or NoopStrategy()
        self._log.emit(RUN_STARTED, {"engine": "backtest"})
        for candle in candles:
            self._process_bar(candle, marks, funding, strat)
        self._log.emit(RUN_FINISHED, {"engine": "backtest"})
        return self.state

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
        # (6) Strategy sees a read-only ctx, returns signals; route them.
        ctx = EngineContext(
            state=self.state,
            default_venue=venue,
            now=start,
            rng=self._rng,
            funding=funding,
        )
        signals = strategy.on_candle(candle, ctx)
        self._route(signals, venue)

    # -- (2) T+1 market fills --------------------------------------------------

    def _fill_pending_market(
        self, candle: Candle, market_marks: list[MarkSnapshot]
    ) -> None:
        """Fill queued market orders at this bar's open (their T+1 moment)."""
        if not self._pending_market:
            return
        still_pending: list[Order] = []
        for order in self._pending_market:
            if order.market != candle.market or order.venue != candle.venue:
                still_pending.append(order)  # a different market's bar
                continue
            ctx = self._fill_ctx(candle, candle.open, order, market_marks)
            result = self._fill.fill(order, ctx)
            if result is not None:  # IOC: any unfilled remainder is cancelled
                self._apply_fill(order, result)
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

        Isolated positions stand alone against their own ``isolated_margin``;
        cross positions share the venue's cash pool, so one breaching position
        endangers all of them — the cross pool is evaluated across *every* cross
        position on the venue each bar (not per-position) and the largest-loss one
        closes first, repeating until the pool clears maintenance. The current
        market's position is stressed at its adverse in-bar extreme; positions on
        other markets are valued at their last recorded close mark.
        """
        venue = candle.venue
        curkey = (venue, candle.market)
        # Best-available mark (+ path-ambiguity) per position on this venue.
        marks: dict[tuple[str, str], tuple[float, bool]] = {}
        curpos = self.state.positions.get(curkey)
        if curpos is not None:
            marks[curkey] = self._adverse_mark(curpos.side, candle, bar_marks)
        for key in self.state.positions:
            if key[0] == venue and key != curkey and key in self._last_mark:
                marks[key] = (self._last_mark[key], True)  # carried → path assumed

        # Isolated: each closes whole against its own dedicated margin.
        isolated = [
            k
            for k, p in self.state.positions.items()
            if k in marks and p.margin_mode == "isolated"
        ]
        for key in isolated:
            pos = self.state.positions[key]
            mark, ambiguous = marks[key]
            maintenance = tiered_maintenance(pos, mark, self._spec.liquidation)
            equity = pos.isolated_margin + pos.unrealized_pnl(mark)
            if equity < maintenance:
                self._liquidate(
                    key, mark, equity, maintenance,
                    backing=pos.isolated_margin, ambiguous=ambiguous, ts=candle.ts,
                )

        # Cross: shared-pool cascade.
        self._resolve_cross_pool(venue, marks, ts=candle.ts)

        # Carry a representative (bar-closing) mark forward for next bar.
        self._last_mark[curkey] = (
            bar_marks[-1].mark_price if bar_marks else candle.close
        )

    def _resolve_cross_pool(
        self,
        venue: str,
        marks: dict[tuple[str, str], tuple[float, bool]],
        ts: int,
    ) -> None:
        """Deplete the shared cross pool, closing the largest-loss position first.

        Cross pool = venue cash + unrealized PnL across all cross positions; it
        must cover the sum of their maintenance. While it doesn't, the most
        underwater cross position is liquidated and the pool re-evaluated — its
        loss can drag the rest under, exactly the shared-collateral risk a naive
        per-position check hides (§6.5).
        """
        acct = self.state.account(venue)

        def cross_items() -> list[tuple[tuple[str, str], Position, float, bool]]:
            return [
                (key, self.state.positions[key], marks[key][0], marks[key][1])
                for key in list(self.state.positions)
                if key[0] == venue
                and key in marks
                and self.state.positions[key].margin_mode == "cross"
            ]

        while True:
            items = cross_items()
            if not items:
                return
            pool_equity = float(acct.cash) + sum(
                p.unrealized_pnl(m) for _, p, m, _ in items
            )
            pool_maint = sum(
                tiered_maintenance(p, m, self._spec.liquidation)
                for _, p, m, _ in items
            )
            if pool_equity >= pool_maint:
                return  # pool clears maintenance
            key, _pos, mark, ambiguous = min(
                items, key=lambda it: it[1].unrealized_pnl(it[2])
            )
            self._liquidate(
                key, mark, pool_equity, pool_maint,
                backing=float(acct.cash), ambiguous=ambiguous, ts=ts,
            )

    def _liquidate(
        self,
        key: tuple[str, str],
        mark: float,
        equity: float,
        maintenance: float,
        *,
        backing: float,
        ambiguous: bool,
        ts: int,
    ) -> None:
        """Realize a liquidated position, apply the post-trigger loss, emit (§6.5).

        HL charges no clearance fee, so the position realizes at the mark; the
        extra loss comes from the *backstop*: below ``backstop_maint_frac`` ×
        maintenance the HLP vault takes over and the user forfeits remaining
        margin. Under the v1 solvent-fund assumption the vault absorbs any
        sub-bankruptcy gap, so the backing cannot go negative — the covered
        ``insurance_shortfall`` is recorded so the tearsheet can surface it.
        """
        venue, market = key
        pos = self.state.positions[key]
        liq = self._spec.liquidation
        acct = self.state.account(venue)
        m_frac = liq.maint_frac(pos.notional(mark))
        realized = (mark - pos.entry_price) * pos.side.sign * pos.size
        backstop = equity < liq.backstop_maint_frac * maintenance
        shortfall = 0.0
        if backstop and liq.insurance_fund_solvent:
            max_loss = -backing  # realizing this leaves the backing at exactly zero
            if realized < max_loss:
                shortfall = max_loss - realized  # the vault covers this gap
                realized = max_loss
        acct.credit(money(realized))
        acct.realized_pnl += money(realized)
        del self.state.positions[key]
        self._log.emit(
            LIQUIDATION,
            {
                "market": market,
                "venue": venue,
                "side": pos.side,
                "size": pos.size,
                "margin_mode": pos.margin_mode,
                "mark_price": mark,
                "equity": equity,
                "maintenance": maintenance,
                "liq_price": liquidation_price(pos, backing, m_frac),
                "bankruptcy_price": bankruptcy_price(pos, backing),
                "realized_pnl": str(money(realized)),
                "backstop": backstop,
                "insurance_fund_solvent": liq.insurance_fund_solvent,
                "insurance_shortfall": str(money(shortfall)),
                "adl_rank": liq.adl_rank,
                "intrabar_ambiguous": ambiguous,
            },
            ts=ts,
        )

    @staticmethod
    def _adverse_mark(
        side: Side, candle: Candle, bar_marks: list[MarkSnapshot]
    ) -> tuple[float, bool]:
        """The bar's adverse mark for ``side`` + whether the path was assumed.

        With recorded mark snapshots (Tier A/B) the adverse extreme is the worst
        mark in the bar and the timing is known-enough. On OHLCV-only segments
        (Tier C) it falls back to the candle's adverse extreme — low for a long,
        high for a short — and the caller flags the event ``intrabar_ambiguous``
        (§6.1 D11 pessimistic policy).
        """
        if bar_marks:
            prices = [m.mark_price for m in bar_marks]
            return (min(prices) if side is Side.LONG else max(prices)), False
        return (candle.low if side is Side.LONG else candle.high), True

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
        """Convert signals to orders and enqueue them on the shared fill path.

        Slice 3.1 handles the market/close/limit routing needed to prove T+1 and
        the resting path; the full Signal→Order conversion (size_usd sizing at the
        execution bar's open, validation, per-(market,venue,action) dedup — §8.1)
        lands in slice 3.6.
        """
        for sig in signals:
            venue = sig.venue or default_venue
            if sig.is_close:
                pos = self.state.positions.get((venue, sig.market))
                if pos is None:
                    continue
                order = self._new_order(
                    market=sig.market,
                    venue=venue,
                    side=pos.side.opposite,
                    type=OrderType.MARKET,
                    size=pos.size,
                    tif=TimeInForce.IOC,
                    reduce_only=True,
                    margin_mode=pos.margin_mode,
                )
                self._pending_market.append(order)
                self._emit_placed(order)
                continue
            if sig.size <= 0:
                # size_usd sizing needs the next bar's open mark — deferred to
                # slice 3.6 with the sizing helpers; skip in 3.1.
                continue
            side = Side.LONG if sig.action == "long" else Side.SHORT
            if sig.limit_price > 0:
                order = self._new_order(
                    market=sig.market,
                    venue=venue,
                    side=side,
                    type=OrderType.LIMIT,
                    size=sig.size,
                    price=sig.limit_price,
                    tif=sig.tif or TimeInForce.GTC,
                    margin_mode=sig.margin_mode,
                )
                self._resting.append(order)
            else:
                order = self._new_order(
                    market=sig.market,
                    venue=venue,
                    side=side,
                    type=OrderType.MARKET,
                    size=sig.size,
                    tif=TimeInForce.IOC,
                    margin_mode=sig.margin_mode,
                )
                self._pending_market.append(order)
            self._emit_placed(order)

    # -- shared fill application -----------------------------------------------

    def _apply_fill(
        self, order: Order, result: FillResult, *, intrabar_ambiguous: bool = False
    ) -> None:
        """Apply a fill to cash + the position book and emit a FILL event."""
        fill = result.fill
        acct = self.state.account(fill.venue)
        acct.cash -= money(fill.fee)
        acct.fees_paid += money(fill.fee)
        key = (fill.venue, fill.market)
        pos = self.state.positions.get(key)
        realized = 0.0
        if pos is None:
            self.state.positions[key] = Position(
                market=fill.market,
                venue=fill.venue,
                side=fill.side,
                size=fill.size,
                entry_price=fill.price,
                margin_mode=order.margin_mode,
            )
        elif pos.side is fill.side:
            total = pos.size + fill.size
            entry = (pos.entry_price * pos.size + fill.price * fill.size) / total
            self.state.positions[key] = replace(pos, size=total, entry_price=entry)
        else:
            closed = min(pos.size, fill.size)
            realized = (fill.price - pos.entry_price) * pos.side.sign * closed
            acct.credit(money(realized))
            acct.realized_pnl += money(realized)
            remaining = pos.size - fill.size
            if remaining > 0:
                self.state.positions[key] = replace(pos, size=remaining)
            elif remaining == 0:
                del self.state.positions[key]
            else:  # flip: close the old side, open the remainder on the fill side
                self.state.positions[key] = replace(
                    pos, side=fill.side, size=-remaining, entry_price=fill.price
                )
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
                "realized_pnl": str(money(realized)),
                "intrabar_ambiguous": intrabar_ambiguous,
                "flags": list(result.flags),
            },
            ts=fill.ts,
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

    def _emit_placed(self, order: Order) -> None:
        self._log.emit(
            ORDER_PLACED,
            {
                "client_order_id": order.client_order_id,
                "market": order.market,
                "venue": order.venue,
                "side": order.side,
                "type": order.type,
                "size": order.size,
                "price": order.price,
                "tif": order.tif,
                "reduce_only": order.reduce_only,
            },
        )
