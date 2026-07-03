"""The Python SDK ``Lab`` — the notebook surface over services/ (§12, D8).

Every Lab call drives the domain only through ``services/`` under a TenantContext; the
result objects render the §11 trust tearsheet. Data is hand-authored recorded-fragment
fixtures (D26): no generated market-like series. Fully mocked — no network, no keys.
"""

from __future__ import annotations

import pyarrow as pa

from flint.adapters import InMemoryUserData
from flint.data import DataManager
from flint.data.ranges import Kind
from flint.data.sources import InMemoryCacheSource
from flint.ports import TenantContext
from flint.sdk import BacktestResult, Lab, OptimizeResult

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
T0 = 1_700_000_000_000
H = 3_600_000
N = 6
COVERED_END = T0 + (N - 1) * H + 60_000 + 1


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


def _data(*, with_funding: bool = True) -> DataManager:
    cache = InMemoryCacheSource()
    cache.store(VENUE, MARKET, Kind.CANDLES, _candles())
    if with_funding:
        cache.store(VENUE, MARKET, Kind.FUNDING, _funding())
    return DataManager(sources=[cache])


def _lab(data: DataManager | None = None, ud: InMemoryUserData | None = None) -> Lab:
    return Lab(
        TenantContext(tenant_id="alice"),
        user_data=ud or InMemoryUserData(),
        data=data or _data(),
    )


# -- backtest ----------------------------------------------------------------


def test_lab_backtest_returns_a_result_with_a_trust_tearsheet():
    result = _lab().backtest(
        "funding_harvest",
        universe=[MARKET],
        venues=[VENUE],
        start_ms=T0,
        end_ms=COVERED_END,
    )
    assert isinstance(result, BacktestResult)
    assert result.verdict == "ok"
    assert not result.rejected
    assert "sharpe" in result.metrics

    sheet = result.tearsheet()
    # carry-forward (i): raw Sharpe + DSR + trial count + annualization + effective range.
    assert "Sharpe (raw):" in sheet
    assert "Deflated Sharpe: n/a (0 trials" in sheet  # single un-tuned run → honest n/a
    assert "/yr)" in sheet  # annualization factor printed
    assert f"[{T0}," in sheet  # effective evaluated range beside the metrics


def test_lab_backtest_tearsheet_carries_the_timing_breakdown():
    # §19.2: a per-run timing breakdown so "why is my backtest slow" is answerable.
    result = _lab().backtest(
        "funding_harvest",
        universe=[MARKET],
        venues=[VENUE],
        start_ms=T0,
        end_ms=COVERED_END,
    )
    timing = result.summary["timing"]
    assert set(timing) == {
        "data_fetch_ms",
        "input_build_ms",
        "engine_run_ms",
        "report_ms",
    }
    assert "timing (total" in result.tearsheet()


def test_lab_backtest_funding_gap_is_a_rejected_result_not_an_exception():
    result = _lab().backtest(
        "funding_harvest",
        universe=[MARKET],
        venues=[VENUE],
        start_ms=T0,
        end_ms=COVERED_END + H,  # one bar past funding coverage → hard gate (§6.4)
    )
    assert result.rejected
    assert result.rejection["code"] == "funding_gap"
    sheet = result.tearsheet()
    assert "REJECTED [funding_gap]" in sheet
    assert (
        "fix:" in sheet
    )  # the rejection carries the actionable fix, not a stack trace


def test_lab_persists_the_run_under_the_tenant():
    ud = InMemoryUserData()
    lab = _lab(ud=ud)
    result = lab.backtest(
        "funding_harvest",
        universe=[MARKET],
        venues=[VENUE],
        start_ms=T0,
        end_ms=COVERED_END,
    )
    assert ud.load_run(lab.tenant, result.run_id).status == "done"
    # A different tenant sees nothing of Alice's run (§2.7).
    assert ud.list_runs(TenantContext(tenant_id="bob")) == []


# -- optimize ----------------------------------------------------------------


def test_lab_optimize_reports_dsr_and_trial_count():
    result = _lab().optimize(
        "funding_harvest",
        params=["rate_threshold=0.0:0.0005"],
        universe=[MARKET],
        venues=[VENUE],
        start_ms=T0,
        end_ms=COVERED_END,
        n_windows=2,
        n_trials=2,
        seed=1,
    )
    assert isinstance(result, OptimizeResult)
    # 2 windows × 2 trials = 4 trials in the family; DSR is computed, not n/a.
    assert result.summary["n_trials"] == 4
    assert result.summary["deflated_sharpe"] is not None
    assert result.summary["optimize"]["n_windows"] == 2
    assert "rate_threshold" in result.best_params

    sheet = result.tearsheet()
    assert "Deflated Sharpe:" in sheet
    assert "over 4 trials" in sheet
    assert "walk-forward: 2 windows, 4 trials" in sheet


def test_lab_optimize_selects_the_best_oos_fold_string_or_paramspace():
    from flint.research import ParamSpace

    # The CLI string form and a ParamSpace object are accepted interchangeably.
    result = _lab().optimize(
        "funding_harvest",
        params=[ParamSpace.parse("rate_threshold=0.0:0.0005")],
        universe=[MARKET],
        venues=[VENUE],
        start_ms=T0,
        end_ms=COVERED_END,
        n_windows=2,
        n_trials=2,
    )
    assert result.verdict == "ok"
    assert result.metrics  # the confirmation run's metrics are present


# -- paper -------------------------------------------------------------------


def test_lab_paper_starts_a_session_and_snapshots_it():
    ud = InMemoryUserData()
    lab = _lab(ud=ud)
    handle = lab.paper("funding_harvest", market=MARKET, resolution_s=3600)
    assert handle.run_id
    # The head is persisted as a running paper run, streamable by the monitor.
    snap = handle.snapshot()
    assert snap["run_id"] == handle.run_id
    assert snap["kind"] == "paper"
    assert snap["status"] == "running"
    assert ud.load_run(lab.tenant, handle.run_id).kind == "paper"
