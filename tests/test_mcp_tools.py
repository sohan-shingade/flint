"""The MCP agent tool layer — JSON in/out over services, bounded concurrency (§13).

Drives :class:`AgentTools` directly (transport-independent) plus a light check
that the FastMCP stdio server registers every tool. All data is hand-authored
(D26); the JobRunner and stores are the local in-memory adapters, so there is no
network and no keys.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from flint.adapters import InMemoryUserData
from flint.data import DataManager
from flint.data.ranges import Kind
from flint.data.sources import InMemoryCacheSource
from flint.ports import JobRunnerPort, TenantContext
from flint.mcp_srv import AgentTools, build_server, mcp_available
from flint.services import ValidationError

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
T0 = 1_700_000_000_000
H = 3_600_000
N = 8


def _candles(n: int = N) -> pa.Table:
    closes = [100.0, 101.0, 102.0, 101.0, 103.0, 104.0, 103.0, 105.0][:n]
    return pa.table(
        {
            "ts": [T0 + i * H for i in range(n)],
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
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


def _covered_end(n: int = N) -> int:
    return T0 + (n - 1) * H + 60_000 + 1


def _data(*, with_funding: bool = True) -> DataManager:
    cache = InMemoryCacheSource()
    cache.store(VENUE, MARKET, Kind.CANDLES, _candles())
    if with_funding:
        cache.store(VENUE, MARKET, Kind.FUNDING, _funding())
    return DataManager(sources=[cache])


CLEAN = """
from flint.strategy import Strategy
from flint.core.models import Signal


class MomoStrat(Strategy):
    def on_candle(self, candle, history, ctx):
        if len(history) < 2:
            return []
        if candle.close > history[-2].close:
            return [Signal.long(candle.market, candle.venue, size_usd=2000.0)]
        return [Signal(market=candle.market, venue=candle.venue, action="close")]
"""

BAD_IMPORT = """
import requests
from flint.strategy import Strategy


class S(Strategy):
    def on_candle(self, candle, history, ctx):
        return []
"""


def _tools(*, data: DataManager | None = None, **kw) -> AgentTools:
    return AgentTools(
        tenant=TenantContext(tenant_id="agent"),
        user_data=InMemoryUserData(),
        data=data if data is not None else _data(),
        **kw,
    )


def _bt_kwargs() -> dict:
    return dict(
        universe=(MARKET,),
        venues=(VENUE,),
        start_ms=T0,
        end_ms=_covered_end(),
        resolution_s=3600,
    )


def test_list_universe_lists_templates_and_the_executable_venue():
    out = _tools().list_universe()
    names = {t["name"] for t in out["templates"]}
    assert "funding_harvest" in names
    assert out["executable_venues"] == [VENUE]  # D28: HL only


def test_data_coverage_reports_covered_ranges():
    out = _tools().data_coverage(market=MARKET, venue=VENUE)
    assert out["market"] == MARKET
    assert out["coverage"]["candles"] is not None


def test_validate_strategy_tool_returns_structured_payload():
    out = _tools().validate_strategy(BAD_IMPORT)
    assert out["valid"] is False
    assert out["stage"] == "screen"
    assert any(v["code"] == "import-not-allowed" for v in out["screen_violations"])


def test_run_backtest_accepts_user_source_end_to_end():
    tools = _tools()
    out = tools.run_backtest(code=CLEAN, **_bt_kwargs())
    assert out["verdict"] == "ok"
    results = tools.get_results(out["run_id"])
    assert results["metrics"]["n_returns"] == N - 1
    # get_results carries the per-trade log + per-segment fidelity tier (§13.3).
    assert len(results["trades"]) >= 1
    assert results["fills_by_fidelity_tier"]
    assert results["cost_attribution"]["funding_settlements"] >= 1


def test_run_backtest_accepts_a_template_by_name():
    out = _tools().run_backtest(strategy="funding_harvest", **_bt_kwargs())
    assert out["verdict"] == "ok"


def test_run_backtest_requires_exactly_one_of_code_or_strategy():
    tools = _tools()
    with pytest.raises(ValidationError):
        tools.run_backtest(**_bt_kwargs())  # neither
    with pytest.raises(ValidationError):
        tools.run_backtest(code=CLEAN, strategy="funding_harvest", **_bt_kwargs())


def test_run_backtest_invalid_user_source_returns_validation_no_run():
    out = _tools().run_backtest(code=BAD_IMPORT, **_bt_kwargs())
    assert out["verdict"] == "invalid"
    assert out["validation"]["stage"] == "screen"


def test_run_backtest_funding_gap_is_a_structured_rejection():
    out = _tools(data=_data(with_funding=False)).run_backtest(
        strategy="funding_harvest", **_bt_kwargs()
    )
    assert out["verdict"] == "rejected"
    assert out["rejected"]["code"] == "funding_gap"


def test_explain_failure_returns_well_formed_reason_records():
    tools = _tools()
    out = tools.run_backtest(code=CLEAN, **_bt_kwargs())
    diag = tools.explain_failure(out["run_id"])
    assert diag["run_id"] == out["run_id"]
    assert isinstance(diag["reasons"], list)
    assert all("reason" in r for r in diag["reasons"])


def test_diagnose_classifies_each_failure_mode():
    # A pure-function pass over hand-authored result blobs (D26) — one per §13.3
    # enum, so the classifier is pinned independent of any real run's numbers.
    from flint.mcp_srv.tools import _diagnose

    funding = _diagnose(
        {"cost_attribution": {"funding": -90.0, "trading_pnl": -10.0}, "trades": [{}]}
    )
    assert any(r["reason"] == "funding_dominated" for r in funding)

    liquidated = _diagnose(
        {"trades": [{"side": "liquidation", "ts": 5, "price": 42.0}]}
    )
    assert any(r["reason"] == "liquidated" for r in liquidated)

    overfit = _diagnose(
        {"trades": [{}], "deflated_sharpe": {"deflated_sharpe": -0.2}, "n_trials": 20}
    )
    assert any(r["reason"] == "overfit_suspected" for r in overfit)

    leak = _diagnose(
        {
            "trades": [{}],
            "validation": {
                "leak_detected": True,
                "lookahead": {"summary": "row 0 changed"},
            },
        }
    )
    assert any(r["reason"] == "lookahead_detected" for r in leak)

    rejected = _diagnose({"rejected": {"code": "funding_gap", "message": "no funding"}})
    assert rejected == [
        {"reason": "rejected", "code": "funding_gap", "detail": "no funding"}
    ]

    invalid = _diagnose({"verdict": "invalid", "validation": {"stage": "screen"}})
    assert invalid[0]["reason"] == "invalid_strategy"


def test_explain_failure_flags_no_trades():
    never = """
