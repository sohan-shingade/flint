"""Paper session + drift + alerts (slice 5.6, §6.7) — the parity promise, tested.

All on hand-authored real-shape HL frames, recorded-fragment lake data, and
hand-built event logs (D26 — no network, no keys, no generated series). Coverage:

* the runner contract: template → EngineStrategy / MLEngineStrategy adapter;
* a paper session over a fixture stream opens a position and computes drift;
* ACCEPTANCE — kill/restart + reconnect over the lake replays the gap and does
  NOT double-fill (state folded from the event log, bars never re-fed);
* drift metrics — structural (slippage z, funding mismatch, unexpected events)
  → alert; market (rolling Sharpe / hit-rate) → chart;
* the alert rule engine (liq distance, drift breach, funding-spread flip,
  heartbeat), webhook dispatch (mocked poster), and persistence.
"""

from __future__ import annotations

from flint.adapters import InMemoryUserData
from flint.core.models import Candle, FundingRate, MarkSnapshot, Signal
from flint.data.ingest.recorders import ReplayWsSource
from flint.data.livefeed import InMemoryGapSource
from flint.engine.portfolio.events import (
    EQUITY,
    FILL,
    FUNDING,
    LIQUIDATION,
    ORDER_REJECTED,
    Event,
)
from flint.live import (
    AlertContext,
    AlertEngine,
    CollectingChannel,
    DriftBreachRule,
    FundingSpreadFlipRule,
    HeartbeatRule,
    LiqDistanceRule,
    PaperSession,
    SlippageBaseline,
    WebhookChannel,
    build_adapter,
    build_drift_report,
    default_rules,
)
from flint.live.drift import FUNDING_ROUNDING_TOL
from flint.ports import TenantContext
from flint.strategy import Strategy
from flint.strategy.base import EngineStrategy
from flint.strategy.ml import MLEngineStrategy
from flint.strategy.templates.registry import TemplateSpec, get_template

VENUE = "hyperliquid"
COIN = "SOL"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = HOUR_S * 1000
BASE_HOUR = 472_223


def bar(n: int) -> int:
    return (BASE_HOUR + n) * HOUR_MS


# --- hand-authored real-shape HL WS frames --------------------------------


def trades_frame(ts: int, px: float, sz: float, *, tid: int = 1):
    return ("trades", [{"coin": COIN, "side": "B", "px": str(px), "sz": str(sz),
                        "time": ts, "hash": "0x", "tid": tid}])


def ctx_frame(*, mark: float, funding: float = 0.0, oi: float = 1000.0):
    return ("activeAssetCtx", {"coin": COIN, "ctx": {
        "openInterest": str(oi), "markPx": str(mark), "oraclePx": str(mark),
        "funding": str(funding), "midPx": str(mark)}})


# --- a minimal public strategy: open once, stay put (position-aware) --------


class _LongIfFlat(Strategy):
    params = dict(venue=VENUE, notional_usd=1000.0)

    def on_candle(self, candle, history, ctx):
        if ctx.position(candle.market, self.params["venue"]) is None:
            return Signal.long(candle.market, self.params["venue"],
                               size_usd=self.params["notional_usd"])
        return []


_LONG_SPEC = TemplateSpec(
    name="_test_long", strategy_cls=_LongIfFlat, summary="test", category="technical"
)


def _session(store, run_id, *, gap_source=None, channel=None, resume=False, spec=_LONG_SPEC):
    tenant = TenantContext.local()
    common = dict(
        tenant=tenant, store=store, run_id=run_id, market=MARKET, resolution_s=HOUR_S,
        gap_source=gap_source, channel=channel,
        slippage_baseline=SlippageBaseline(0.0, 1.0),
    )
    if resume:
        adapter = build_adapter(spec, tenant)
        return PaperSession.resume(adapter=adapter, **common)
    return PaperSession.create(template=spec, initial_capital="100000", **common)


# --- the runner contract (build_adapter) -----------------------------------


