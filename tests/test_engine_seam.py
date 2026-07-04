"""The engine seam — SimulationEngine conformance + the mark-policy fence (§6.0, N1).

Two proofs the N1 extraction is behavior-neutral:

* **Byte-compare conformance.** The legacy engine reached through the
  :class:`SimulationEngine` seam (``engine_for("legacy-bar")``) writes an event log
  byte-identical to a direct :meth:`BacktestEngine.run` on the same hand-authored
  fixture — a run exercising order routing, T+1 fills, funding settlement, and the
  liquidation cascade, so every moved code path is covered.
* **Mark-policy fence.** ``mark_policy="recorded"`` on a candle-only feed lets **no**
  synthesized mark reach the engine; ``mark_policy="close_derived"`` reproduces
  today's per-market close-mark synthesis exactly.

Hand-authored inputs only (D26).
"""

from __future__ import annotations

import pytest

from flint.adapters import InMemoryUserData
from flint.core.models import Candle, FundingRate, MarkSnapshot, Position, Side, Signal
from flint.data.ranges import Kind
from flint.engine import BacktestEngine, EngineConfig, PortfolioState, money
from flint.engine.api import EngineFeed, EngineRunSpec
from flint.engine.portfolio import LIQUIDATION, EventLog
from flint.engine.select import LegacyBarEngine, UnknownEngineError, engine_for
from flint.ports import TenantContext
from flint.services._engine_inputs import build_engine_feed, build_engine_inputs
from flint.venues import HYPERLIQUID

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_000_000


def _bar(ts, open_, high, low, close) -> Candle:
    return Candle(
        ts=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
        market=MARKET,
        resolution_s=HOUR_S,
        venue=VENUE,
    )


def _scenario_feed() -> EngineFeed:
    """A run touching routing, T+1 fills, funding, and the liquidation cascade.

    bar0: strategy opens a leveraged long (fills at bar1 open). bar1: a final funding
    settlement is charged. bar2: the mark collapses and the position is liquidated.
    """
    candles = [
        _bar(T0, 100.0, 101.0, 99.0, 100.0),
        _bar(T0 + HOUR_MS, 100.0, 101.0, 99.0, 100.0),
        _bar(T0 + 2 * HOUR_MS, 100.0, 101.0, 60.0, 65.0),
    ]
    funding = {
        MARKET: [
            FundingRate(
                market=MARKET,
                ts=T0 + HOUR_MS + 12 * 60 * 1000,  # minute 12 of bar1
                rate_hourly=0.001,
                interval_s=HOUR_S,
                price_basis="oracle",
                rate_type="final",
                venue=VENUE,
            )
        ]
    }
    marks = {
        MARKET: [
            MarkSnapshot(market=MARKET, ts=T0 + HOUR_MS, mark_price=100.0, index_price=100.0, venue=VENUE),
            MarkSnapshot(market=MARKET, ts=T0 + HOUR_MS + 12 * 60 * 1000, mark_price=100.0, index_price=100.0, venue=VENUE),
            MarkSnapshot(market=MARKET, ts=T0 + 2 * HOUR_MS, mark_price=62.0, index_price=62.0, venue=VENUE),
        ]
    }
    return EngineFeed(candles=candles, funding=funding, marks=marks)


def _strategy():
    # Fresh instance per run — the strategy is stateful (fires once).
    class _OpenOnce:
        def __init__(self) -> None:
            self._fired = False

        def on_candle(self, candle, ctx):
            if not self._fired:
                self._fired = True
                return [Signal.long(MARKET, VENUE, size=50.0)]
            return []

    return _OpenOnce()


def _log(run_id: str) -> EventLog:
    return EventLog(InMemoryUserData(), TenantContext.local(), run_id=run_id)