from flint.strategy import Strategy


class Idle(Strategy):
    def on_candle(self, candle, history, ctx):
        return []
"""
    tools = _tools()
    out = tools.run_backtest(code=never, **_bt_kwargs())
    diag = tools.explain_failure(out["run_id"])
    assert any(r["reason"] == "no_trades" for r in diag["reasons"])


class _ReentrantRunner(JobRunnerPort):
    """A runner whose first submit re-enters the tools to force a second run."""

    def __init__(self) -> None:
        self.reenter = None
        self._calls = 0

    def submit(self, tenant, fn, quota):
        self._calls += 1
        if self._calls == 1 and self.reenter is not None:
            self.reenter()
        return fn()


def test_concurrency_cap_is_a_structured_rejection_not_a_crash():
    runner = _ReentrantRunner()
    tools = AgentTools(
        tenant=TenantContext(tenant_id="agent"),
        user_data=InMemoryUserData(),
        data=_data(),
        job_runner=runner,
        max_concurrent_runs=1,
    )
    captured: dict = {}

    def reenter():
        # A second run while the first is still in flight — over the cap of 1.
        captured["res"] = tools.run_backtest(strategy="funding_harvest", **_bt_kwargs())

    runner.reenter = reenter
    outer = tools.run_backtest(strategy="funding_harvest", **_bt_kwargs())
    assert outer["verdict"] == "ok"
    assert captured["res"]["rejected"]["code"] == "concurrency_limit"


def test_optimize_returns_trial_count_and_out_of_sample_scores():
    out = _tools().optimize(
        strategy="funding_harvest",
        params=["rate_threshold=0.00001:0.00003:0.00001"],
        universe=(MARKET,),
        venues=(VENUE,),
        start_ms=T0,
        end_ms=_covered_end(),
        resolution_s=3600,
        n_windows=2,
        n_trials=3,
    )
    assert out["verdict"] in {"ok", "rejected"}
    if out["verdict"] == "ok":
        assert out["summary"]["n_trials"] >= 1
        assert "optimize" in out["summary"]


def test_compare_carries_runlib_warnings():
    tools = _tools()
    a = tools.run_backtest(code=CLEAN, run_id="a", **_bt_kwargs())
    b = tools.run_backtest(strategy="funding_harvest", run_id="b", **_bt_kwargs())
    cmp = tools.compare([a["run_id"], b["run_id"]])
    assert set(cmp["run_ids"]) == {"a", "b"}
    assert "warnings" in cmp
    assert "metrics" in cmp


def test_server_registers_every_tool():
    import asyncio

    assert mcp_available() is True
    server = build_server(_tools())
    listed = asyncio.run(server.list_tools())
    assert {t.name for t in listed} == {
        "list_universe",
        "data_coverage",
        "validate_strategy",
        "run_backtest",
        "get_results",
        "explain_failure",
        "optimize",
        "compare",
    }