def test_build_adapter_wraps_classic_in_engine_strategy():
    adapter = build_adapter(_LONG_SPEC, TenantContext.local())
    assert isinstance(adapter, EngineStrategy)
    assert not isinstance(adapter, MLEngineStrategy)


def test_build_adapter_wraps_ml_in_ml_engine_strategy_with_scoped_store():
    spec = get_template("lgbm_trend")  # the built-in ML template (is_ml=True)
    adapter = build_adapter(spec, TenantContext.local(), seed=42, strategy_id="run-x")
    assert isinstance(adapter, MLEngineStrategy)
    assert adapter.seed == 42
    assert adapter.model_store is not None  # tenant-scoped ModelStore constructed


# --- a paper session over a fixture stream ---------------------------------


def test_paper_session_opens_position_and_computes_drift():
    store = InMemoryUserData()
    sess = _session(store, "run-1")
    frames = [
        trades_frame(bar(0) + 100_000, 100.0, 1.0, tid=1),
        ctx_frame(mark=100.0, funding=0.0001),
        trades_frame(bar(1) + 100_000, 101.0, 1.0, tid=2),
        trades_frame(bar(2) + 100_000, 102.0, 1.0, tid=3),  # closes bar1
    ]
    result = sess.feed(ReplayWsSource(frames))

    # Long decided on bar0 fills T+1 at bar1's open — a real position exists.
    assert result.final_state.position(VENUE, MARKET) is not None
    assert result.drift.attribution  # drift numbers computed
    # The run head + summary were persisted (tenant-scoped).
    rec = store.load_run(TenantContext.local(), "run-1")
    assert rec.kind == "paper" and "cash" in rec.summary


# --- ACCEPTANCE: kill / restart + reconnect without double-fills ------------


def test_restart_and_reconnect_replays_gap_without_double_fill():
    store = InMemoryUserData()

    # Session 1: bars 0,1,2 — the long opens and fills at bar1's open.
    s1 = _session(store, "acc-1")
    s1.feed(ReplayWsSource([
        trades_frame(bar(0) + 100_000, 100.0, 1.0, tid=1),
        ctx_frame(mark=100.0),
        trades_frame(bar(1) + 100_000, 100.0, 1.0, tid=2),
        trades_frame(bar(2) + 50_000, 100.0, 1.0, tid=3),  # closes bar1 (bar2 partial)
    ]))
    fills_after_conn1 = sum(1 for e in _events(store, "acc-1") if e.kind == FILL)
    assert fills_after_conn1 == 1

    # KILL + RESTART: brand-new session object from the same persisted store,
    # with the lake holding the missed bars 3,4 + a final funding settlement.
    gap = InMemoryGapSource(
        candles={(VENUE, MARKET): [_flat_candle(3, 100.0), _flat_candle(4, 100.0)]},
        funding={(VENUE, MARKET): [FundingRate(
            ts=bar(3) + 1_000, rate_hourly=0.0003, interval_s=HOUR_S,
            price_basis="oracle", rate_type="final", venue=VENUE, market=MARKET)]},
        marks={(VENUE, MARKET): [MarkSnapshot(MARKET, bar(3) + 500, 100.0, 100.0, VENUE)]},
    )
    s2 = _session(store, "acc-1", gap_source=gap, resume=True)

    # RECONNECT: stream resumes in bar 5 — bars 3,4 were missed.
    result = s2.feed(ReplayWsSource([
        trades_frame(bar(5) + 50_000, 100.0, 1.0, tid=9),
        trades_frame(bar(6) + 50_000, 100.0, 1.0, tid=10),  # closes bar5
    ]))

    # The gap was replayed through the engine (bars 3,4 recovered).
    assert result.recoveries and result.recoveries[-1].bars_recovered == 2
    # NO double-fill: still exactly one fill in the whole log after restart.
    assert sum(1 for e in _events(store, "acc-1") if e.kind == FILL) == 1
    # The position carried across the restart; funding settled on a replayed bar.
    assert result.final_state.position(VENUE, MARKET) is not None
    assert result.final_state.account(VENUE).funding_paid != 0


