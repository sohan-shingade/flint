"""``NautilusEngine`` — the Nautilus-backed :class:`SimulationEngine` (§A3, §6.0).

Assembles a Nautilus ``BacktestEngine`` from a Flint :class:`EngineFeed`: one HL
venue, one ``CryptoPerpetual`` per market, the converted market data, a
:class:`FlintRecorder` (the single EventLog writer + shadow book), and a host
strategy slot. Runs synchronously and returns the shadow :class:`PortfolioState`
after a run-end shadow-vs-Nautilus reconciliation (§19.4).

Nautilus is a domain-internal detail of ``flint/engine/`` — this module is imported
**lazily** from :func:`flint.engine.select.engine_for` so candle-only users never
pay the wheel's import cost. ``flint/engine/__init__`` never imports it.

N2 scope: a Noop run through the real EventLog. The host strategy is inert (it
holds the Flint strategy for the N3 bar-lane shim, which will route signals and
fold fills into the recorder's shadow book). Books are not wired (bar lane; N8).
"""

from __future__ import annotations

from decimal import Decimal

from flint.engine.api import EngineFeed, EngineRunSpec
from flint.engine.money import money
from flint.engine.portfolio import EventLog
from flint.engine.state import PortfolioState

from . import dataconv
from ._compat import (
    USDC,
    AccountType,
    BacktestEngine,
    BacktestEngineConfig,
    BookType,
    ClientId,
    DataEngineConfig,
    LoggingConfig,
    Money,
    OmsType,
    OrderBookDeltas,
    Venue,
)
from .execmodels import FlintPriceFillModel, FlintTagFeeModel, TickFeeModel
from .funding import build_funding_module
from .liquidation import build_liquidation_module
from .shim import BarLaneStrategy
from .ticklane import TickLaneStrategy
from .translate import FlintRecorder

# Bar-lane instrument price precision. Flint prices to 5 significant figures
# (VenueSpec.price_sig_figs); a fixed 8-decimal instrument precision represents
# those exactly for every HL perp priced above ~$0.001 and keeps the Nautilus fill
# price within the §19.4 entry tolerance of Flint's. HL's real rule is
# significant-figure- not decimal-based, so per-asset precision for sub-cent alts is
# a later refinement — the bar-lane markets (majors) are well inside this bound.
_BAR_LANE_PRICE_PRECISION = 8

# Tick-lane defaults. ``on_candle`` aggregates the trade tape at the recorded candle
# resolution when the feed carries candles, else this 60 s fallback; EQUITY is sampled
# on a fixed interval (Nautilus's AccountState is not per-bar, §6.0) at the spec's
# cadence, or this 60 s default when the run leaves it unset.
_TICK_CANDLE_RESOLUTION_S = 60
_TICK_EQUITY_INTERVAL_S = 60


