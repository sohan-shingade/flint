"""Services front door — run_backtest drives the REAL engine under a tenant (§4, §6).

Supersedes the Phase-1 walking-skeleton acceptance: the front door no longer runs
a no-op ``def run(ctx)`` stub — it resolves funding-gated data, wraps a template
in the runner-contract adapter (5.6), walks the honest engine, and folds the trust
report (§11). Templates are trusted built-ins, so there is no user source to
sandbox here (the sandbox is the surface boundary for *user* code — 7.3's
validate_strategy). Data is hand-authored recorded-fragment fixtures (D26): no
generated market-like series anywhere.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from flint.adapters import InMemoryUserData
from flint.data import DataManager
from flint.data.ranges import Kind
from flint.data.sources import InMemoryCacheSource
from flint.ports import TenantContext
from flint.research import list_runs as runlib_list_runs
from flint.services import (
    BacktestRequest,
    ValidationError,
    make_backtest_runner,
    run_backtest,
)

ALICE = TenantContext(tenant_id="alice")
BOB = TenantContext(tenant_id="bob")

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
T0 = 1_700_000_000_000
H = 3_600_000
N = 6


def _candles(n: int = N) -> pa.Table:
    return pa.table(
        {
            "ts": [T0 + i * H for i in range(n)],
            "open": [100.0] * n,
            "high": [100.0] * n,
            "low": [100.0] * n,
            "close": [100.0] * n,
            "volume": [1000.0] * n,
            "market": [MARKET] * n,
            "resolution_s": [3600] * n,
            "venue": [VENUE] * n,
        }
    )


def _funding(n: int = N) -> pa.Table:
    # Predicted at bar start (strategy-visible); final +1min into the bar (settles).
    # Distinct ts so the store's ts-dedupe keeps both rows (§6.4 predicted/final).
    ts_pred = [T0 + i * H for i in range(n)]
    ts_fin = [T0 + i * H + 60_000 for i in range(n)]
    return pa.table(
        {
            "ts": ts_pred + ts_fin,
            "rate_hourly": [0.001] * (2 * n),
            "interval_s": [3600] * (2 * n),
            "price_basis": ["oracle"] * (2 * n),
            "rate_type": ["predicted"] * n + ["final"] * n,
            "venue": [VENUE] * (2 * n),
            "market": [MARKET] * (2 * n),
        }
    )


def _covered_end(n: int = N) -> int:
    return T0 + (n - 1) * H + 60_000 + 1  # last final ts + 1 (half-open envelope)


def _data(*, with_funding: bool = True) -> DataManager:
    cache = InMemoryCacheSource()
    cache.store(VENUE, MARKET, Kind.CANDLES, _candles())
    if with_funding:
        cache.store(VENUE, MARKET, Kind.FUNDING, _funding())
    return DataManager(sources=[cache])


def _request(**over) -> BacktestRequest:
    base = dict(
        run_id="run-1",
        strategy="funding_harvest",
        universe=(MARKET,),
        venues=(VENUE,),
        start_ms=T0,
        end_ms=_covered_end(),
        resolution_s=3600,
    )
    base.update(over)
    return BacktestRequest(**base)


def test_backtest_runs_through_the_real_engine():
    ud = InMemoryUserData()
    out = run_backtest(ALICE, _request(), user_data=ud, data=_data())

    assert out.verdict == "ok"
    m = out.summary["metrics"]
    assert m["n_returns"] == N - 1  # one return per bar transition
    # The per-bar EQUITY event folded into a curve of one point per bar (§6.1).
    assert len(out.summary["equity_curve"]) == N
    # The short actually opened and funding actually settled (real engine, not a stub).
    cost = out.summary["cost"]
    assert cost["funding_settlements"] >= 1
    assert cost["funding"] > 0
    # Fidelity is recorded honestly per leg (funding full; OI absent → marks fell
    # back to the candle close, flagged degraded rather than silently faked).
    lines = out.summary["fidelity_lines"]
    assert any("funding: full" in line for line in lines)
    assert any("oi: DEGRADED" in line for line in lines)
    # The durable head is persisted, tenant-scoped, and marked done.
    assert ud.load_run(ALICE, "run-1").status == "done"


def test_funding_hard_gate_returns_a_structured_rejection_not_an_error():
    # Request one bar past the funding coverage → the §6.4 hard gate rejects with
    # the range available and the fix, as DATA (§19.1) — never a stack trace.
    ud = InMemoryUserData()
    out = run_backtest(
        ALICE, _request(end_ms=_covered_end() + H), user_data=ud, data=_data()
    )
    assert out.verdict == "rejected"
    assert out.rejection is not None
    assert out.rejection.code == "funding_gap"
    assert out.rejection.missing  # names the leg(s) missing funding
    assert out.rejection.available  # carries the available bounds
    assert out.rejection.hint
    # Persisted as a completed run carrying the structured rejected payload.
    stored = ud.load_run(ALICE, "run-1")
    assert stored.status == "done"
    assert stored.summary["rejected"]["code"] == "funding_gap"


def test_no_funding_data_at_all_rejects():
    ud = InMemoryUserData()
    out = run_backtest(ALICE, _request(), user_data=ud, data=_data(with_funding=False))
    assert out.verdict == "rejected"
    assert out.rejection.code == "funding_gap"


def test_unknown_template_is_a_validation_error():
    ud = InMemoryUserData()
    with pytest.raises(ValidationError) as exc:
        run_backtest(ALICE, _request(strategy="nope"), user_data=ud, data=_data())
    assert exc.value.code == "validation"


def test_tenant_isolation_holds():
    ud = InMemoryUserData()
    run_backtest(ALICE, _request(), user_data=ud, data=_data())
    # Bob sees neither Alice's run head nor her run in the library.
    assert ud.list_runs(BOB) == []
    with pytest.raises(KeyError):
        ud.load_run(BOB, "run-1")


def test_run_is_persisted_as_a_run_library_manifest():
    # GET /runs is backed by runlib.list_runs — the run must round-trip as a
    # manifest carrying its metrics + effective range for list/compare (§11.2).
    ud = InMemoryUserData()
    run_backtest(ALICE, _request(), user_data=ud, data=_data())
    manifests = runlib_list_runs(ALICE, ud)
    assert len(manifests) == 1
    man = manifests[0]
    assert man.strategy_name == "funding_harvest"
    assert "sharpe" in man.metrics
    assert man.effective_start_ts == T0


def test_make_backtest_runner_scores_a_window():
    # The production wiring for research.walkforward's injected BacktestRunner:
    # a (params, start, end) -> float closure over the real engine, per tenant.
    runner = make_backtest_runner(
        ALICE,
        data=_data(),
        strategy="funding_harvest",
        universe=[MARKET],
        venues=[VENUE],
        resolution_s=3600,
    )
    score = runner({}, T0, _covered_end())
    assert isinstance(score, float)