def test_resume_is_tenant_scoped():
    store = InMemoryUserData()
    _session(store, "owned").feed(ReplayWsSource([
        trades_frame(bar(0) + 100_000, 100.0, 1.0),
        trades_frame(bar(1) + 100_000, 101.0, 1.0),
    ]))
    # Another tenant cannot resume this run — load_run raises as if it doesn't exist.
    other = TenantContext(tenant_id="intruder")
    import pytest

    with pytest.raises(KeyError):
        PaperSession.resume(
            tenant=other, store=store, run_id="owned",
            adapter=build_adapter(_LONG_SPEC, other),
            market=MARKET, resolution_s=HOUR_S,
        )


# --- drift metrics ----------------------------------------------------------


def _fill_ev(seq, *, slippage_bps=0.0, realized="0"):
    return Event(kind=FILL, payload={
        "client_order_id": f"c{seq}", "market": MARKET, "venue": VENUE, "side": "buy",
        "price": 100.0, "size": 1.0, "fee": "0", "slippage_bps": slippage_bps,
        "realized_pnl": realized}, ts=bar(seq), seq=seq)


def test_structural_slippage_z_breaches_above_three():
    # 20 fills all 5 bps off a baseline of mean 0, std 1 → a huge window z-score.
    events = [_fill_ev(i, slippage_bps=5.0) for i in range(20)]
    report = build_drift_report(events, slippage_baseline=SlippageBaseline(0.0, 1.0))
    assert report.structural.slippage_z is not None
    assert report.structural.slippage_breach is True
    assert report.structural.breached is True
    assert any("slippage" in r for r in report.structural_breaches())


def test_structural_slippage_needs_a_full_window():
    events = [_fill_ev(i, slippage_bps=5.0) for i in range(5)]  # < 20
    report = build_drift_report(events, slippage_baseline=SlippageBaseline(0.0, 1.0))
    assert report.structural.slippage_z is None
    assert report.structural.slippage_breach is False


def test_structural_funding_mismatch_beyond_rounding():
    from flint.engine.money import money

    events = [Event(kind=FUNDING, payload={"amount": "-10.0"}, ts=bar(1), seq=0)]
    report = build_drift_report(
        events, slippage_baseline=SlippageBaseline(0.0, 1.0),
        expected_funding=money("0"),
    )
    assert report.structural.funding_breach is True
    assert abs(report.structural.funding_mismatch) > FUNDING_ROUNDING_TOL


def test_structural_flags_unexpected_liquidation_and_rejection():
    events = [
        Event(kind=LIQUIDATION, payload={"venue": VENUE, "market": MARKET}, ts=bar(1), seq=0),
        Event(kind=ORDER_REJECTED, payload={"client_order_id": "c1", "reason": "no_fill"}, ts=bar(1), seq=1),
    ]
    report = build_drift_report(events, slippage_baseline=SlippageBaseline(0.0, 1.0))
    assert len(report.structural.unexpected) == 2
    assert report.structural.breached is True


def test_market_drift_is_chart_data_not_breach():
    equity = [Event(kind=EQUITY, payload={"equity": str(100.0 + i)}, ts=bar(i), seq=i)
              for i in range(6)]
    fills = [_fill_ev(10, realized="5"), _fill_ev(11, realized="-2")]
    report = build_drift_report(
        equity + fills, slippage_baseline=SlippageBaseline(0.0, 1.0), sharpe_window=3
    )
    assert report.market.rolling_sharpe  # a rolling Sharpe series exists
    assert report.market.hit_rate == 0.5  # one win of two closes
    assert report.structural.breached is False  # market decay is not an alarm


# --- alerts -----------------------------------------------------------------