class NautilusEngine:
    """Run a Flint backtest on a Nautilus core (§6.0, D29)."""

    name = "nautilus"

    def run(
        self,
        feed: EngineFeed,
        strategy: object,
        *,
        event_log: EventLog,
        spec: EngineRunSpec,
    ) -> PortfolioState:
        """Dispatch to the bar or tick lane by the strategy's ``lane`` marker (§8.6).

        A :class:`~flint.strategy.tick.TickStrategy` (or its adapter) exposes
        ``lane == "tick"`` and runs on native L2 matching; everything else is a bar
        strategy on the Flint-priced bar lane. Services enforce that a tick strategy
        was routed to ``engine="nautilus"`` before it ever reaches here (§19.1).
        """
        if getattr(strategy, "lane", "bar") == "tick":
            return self._run_tick(feed, strategy, event_log=event_log, spec=spec)
        return self._run_bar(feed, strategy, event_log=event_log, spec=spec)

    def _run_bar(
        self,
        feed: EngineFeed,
        strategy: object,
        *,
        event_log: EventLog,
        spec: EngineRunSpec,
    ) -> PortfolioState:
        if not feed.candles:
            raise ValueError("NautilusEngine requires at least one candle in the feed")

        venues = {c.venue for c in feed.candles}
        if len(venues) != 1:
            raise NotImplementedError(
                f"the N2 skeleton supports a single venue per run, got {sorted(venues)}"
            )
        venue_str = next(iter(venues))
        resolutions = {c.resolution_s for c in feed.candles}
        if len(resolutions) != 1:
            raise NotImplementedError(
                f"the N2 skeleton supports a single resolution per run, got {sorted(resolutions)}"
            )
        resolution_s = next(iter(resolutions))
        fund_venue = spec.fund_venue or venue_str
        nautilus_venue = Venue(venue_str.upper())

        # Shadow book — the sole source of accounting; the recorder emits from it. On
        # a paper warm start (§6.7) adopt the caller's book (cash + open positions +
        # accumulators) as-is; otherwise fund a fresh one at the fund venue. Carried
        # positions are materialized inside Nautilus at the first bar (the shim's
        # ``_materialize_seed``), so the book mirrors the shadow from run start and the
        # run-end reconciliation stays a strict cross-check (see FlintRecorder.reconcile).
        shadow = spec.initial_state
        if shadow is None:
            shadow = PortfolioState()
            shadow.fund(fund_venue, money(spec.initial_capital))

        # The recorder (single EventLog + shadow-book writer) and the funding module
        # are built before the venue: the module needs the recorder to emit FUNDING and
        # move the shadow book, and the venue needs the module in its ``modules`` list so
        # Nautilus wires ``module.exchange`` for ``adjust_account`` (§6.4, §A4).
        recorder = FlintRecorder(
            event_log=event_log,
            shadow=shadow,
            engine_name=self.name,
        )
        funding_module = build_funding_module(
            recorder=recorder,
            shadow=shadow,
            feed=feed,
            venue_spec=spec.venue_spec,
        )
        # Liquidation is registered AFTER funding (§A5, §6.1): the tick lane (N8) runs
        # modules in registration order, so funding settles before the liquidation
        # check in the same timestep — a funding receipt can rescue a position. Unlike
        # funding, liquidation is unconditional (any open position can breach).
        liquidation_module = build_liquidation_module(
            recorder=recorder,
            shadow=shadow,
            venue_spec=spec.venue_spec,
        )
        modules = [
            m for m in (funding_module, liquidation_module) if m is not None
        ] or None

        engine = BacktestEngine(
            config=BacktestEngineConfig(
                trader_id="FLINT-001",
                logging=LoggingConfig(bypass_logging=True),
                # Never fabricate an empty bar for a trade-less interval (D26).
                data_engine=DataEngineConfig(time_bars_build_with_no_updates=False),
            )
        )
        engine.add_venue(
            venue=nautilus_venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USDC,
            # Fund Nautilus with the shadow's cash at the fund venue (Decimal) so its
            # account mirrors the adopted book from the first tick; on a cold start
            # that cash is exactly ``initial_capital``, so this is behavior-neutral.
            starting_balances=[Money(Decimal(shadow.account(fund_venue).cash), USDC)],
            # Flint prices every fill: a synthetic book pinned to Flint's price and a
            # fee model echoing Flint's fee, so Nautilus's account/positions mirror
            # the shadow book (§A7). Synchronous processing so the OrderFilled →
            # FILL translation happens inline in the shim's per-bar sequence.
            fill_model=FlintPriceFillModel(),
            fee_model=FlintTagFeeModel(),
            use_message_queue=False,
            modules=modules,
        )

        # One instrument per market; a raised price precision so Flint's 5-sig-fig
        # fill prices round-trip within the §19.4 entry tolerance (see module const).
        markets = sorted({c.market for c in feed.candles})
        instruments: dict[str, object] = {}
        for market in markets:
            instrument = dataconv.build_instrument(
                spec.venue_spec,
                market,
                venue_str,
                price_precision=_BAR_LANE_PRICE_PRECISION,
            )
            engine.add_instrument(instrument)
            instruments[market] = instrument

        builtin_data: list = []
        funding_data: list = []
        for market in markets:
            instrument = instruments[market]
            iid = instrument.id
            price_precision = instrument.price_precision
            size_precision = instrument.size_precision
            bar_type = dataconv.bar_type_for(iid, resolution_s)
            for candle in feed.candles:
                if candle.market != market:
                    continue
                builtin_data.append(
                    dataconv.candle_to_bar(
                        candle,
                        bar_type,
                        price_precision=price_precision,
                        size_precision=size_precision,
                    )
                )
            for mark in feed.marks.get(market, []):
                builtin_data.extend(
                    dataconv.mark_to_updates(mark, iid, price_precision=price_precision)
                )
            for seq, tp in enumerate(feed.trades.get(market, [])):
                builtin_data.append(
                    dataconv.trade_to_tick(
                        tp,
                        iid,
                        price_precision=price_precision,
                        size_precision=size_precision,
                        seq=seq,
                    )
                )
            for fr in feed.funding.get(market, []):
                data = dataconv.predicted_funding_custom_data(fr, iid)
                if data is not None:
                    funding_data.append(data)

        engine.add_data(builtin_data, sort=True)
        if funding_data:
            engine.add_data(
                funding_data, client_id=ClientId(dataconv.FLINT_CLIENT_ID), sort=True
            )

        shim = BarLaneStrategy(
            flint_strategy=strategy,
            feed=feed,
            recorder=recorder,
            shadow=shadow,
            spec=spec,
            instruments=instruments,
            resolution_s=resolution_s,
            funding=funding_module,
            liquidation=liquidation_module,
        )
        engine.add_actor(recorder)
        engine.add_strategy(shim)

        engine.run()
        # Run-end order cancels (shim-driven, legacy order) precede RUN_FINISHED,
        # then reconcile the shadow book against Nautilus's own account/positions.
        shim.finalize_run()
        recorder.emit_run_finished()
        recorder.reconcile(
            portfolio=engine.portfolio,
            cache=engine.cache,
            venue=nautilus_venue,
            settlement_currency=USDC,
        )
        engine.dispose()
        return shadow

    # -- tick lane (native L2 matching, §8.6/§A7b) -----------------------------

    def _run_tick(
        self,
        feed: EngineFeed,
        strategy: object,
        *,
        event_log: EventLog,
        spec: EngineRunSpec,
    ) -> PortfolioState:
        """Assemble + run the tick lane: native L2 matching, process-driven economics.

        Differs from the bar lane in the venue (an ``L2_MBP`` book so a marketable
        order fills against real depth, a :class:`TickFeeModel` charging native
        maker/taker), the data (trades / quotes / book deltas / marks / predicted
        funding rather than pre-aggregated bars), the strategy (a
        :class:`TickLaneStrategy` hosting the user tick strategy), and the driver
        (funding + liquidation modules built ``lane="tick"`` so their ``process`` runs
        the perp economics, funding before liquidation by registration order). The
        recorder stays the single writer and the run-end reconciliation is identical.
        """
        if spec.initial_state is not None:
            raise ValueError(
                "the tick lane does not support a warm start (initial_state) — paper "
                "resume is bar-lane only (N10); a tick run always starts from a freshly "
                "funded book"
            )
        venue_str = spec.fund_venue or self._infer_tick_venue(feed)
        if not venue_str:
            raise ValueError(
                "the tick lane needs a venue — set spec.fund_venue (no datum in the "
                "feed carries one to infer it from)"
            )
        markets = sorted(self._tick_markets(feed))
        if not markets:
            raise ValueError(
                "the tick lane requires at least one market with trade/quote/book/mark "
                "data in the feed"
            )
        nautilus_venue = Venue(venue_str.upper())
        # ``on_candle`` aggregates the trade tape at the recorded candle resolution
        # when present, else the 60 s default (the aggregation is INTERNAL either way).
        resolution_s = (
            feed.candles[0].resolution_s if feed.candles else _TICK_CANDLE_RESOLUTION_S
        )

        # Shadow book — the sole source of accounting; the recorder emits from it.
        shadow = PortfolioState()
        shadow.fund(venue_str, money(spec.initial_capital))

        # Instruments first: the recorder needs the InstrumentId → (venue, market) map
        # to subscribe to each market's mark-price stream (its carried-mark source).
        instruments: dict[str, object] = {}
        mark_keys: dict[object, tuple[str, str]] = {}
        for market in markets:
            instrument = dataconv.build_instrument(
                spec.venue_spec,
                market,
                venue_str,
                price_precision=_BAR_LANE_PRICE_PRECISION,
            )
            instruments[market] = instrument
            mark_keys[instrument.id] = (venue_str, market)

        recorder = FlintRecorder(
            event_log=event_log,
            shadow=shadow,
            engine_name=self.name,
            equity_interval_s=spec.equity_sample_interval_s or _TICK_EQUITY_INTERVAL_S,
            mark_keys=mark_keys,
        )
        # Funding before liquidation (registration order == process order, §6.1): a
        # funding credit in a timestep can lift a position over maintenance before the
        # liquidation check in the same step.
        funding_module = build_funding_module(
            recorder=recorder,
            shadow=shadow,
            feed=feed,
            venue_spec=spec.venue_spec,
            lane="tick",
        )
        liquidation_module = build_liquidation_module(
            recorder=recorder,
            shadow=shadow,
            venue_spec=spec.venue_spec,
            lane="tick",
        )
        modules = [
            m for m in (funding_module, liquidation_module) if m is not None
        ] or None

        engine = BacktestEngine(
            config=BacktestEngineConfig(
                trader_id="FLINT-001",
                logging=LoggingConfig(bypass_logging=True),
                data_engine=DataEngineConfig(time_bars_build_with_no_updates=False),
            )
        )
        engine.add_venue(
            venue=nautilus_venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USDC,
            starting_balances=[Money(Decimal(spec.initial_capital), USDC)],
            # An L2_MBP book so a marketable untagged order fills against real depth
            # (native matching); FlintPriceFillModel defers untagged orders to that
            # book and pins only the tagged §6.5 liquidation close at its entry price.
            fill_model=FlintPriceFillModel(),
            fee_model=TickFeeModel(),
            book_type=BookType.L2_MBP,
            use_message_queue=False,
            modules=modules,
        )
        for market in markets:
            engine.add_instrument(instruments[market])

        builtin_data: list = []
        book_data: dict[str, list] = {}
        funding_data: list = []
        for market in markets:
            instrument = instruments[market]
            iid = instrument.id
            pp = instrument.price_precision
            sp = instrument.size_precision
            for seq, tp in enumerate(feed.trades.get(market, [])):
                builtin_data.append(
                    dataconv.trade_to_tick(
                        tp, iid, price_precision=pp, size_precision=sp, seq=seq
                    )
                )
            for quote in feed.quotes.get(market, []):
                builtin_data.append(
                    dataconv.quote_to_tick(
                        quote, iid, price_precision=pp, size_precision=sp
                    )
                )
            batches = self._book_deltas_data(feed.book_deltas.get(market, []), iid, pp, sp)
            if batches:
                book_data[market] = batches
            for mark in feed.marks.get(market, []):
                builtin_data.extend(
                    dataconv.mark_to_updates(mark, iid, price_precision=pp)
                )
            for fr in feed.funding.get(market, []):
                data = dataconv.predicted_funding_custom_data(fr, iid)
                if data is not None:
                    funding_data.append(data)

        # Order-book data must be registered per instrument off the *first* datum of
        # its add_data call: Nautilus only inspects ``data[0]`` to populate its
        # ``_has_book_data`` set, so a trade sorted ahead of a book delta in one mixed
        # list would leave the L2_MBP venue believing it has no book (a pre-run
        # InvalidConfiguration). Each market's book deltas therefore go in their own
        # call — a book delta is always first — before the mixed trade/quote/mark data.
        for batches in book_data.values():
            engine.add_data(batches, sort=True)
        if builtin_data:
            engine.add_data(builtin_data, sort=True)
        if funding_data:
            engine.add_data(
                funding_data, client_id=ClientId(dataconv.FLINT_CLIENT_ID), sort=True
            )

        host = TickLaneStrategy(
            tick_strategy=strategy,
            feed=feed,
            recorder=recorder,
            shadow=shadow,
            spec=spec,
            instruments=instruments,
            resolution_s=resolution_s,
            venue=venue_str,
        )
        # The liquidation module flattens the Nautilus position through the host's
        # forced-close callback (it has no Strategy capability of its own, §6.5).
        if liquidation_module is not None:
            liquidation_module.set_close_fn(host.flatten)
        engine.add_actor(recorder)
        engine.add_strategy(host)

        engine.run()
        # Cancel any still-working tick order (the tick-lane analogue of the shim's
        # finalize_run), emit RUN_FINISHED, then reconcile shadow ↔ Nautilus (§19.4).
        recorder.finalize_native()
        recorder.emit_run_finished()
        recorder.reconcile(
            portfolio=engine.portfolio,
            cache=engine.cache,
            venue=nautilus_venue,
            settlement_currency=USDC,
        )
        engine.dispose()
        return shadow

    @staticmethod
    def _tick_markets(feed: EngineFeed) -> set[str]:
        """Every market carrying any tick-lane datum (trade/quote/book/mark/funding/candle)."""
        markets: set[str] = {c.market for c in feed.candles}
        for stream in (
            feed.trades,
            feed.quotes,
            feed.book_deltas,
            feed.marks,
            feed.funding,
        ):
            markets.update(stream.keys())
        return markets

    @staticmethod
    def _infer_tick_venue(feed: EngineFeed) -> str:
        """The venue string a tick feed carries, if any (candles or marks name one)."""
        if feed.candles:
            return feed.candles[0].venue
        for marks in feed.marks.values():
            if marks:
                return marks[0].venue
        return ""

    @staticmethod
    def _book_deltas_data(rows, iid, price_precision: int, size_precision: int) -> list:
        """Flint ``BookDelta`` rows → ``OrderBookDeltas`` batches, one per timestamp.

        The rows are grouped by ts into atomic batches (Nautilus applies an
        ``OrderBookDeltas`` as one book update). A snapshot-row group is prefixed with a
        ``CLEAR`` so it rebuilds the whole book (the Tardis snapshot convention);
        incremental rows apply as UPDATE/DELETE against the standing book.
        """
        ordered = sorted(rows, key=lambda r: (r.ts, r.seq))
        out: list = []
        i = 0
        while i < len(ordered):
            ts = ordered[i].ts
            group = []
            while i < len(ordered) and ordered[i].ts == ts:
                group.append(ordered[i])
                i += 1
            deltas = []
            if group[0].is_snapshot:
                deltas.append(dataconv.book_delta_clear(iid, ts, group[0].seq))
            for bd in group:
                deltas.append(
                    dataconv.book_delta_to_delta(
                        bd,
                        iid,
                        price_precision=price_precision,
                        size_precision=size_precision,
                    )
                )
            out.append(OrderBookDeltas(iid, deltas))
        return out


__all__ = ["NautilusEngine"]
