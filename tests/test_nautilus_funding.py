"""N4 gate — §6.4 funding settlement on the Nautilus lane, byte-for-byte (§A4).

The :class:`~flint.engine.nautilus.funding.FlintFundingModule` settles **final** rows
by wrapping the pure ``funding/settlement.py`` primitives verbatim, so the Nautilus
lane reproduces the legacy loop's FUNDING events and ``accrued_funding`` curve exactly.
The gates here are the plan's N4 gates:

* the §6.4 worked example (long 10 SOL, oracle 100.00, +0.01% → −$0.10) byte-exact,
* the predicted/final divergence golden — the strategy sees only **predicted**
  (``ctx.funding_rate``), money moves only on **final** rows,
* funding across multiple settlements with a position flip between them (position at
  settlement time is correct),
* run-end shadow↔Nautilus reconciliation stays exact with funding flowing,
* module *registration* order governs within-timestep ``process`` order (de-risks N5).

All fixtures are hand-authored (D26). Gated on the ``nautilus`` extra.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytest.importorskip("nautilus_trader")

from flint.adapters import InMemoryUserData  # noqa: E402
from flint.core.models import (  # noqa: E402
    Candle,
    FundingRate,
    MarkSnapshot,
    Signal,
)
from flint.engine.api import EngineFeed, EngineRunSpec  # noqa: E402
from flint.engine.funding.settlement import (  # noqa: E402
    clamp_funding_rate,
    settlement_payment,
)
from flint.engine.loop import EngineConfig  # noqa: E402
from flint.engine.portfolio import EventLog  # noqa: E402
from flint.engine.portfolio.events import EQUITY, FUNDING  # noqa: E402
from flint.engine.select import engine_for  # noqa: E402
from flint.ports import TenantContext  # noqa: E402
from flint.venues import HYPERLIQUID  # noqa: E402

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_040_000
CAP = "100000"
N = 6


def _bar_index(candle: Candle) -> int:
    return round((candle.ts - T0) / HOUR_MS)


# --- hand-authored feed pieces -----------------------------------------------


def _candles() -> list[Candle]:
    # A tiny range around a flat 100.0 so the settlement oracle (a mark, below) is a
    # clean 100.00 while the fill still has a price band to work with.
    return [
        Candle(
            ts=T0 + i * HOUR_MS,
            open=100.0,
            high=100.1,
            low=99.9,
            close=100.0,
            volume=1000.0,
            market=MARKET,
            resolution_s=HOUR_S,
            venue=VENUE,
        )
        for i in range(N)
    ]


def _marks(index_price: float = 100.0) -> dict[str, list[MarkSnapshot]]:
    return {
        MARKET: [
            MarkSnapshot(
                market=MARKET,
                ts=T0 + i * HOUR_MS,
                mark_price=100.0,
                index_price=index_price,
                venue=VENUE,
            )
            for i in range(N)
        ]
    }


def _final(ts: int, rate_hourly: float) -> FundingRate:
    return FundingRate(
        market=MARKET,
        ts=ts,
        rate_hourly=rate_hourly,
        interval_s=HOUR_S,
        price_basis="oracle",
        rate_type="final",
        venue=VENUE,
    )


def _predicted(ts: int, rate_hourly: float) -> FundingRate:
    return FundingRate(
        market=MARKET,
        ts=ts,
        rate_hourly=rate_hourly,
        interval_s=HOUR_S,
        price_basis="oracle",
        rate_type="predicted",
        venue=VENUE,
    )


def _spec() -> EngineRunSpec:
    return EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        initial_capital=CAP,
        fund_venue=VENUE,
        mark_policy="close_derived",
    )


# --- hand-authored strategies ------------------------------------------------


class _OpenLongHold:
    """Long 10 at bar 1 (fills bar 2 open), hold — a position spans the settlements."""

    def on_candle(self, candle, ctx):
        return [Signal.long(MARKET, VENUE, size=10.0)] if _bar_index(candle) == 1 else []


class _FlipMidway:
    """Long 10 at bar 1, flip to short 10 at bar 3 — settlements straddle the flip."""

    def on_candle(self, candle, ctx):
        i = _bar_index(candle)
        if i == 1:
            return [Signal.long(MARKET, VENUE, size=10.0)]
        if i == 3:
            return [Signal.short(MARKET, VENUE, size=20.0)]  # net −10 (flip to short 10)
        return []


class _RecordPredicted:
    """Long 10 at bar 1; every bar record the funding rate the ctx exposes."""

    def __init__(self) -> None:
        self.seen: list[float | None] = []

    def on_candle(self, candle, ctx):
        row = ctx.funding_rate(MARKET, VENUE)
        self.seen.append(row.rate_hourly if row is not None else None)
        return [Signal.long(MARKET, VENUE, size=10.0)] if _bar_index(candle) == 1 else []


# --- runners -----------------------------------------------------------------


def _run(engine_name: str, strategy, feed: EngineFeed):
    log = EventLog(
        InMemoryUserData(), TenantContext.local(), run_id=f"{engine_name}-{id(strategy)}"
    )
    state = engine_for(engine_name)().run(feed, strategy, event_log=log, spec=_spec())
    return log, state


def _rows_without_engine_name(log: EventLog) -> list[dict]:
    rows = []
    for e in log.read():
        row = e.to_row()
        if row["kind"] in ("run_started", "run_finished"):
            row["payload"] = {k: v for k, v in row["payload"].items() if k != "engine"}
        rows.append(row)
    return rows


def _funding_events(log: EventLog) -> list[dict]:
    return [e.payload for e in log.read() if e.kind == FUNDING]


# --- feeds per scenario ------------------------------------------------------


def _feed_6_4() -> EngineFeed:
    """The §6.4 golden: one final settlement of +0.01% on a held long 10 @ oracle 100."""
    return EngineFeed(
        candles=_candles(),
        marks=_marks(index_price=100.0),
        funding={MARKET: [_final(T0 + 3 * HOUR_MS, 0.0001)]},
    )


def _feed_divergence() -> EngineFeed:
    """Predicted rows diverge materially from the final that settles (§6.4 contract)."""
    return EngineFeed(
        candles=_candles(),
        marks=_marks(index_price=100.0),
        funding={
            MARKET: [
                _predicted(T0 + 0 * HOUR_MS, 0.05),
                _predicted(T0 + 1 * HOUR_MS, 0.05),
                _predicted(T0 + 2 * HOUR_MS, 0.05),
                _final(T0 + 3 * HOUR_MS, 0.0001),  # the only row that moves money
            ]
        },
    )


def _feed_multi_flip() -> EngineFeed:
    """Two settlements straddling a long→short flip — charge the position then open."""
    return EngineFeed(
        candles=_candles(),
        marks=_marks(index_price=100.0),
        funding={
            MARKET: [
                _final(T0 + 2 * HOUR_MS, 0.001),  # position is long 10 here
                _final(T0 + 4 * HOUR_MS, 0.001),  # position is short 10 after the flip
            ]
        },
    )


def _feed_capped() -> EngineFeed:
    """A final rate above the HL 4%/hr cap — clamped, and the flag recorded."""
    return EngineFeed(
        candles=_candles(),
        marks=_marks(index_price=100.0),
        funding={MARKET: [_final(T0 + 3 * HOUR_MS, 0.05)]},  # 5%/hr → capped to 4%/hr
    )


_PARITY_CASES = {
    "golden_6_4": (_OpenLongHold, _feed_6_4),
    "predicted_final_divergence": (_OpenLongHold, _feed_divergence),
    "multi_settlement_flip": (_FlipMidway, _feed_multi_flip),
    "rate_capped": (_OpenLongHold, _feed_capped),
}


# --- gates -------------------------------------------------------------------


@pytest.mark.parametrize("case", list(_PARITY_CASES))
def test_funding_matches_legacy_byte_for_byte(case):
    make_strategy, make_feed = _PARITY_CASES[case]
    legacy_log, _ = _run("legacy-bar", make_strategy(), make_feed())
    nautilus_log, _ = _run("nautilus", make_strategy(), make_feed())
    assert _rows_without_engine_name(nautilus_log) == _rows_without_engine_name(legacy_log)


def test_6_4_worked_example_value():
    # The normative §6.4 number: −$0.10 on a long 10 SOL, oracle 100.00, +0.01%/hr.
    log, _ = _run("nautilus", _OpenLongHold(), _feed_6_4())
    funding = _funding_events(log)
    assert len(funding) == 1
    ev = funding[0]
    assert Decimal(ev["amount"]) == Decimal("-0.10")
    assert ev["oracle_price"] == 100.0
    assert ev["size"] == 10.0
    assert ev["settled_rate_hourly"] == 0.0001
    assert ev["rate_capped"] is False
    assert ev["rate_type"] == "final"
    assert ev["price_basis"] == "oracle"
    # The settlement is stamped at the funding row's ts, not the bar boundary.
    settle = next(e for e in log.read() if e.kind == FUNDING)
    assert settle.ts == T0 + 3 * HOUR_MS


def test_strategy_sees_only_predicted_money_moves_only_on_final():
    strat = _RecordPredicted()
    log, state = _run("nautilus", strat, _feed_divergence())
    # ctx.funding_rate only ever returned the published predicted rate (0.05), never
    # the final 0.0001 that settled later — the §6.4 look-ahead the linter can't see.
    seen = [s for s in strat.seen if s is not None]
    assert seen and all(s == 0.05 for s in seen)
    assert 0.0001 not in strat.seen
    # Exactly one settlement, and it used the FINAL rate, not any predicted one.
    funding = _funding_events(log)
    assert len(funding) == 1
    assert funding[0]["settled_rate_hourly"] == 0.0001
    assert Decimal(funding[0]["amount"]) == Decimal("-0.10")
    # accrued_funding on the last EQUITY snapshot equals the single settlement.
    equities = [e.payload for e in log.read() if e.kind == EQUITY]
    assert Decimal(equities[-1]["accrued_funding"]) == Decimal("-0.10")


def test_position_at_settlement_time_after_a_flip():
    log, _ = _run("nautilus", _FlipMidway(), _feed_multi_flip())
    funding = _funding_events(log)
    assert len(funding) == 2
    # Settlement 1 (ts=T0+2h): still long 10 → positive rate means the long pays.
    first = settlement_payment(_Pos(+1, 10.0), 0.001, 100.0, HOUR_S)
    assert Decimal(funding[0]["amount"]) == first
    assert first < 0
    # Settlement 2 (ts=T0+4h): now short 10 → same positive rate, the short receives.
    second = settlement_payment(_Pos(-1, 10.0), 0.001, 100.0, HOUR_S)
    assert Decimal(funding[1]["amount"]) == second
    assert second > 0
    assert funding[1]["size"] == 10.0


def test_rate_cap_clamps_and_records():
    log, _ = _run("nautilus", _OpenLongHold(), _feed_capped())
    ev = _funding_events(log)[0]
    capped, was_capped = clamp_funding_rate(0.05, HYPERLIQUID.rate_cap_hourly)
    assert ev["rate_hourly"] == 0.05  # the raw rate is still reported
    assert ev["settled_rate_hourly"] == capped == 0.04
    assert ev["rate_capped"] is True and was_capped
    # The payment settles on the clamped rate, not the raw 5%/hr.
    assert Decimal(ev["amount"]) == settlement_payment(_Pos(+1, 10.0), 0.04, 100.0, HOUR_S)


def test_reconciliation_exact_with_funding_flowing():
    # A clean return (no InvariantError) proves shadow cash == Nautilus balance at run
    # end with funding moving money on both books; also assert the cash actually moved.
    _, state = _run("nautilus", _OpenLongHold(), _feed_6_4())
    assert state.account(VENUE).funding_paid == Decimal("-0.10")


def test_no_final_rows_registers_no_module():
    # A candle-only / predicted-only feed needs no settlement module; the run is still
    # byte-identical to legacy (no FUNDING events at all).
    feed = EngineFeed(
        candles=_candles(),
        marks=_marks(),
        funding={MARKET: [_predicted(T0, 0.05)]},
    )
    legacy_log, _ = _run("legacy-bar", _OpenLongHold(), feed)
    nautilus_log, _ = _run("nautilus", _OpenLongHold(), feed)
    assert _rows_without_engine_name(nautilus_log) == _rows_without_engine_name(legacy_log)
    assert _funding_events(nautilus_log) == []


# --- N5 de-risk: module registration order == within-timestep process order ---


class _Pos:
    """A minimal position stand-in for the pure ``settlement_payment`` in assertions."""

    def __init__(self, sign: int, size: float) -> None:
        from flint.core.models import Side

        self.side = Side.LONG if sign > 0 else Side.SHORT
        self.size = size


def test_modules_process_in_registration_order():
    """Two stub SimulationModules prove Nautilus runs ``process`` in registration order.

    This is the N5 gate's load-bearing assumption (funding registered before
    liquidation ⇒ funding settles first in the same timestep). Proving it here with
    stubs de-risks N5 before the liquidation module exists.
    """
    from decimal import Decimal as _D

    from flint.engine.nautilus import dataconv
    from flint.engine.nautilus._compat import (
        AccountType,
        BacktestEngine,
        BacktestEngineConfig,
        DataEngineConfig,
        LoggingConfig,
        Money,
        OmsType,
        SimulationModule,
        SimulationModuleConfig,
        USDC,
        Venue,
    )

    calls: list[tuple[int, str]] = []

    class _RecordingModule(SimulationModule):
        def __init__(self, config, *, name, sink):
            super().__init__(config)
            self._flint_name = name
            self._flint_sink = sink

        def process(self, ts_now):
            self._flint_sink.append((ts_now, self._flint_name))

        def log_diagnostics(self, logger):  # pragma: no cover - diagnostics only
            pass

        def reset(self):  # pragma: no cover - single run
            pass

    first = _RecordingModule(
        SimulationModuleConfig(component_id="first-mod"), name="first", sink=calls
    )
    second = _RecordingModule(
        SimulationModuleConfig(component_id="second-mod"), name="second", sink=calls
    )

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id="FLINT-ORD",
            logging=LoggingConfig(bypass_logging=True),
            data_engine=DataEngineConfig(time_bars_build_with_no_updates=False),
        )
    )
    nautilus_venue = Venue(VENUE.upper())
    engine.add_venue(
        venue=nautilus_venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USDC,
        starting_balances=[Money(_D(CAP), USDC)],
        modules=[first, second],  # registration order: first, then second
    )
    instrument = dataconv.build_instrument(HYPERLIQUID, MARKET, VENUE, price_precision=8)
    engine.add_instrument(instrument)
    bar_type = dataconv.bar_type_for(instrument.id, HOUR_S)
    engine.add_data(
        [
            dataconv.candle_to_bar(
                c, bar_type, price_precision=8, size_precision=instrument.size_precision
            )
            for c in _candles()
        ],
        sort=True,
    )
    engine.run()
    engine.dispose()

    assert calls, "modules never processed"
    # Nautilus iterates ``exchange.modules`` in registration order once per settle
    # round; a timestep may settle in several rounds (e.g. end-of-run flushes), so the
    # global call sequence is the registration order [first, second] repeated. Every
    # round therefore runs the first-registered module before the second — the ordering
    # guarantee N5 relies on (funding registered before liquidation ⇒ funding first).
    names = [name for _, name in calls]
    assert len(names) % 2 == 0, f"odd number of module calls: {names}"
    assert names == ["first", "second"] * (len(names) // 2)
