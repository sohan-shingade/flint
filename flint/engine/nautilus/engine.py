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
    ClientId,
    LoggingConfig,
    Money,
    OmsType,
    Strategy,
    Venue,
)
from .translate import FlintRecorder


class _NoopHostStrategy(Strategy):
    """The strategy slot — inert in N2, the bar-lane shim's host in N3.

    Holds a reference to the Flint strategy so N3 can subscribe it to bars, route
    its Signals to marketable orders, and fold the resulting fills into the shadow
    book. In N2 it does nothing, so a Noop run trades not at all.
    """

    def __init__(self, flint_strategy: object) -> None:
        super().__init__()
        self._flint_strategy = flint_strategy


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

        # Shadow book — the sole source of accounting; the recorder emits from it.
        shadow = PortfolioState()
        shadow.fund(fund_venue, money(spec.initial_capital))

        engine = BacktestEngine(
            config=BacktestEngineConfig(
                trader_id="FLINT-001",
                logging=LoggingConfig(bypass_logging=True),
            )
        )
        engine.add_venue(
            venue=nautilus_venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USDC,
            starting_balances=[Money(Decimal(spec.initial_capital), USDC)],
        )

        # One instrument + bar type per market; remember precisions for conversion.
        markets = sorted({c.market for c in feed.candles})
        instruments: dict[str, object] = {}
        bar_types = []
        for market in markets:
            instrument = dataconv.build_instrument(spec.venue_spec, market, venue_str)
            engine.add_instrument(instrument)
            instruments[market] = instrument
            bar_types.append(dataconv.bar_type_for(instrument.id, resolution_s))

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
                data = dataconv.predicted_funding_to_data(fr, iid)
                if data is not None:
                    funding_data.append(data)

        engine.add_data(builtin_data, sort=True)
        if funding_data:
            engine.add_data(
                funding_data, client_id=ClientId(dataconv.FLINT_CLIENT_ID), sort=True
            )

        recorder = FlintRecorder(
            event_log=event_log,
            shadow=shadow,
            bar_types=bar_types,
            resolution_s=resolution_s,
            engine_name=self.name,
        )
        engine.add_actor(recorder)
        engine.add_strategy(_NoopHostStrategy(strategy))

        engine.run()
        recorder.reconcile(
            portfolio=engine.portfolio,
            cache=engine.cache,
            venue=nautilus_venue,
            settlement_currency=USDC,
        )
        engine.dispose()
        return shadow


__all__ = ["NautilusEngine"]