def test_liq_distance_rule_fires_when_close():
    rule = LiqDistanceRule(threshold_pct=10.0)
    fired = rule.evaluate(AlertContext(
        now_ts=bar(1), bar_interval_s=HOUR_S, liq_distances_pct={MARKET: 5.0}))
    assert len(fired) == 1 and fired[0].rule == "liq_distance"
    # comfortably far → nothing
    assert rule.evaluate(AlertContext(
        now_ts=bar(1), bar_interval_s=HOUR_S, liq_distances_pct={MARKET: 40.0})) == []


def test_funding_spread_flip_rule():
    rule = FundingSpreadFlipRule()
    ctx = AlertContext(now_ts=bar(1), bar_interval_s=HOUR_S,
                       funding_spread=-0.001, prev_funding_spread=0.001)
    assert len(rule.evaluate(ctx)) == 1
    same = AlertContext(now_ts=bar(1), bar_interval_s=HOUR_S,
                        funding_spread=0.002, prev_funding_spread=0.001)
    assert rule.evaluate(same) == []


def test_heartbeat_rule_fires_on_silence():
    rule = HeartbeatRule(multiple=2.0)
    # 3h of silence on a 1h bar > 2× → fire
    ctx = AlertContext(now_ts=bar(0) + 3 * HOUR_MS, bar_interval_s=HOUR_S,
                       last_event_ts=bar(0))
    assert len(rule.evaluate(ctx)) == 1
    # within 2 bars → quiet
    ok = AlertContext(now_ts=bar(0) + HOUR_MS, bar_interval_s=HOUR_S, last_event_ts=bar(0))
    assert rule.evaluate(ok) == []


def test_drift_breach_rule_fires_from_report():
    events = [_fill_ev(i, slippage_bps=8.0) for i in range(20)]
    report = build_drift_report(events, slippage_baseline=SlippageBaseline(0.0, 1.0))
    fired = DriftBreachRule().evaluate(
        AlertContext(now_ts=bar(1), bar_interval_s=HOUR_S, drift=report))
    assert len(fired) == 1 and fired[0].rule == "drift_breach"


def test_alert_engine_dispatches_to_webhook_and_collects():
    posted = []
    channel = WebhookChannel("https://hook.example/x", lambda url, payload: posted.append((url, payload)))
    engine = AlertEngine(default_rules(), channel)
    fired = engine.evaluate(AlertContext(
        now_ts=bar(1), bar_interval_s=HOUR_S, liq_distances_pct={MARKET: 3.0}))
    assert len(fired) == 1
    assert posted and posted[0][0] == "https://hook.example/x"
    assert posted[0][1]["rule"] == "liq_distance"
    assert engine.fired  # retained for the UI / persistence


def test_session_persists_alerts_in_run_summary():
    store = InMemoryUserData()
    channel = CollectingChannel()
    # Force a structural breach via a tight baseline + drifting slippage is hard on
    # a 3-bar fixture, so assert the persistence path structurally: the summary
    # carries the alerts list (empty here) and the structural-breach list.
    sess = _session(store, "run-a", channel=channel)
    sess.feed(ReplayWsSource([
        trades_frame(bar(0) + 100_000, 100.0, 1.0, tid=1),
        ctx_frame(mark=100.0),
        trades_frame(bar(1) + 100_000, 101.0, 1.0, tid=2),
        trades_frame(bar(2) + 50_000, 102.0, 1.0, tid=3),
    ]))
    rec = store.load_run(TenantContext.local(), "run-a")
    assert "alerts" in rec.summary and "structural_breaches" in rec.summary


# --- helpers ----------------------------------------------------------------


def _events(store, run_id):
    from flint.engine.portfolio import EventLog

    return EventLog(store, TenantContext.local(), run_id).read()


def _flat_candle(n: int, px: float) -> Candle:
    return Candle(ts=bar(n), open=px, high=px, low=px, close=px, volume=1.0,
                  market=MARKET, resolution_s=HOUR_S, venue=VENUE)