def test_legacy_engine_via_seam_is_byte_identical_to_a_direct_run():
    feed = _scenario_feed()

    # (a) direct BacktestEngine.run, funded + configured exactly as the seam does.
    direct_log = _log("direct")
    state = PortfolioState()
    state.fund(VENUE, money("1000"))
    BacktestEngine(
        direct_log, config=EngineConfig(), state=state, venue_spec=HYPERLIQUID
    ).run(
        feed.candles,
        marks=feed.marks,
        funding=feed.funding,
        books=feed.books,
        trades=feed.trades,
        oi=feed.oi,
        strategy=_strategy(),
    )

    # (b) the same run reached through the SimulationEngine seam.
    seam_log = _log("seam")
    spec = EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        initial_capital="1000",
        fund_venue=VENUE,
        mark_policy="close_derived",
    )
    engine = engine_for("legacy-bar")()
    assert engine.name == "legacy-bar"
    engine.run(feed, _strategy(), event_log=seam_log, spec=spec)

    direct_rows = [e.to_row() for e in direct_log.read()]
    seam_rows = [e.to_row() for e in seam_log.read()]
    assert direct_rows == seam_rows  # byte-identical event log — zero behavior change
    # And the fixture genuinely exercised the cascade path.
    assert any(r["kind"] == LIQUIDATION for r in seam_rows)


def test_engine_for_maps_legacy_bar_and_rejects_unknown_engines():
    # "auto" now resolves to Nautilus (the N9 default flip); see the guarded
    # test below. The legacy factory and the unknown-engine rejection run in
    # every environment, extra or not.
    assert engine_for("legacy-bar") is LegacyBarEngine
    with pytest.raises(UnknownEngineError, match="unknown engine"):
        engine_for("does-not-exist")


def test_nautilus_resolves_when_the_extra_is_installed():
    # N2: "nautilus" now resolves to the real engine when the optional extra is
    # present, and is loaded lazily (this import is the only place Nautilus enters).
    # N9: "auto" resolves to that same Nautilus engine — the default backtest
    # substrate as of the flip.
    pytest.importorskip("nautilus_trader")
    from flint.engine.nautilus import NautilusEngine

    assert engine_for("nautilus") is NautilusEngine
    assert engine_for("auto") is NautilusEngine
    assert NautilusEngine.name == "nautilus"


# --- the mark-policy fence (§6.0) -----------------------------------------


def _candle_only_tables() -> dict:
    table = _ArrowLike(
        [
            {
                "ts": T0,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 98.0,
                "volume": 1000.0,
                "market": MARKET,
                "resolution_s": HOUR_S,
                "venue": VENUE,
            }
        ]
    )
    return {(VENUE, MARKET, Kind.CANDLES): table}


class _ArrowLike:
    """A minimal stand-in for the Arrow tables ``build_engine_feed`` reads."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    @property
    def num_rows(self) -> int:
        return len(self._rows)

    def to_pylist(self) -> list[dict]:
        return list(self._rows)


def test_recorded_policy_lets_no_synthesized_mark_reach_the_engine():
    feed = build_engine_feed(
        _candle_only_tables(), {VENUE}, mark_policy="recorded"
    )
    assert feed.marks == {}  # candle-only feed, no synthesis under "recorded"


def test_close_derived_policy_synthesizes_todays_marks_exactly():
    feed = build_engine_feed(
        _candle_only_tables(), {VENUE}, mark_policy="close_derived"
    )
    # One close-derived mark at the first candle (mark = index = close), matching the
    # legacy build_engine_inputs behavior byte-for-byte.
    assert list(feed.marks) == [MARKET]
    (mark,) = feed.marks[MARKET]
    assert (mark.mark_price, mark.index_price) == (98.0, 98.0)

    legacy = build_engine_inputs(_candle_only_tables(), {VENUE})
    assert feed.marks == legacy.marks  # the fence preserves today's exact marks


# --- warm start: paper-resume parity across both engines (§6.7, N10) --------


def _warm_feed() -> EngineFeed:
    """A short warm-start run: add to a carried long, then settle funding on it.

    Prices sit around the carried entry so nothing liquidates — the run exercises a
    fill on top of a seeded book and a funding settlement on the grown position, the
    two accounting moves a paper resume must reproduce identically on both engines.
    """
    candles = [
        _bar(T0, 100.0, 101.0, 99.0, 100.0),
        _bar(T0 + HOUR_MS, 100.0, 101.0, 99.0, 100.0),
        _bar(T0 + 2 * HOUR_MS, 100.0, 101.0, 99.0, 100.0),
    ]
    funding = {
        MARKET: [
            FundingRate(
                market=MARKET,
                ts=T0 + HOUR_MS + 12 * 60 * 1000,  # minute 12 of bar1
                rate_hourly=0.001,
                interval_s=HOUR_S,
                price_basis="oracle",
                rate_type="final",
                venue=VENUE,
            )
        ]
    }
    marks = {
        MARKET: [
            MarkSnapshot(
                market=MARKET,
                ts=T0 + HOUR_MS + 12 * 60 * 1000,
                mark_price=100.0,
                index_price=100.0,
                venue=VENUE,
            )
        ]
    }
    return EngineFeed(candles=candles, funding=funding, marks=marks)


def _warm_seed_state() -> PortfolioState:
    """A carried book: nonzero cash + one open long + nonzero accumulators (§6.7).

    A fresh instance per engine — the legacy engine mutates the adopted state, so the
    two engines must not share one object.
    """
    state = PortfolioState()
    acct = state.account(VENUE)
    acct.cash = money("10000")
    acct.fees_paid = money("1.5")
    acct.funding_paid = money("2.0")
    acct.realized_pnl = money("3.0")
    state.positions[(VENUE, MARKET)] = Position(
        market=MARKET,
        venue=VENUE,
        side=Side.LONG,
        size=5.0,
        entry_price=100.0,
        margin_mode="cross",
    )
    return state


def _add_long_once():
    """Add 2.0 to the carried long on bar0 (fills T+1) — no seed close, blends entry."""

    class _AddOnce:
        def __init__(self) -> None:
            self._fired = False

        def on_candle(self, candle, ctx):
            if not self._fired:
                self._fired = True
                return [Signal.long(MARKET, VENUE, size=2.0)]
            return []

    return _AddOnce()


def _stripped_rows(log: EventLog) -> list[dict]:
    """Every event row with the honest ``engine`` field dropped from lifecycle events."""
    rows = []
    for e in log.read():
        row = e.to_row()
        if row["kind"] in ("run_started", "run_finished"):
            row["payload"] = {k: v for k, v in row["payload"].items() if k != "engine"}
        rows.append(row)
    return rows


def test_warm_start_parity_legacy_vs_nautilus():
    """A warm-started run is byte-identical across both engines (§6.7, §19.4).

    Both engines adopt the same seed book (fresh per engine), add to the carried long,
    and settle funding on it. The event streams must be byte-equal except the honest
    ``engine`` name on the lifecycle events — the same parity contract the cold-start
    goldens hold, now with a non-empty starting book.
    """
    pytest.importorskip("nautilus_trader")
    feed = _warm_feed()

    def run(engine_name: str) -> EventLog:
        log = _log(f"warm-{engine_name}")
        spec = EngineRunSpec(
            config=EngineConfig(),
            venue_spec=HYPERLIQUID,
            fund_venue=VENUE,
            mark_policy="close_derived",
            initial_state=_warm_seed_state(),
        )
        engine_for(engine_name)().run(
            feed, _add_long_once(), event_log=log, spec=spec
        )
        return log

    nautilus_rows = _stripped_rows(run("nautilus"))
    assert _stripped_rows(run("legacy-bar")) == nautilus_rows

    # The seed genuinely flowed through: EQUITY carries the seed's funding_paid, and a
    # new fill landed on top of the seeded book.
    assert any(
        r["kind"] == "equity" and r["payload"]["accrued_funding"] != "0"
        for r in nautilus_rows
    )
    assert any(r["kind"] == "fill" for r in nautilus_rows)
    assert any(r["kind"] == "funding" for r in nautilus_rows)


def _close_once():
    """Close the carried long on bar0 (fills T+1) — realizes PnL against the seed."""

    class _CloseOnce:
        def __init__(self) -> None:
            self._fired = False

        def on_candle(self, candle, ctx):
            if not self._fired:
                self._fired = True
                return [Signal.close(MARKET, VENUE)]
            return []

    return _CloseOnce()


def _reduce_half_once():
    """Halve the carried long on bar0 (fills T+1) — partial realize against the seed."""

    class _ReduceHalfOnce:
        def __init__(self) -> None:
            self._fired = False

        def on_candle(self, candle, ctx):
            if not self._fired:
                self._fired = True
                return [Signal.short(MARKET, VENUE, size=2.5)]
            return []

    return _ReduceHalfOnce()


def _run_warm(engine_name: str, strategy, feed: EngineFeed) -> EventLog:
    """Run one warm-started engine from the shared seed book (fresh state per engine)."""
    log = _log(f"warm-{engine_name}")
    spec = EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        fund_venue=VENUE,
        mark_policy="close_derived",
        initial_state=_warm_seed_state(),
    )
    engine_for(engine_name)().run(feed, strategy, event_log=log, spec=spec)
    return log


def test_warm_start_seed_close_parity_legacy_vs_nautilus():
    """Closing a carried position under warm start is byte-identical (§6.7, §19.4).

    Regression for the silently-dropped close: the seed long is materialized inside
    Nautilus at run start, so the strategy's reduce-only close fills against a real
    Nautilus position (rather than being denied against a flat book) and realizes PnL
    against the same entry basis on both engines. Before the fix Nautilus produced
    zero fills here while legacy produced one.
    """
    pytest.importorskip("nautilus_trader")
    feed = _warm_feed()

    nautilus_rows = _stripped_rows(_run_warm("nautilus", _close_once(), feed))
    assert _stripped_rows(_run_warm("legacy-bar", _close_once(), feed)) == nautilus_rows

    fills = [r for r in nautilus_rows if r["kind"] == "fill"]
    assert len(fills) == 1  # exactly the close — the seed materialization emits nothing
    fill = fills[0]["payload"]
    assert fill["side"] == Side.SHORT and fill["size"] == 5.0  # full close of the long
    assert float(fill["realized_pnl"]) != 0.0  # PnL realized against the carried seed
    # The seed materialization is Nautilus-internal bookkeeping — it leaks no event
    # (no extra fill, no order-lifecycle row referencing a seed order).
    assert all(
        "seed" not in str(r["payload"].get("client_order_id", "")) for r in nautilus_rows
    )


def test_warm_start_partial_reduce_parity_legacy_vs_nautilus():
    """Halving a carried position under warm start is byte-identical (§6.7, §19.4).

    A partial reduce nets the seed long down and realizes PnL on the closed half; the
    materialized seed lets Nautilus net against a real position, matching the shadow.
    """
    pytest.importorskip("nautilus_trader")
    feed = _warm_feed()

    nautilus_rows = _stripped_rows(_run_warm("nautilus", _reduce_half_once(), feed))
    assert (
        _stripped_rows(_run_warm("legacy-bar", _reduce_half_once(), feed))
        == nautilus_rows
    )

    fills = [r for r in nautilus_rows if r["kind"] == "fill"]
    assert len(fills) == 1
    assert fills[0]["payload"]["size"] == 2.5  # half the carried long closed
    assert float(fills[0]["payload"]["realized_pnl"]) != 0.0


def test_tick_lane_rejects_a_warm_start():
    """A warm start is bar-lane only — the tick lane rejects ``initial_state`` (N10)."""
    pytest.importorskip("nautilus_trader")

    class _TickStub:
        lane = "tick"

    spec = EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        fund_venue=VENUE,
        initial_state=_warm_seed_state(),
    )
    with pytest.raises(ValueError, match="tick lane does not support"):
        engine_for("nautilus")().run(
            EngineFeed(candles=[]), _TickStub(), event_log=_log("tick-warm"), spec=spec
        )
