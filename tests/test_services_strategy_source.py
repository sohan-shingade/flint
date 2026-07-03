"""User-strategy-source services — validate + backtest submitted code (§13, D25).

Covers the carry-forward (c)/(d)/(n) boundary: validate_strategy is the sandbox +
static-lint gate that returns structured, line-precise errors *before* any run,
and run_backtest_source is the user-source engine path 7.1's template-by-name
run_backtest deliberately did not discharge. All inputs are hand-authored (D26):
tiny fixed candle/funding frames and unit strategy sources — no generated
market-like series, no network, no keys.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from flint.adapters import InMemoryUserData
from flint.data import DataManager
from flint.data.ranges import Kind
from flint.data.sources import InMemoryCacheSource
from flint.ports import TenantContext
from flint.services import (
    ValidationError,
    compile_strategy,
    run_backtest_source,
    run_results,
    validate_strategy,
)

ALICE = TenantContext(tenant_id="alice")
BOB = TenantContext(tenant_id="bob")
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


# --- strategy source fixtures (hand-authored, D26) ---------------------------

CLEAN = """
from flint.strategy import Strategy
from flint.core.models import Signal


class MomoStrat(Strategy):
    params = {"n": 1}

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

RUNTIME_ERROR = """
from flint.strategy import Strategy


class S(Strategy):
    def on_candle(self, candle, history, ctx):
        return 1 / 0
"""

STATIC_LEAK = """
import numpy as np


def features(rows):
    closes = [r.close for r in rows]
    m = np.mean(closes)
    return [c - m for c in closes]
"""

DYNAMIC_LEAK = """
def features(rows):
    last = rows[-1].close
    return [r.close / last for r in rows]
"""

NO_ENTRY = """
CONST = 42


def helper(x):
    return x + 1
"""


def test_validate_clean_strategy_is_valid_and_ran_in_the_sandbox():
    report = validate_strategy(CLEAN)
    assert report.valid is True
    assert report.stage == "ok"
    assert report.dynamic_ran is True
    assert report.leak_detected is False
    # Honest wording (carry-forward (d)): never "leak-free"; blind spots shown.
    assert "no leak detected" in report.lookahead_summary
    assert "leak-free" not in report.lookahead_summary
    assert len(report.blind_spots) > 0
    assert "truncation_probe" in report.checks_run


def test_validate_forbidden_import_is_a_line_precise_screen_violation():
    report = validate_strategy(BAD_IMPORT)
    assert report.valid is False
    assert report.stage == "screen"
    codes = [v["code"] for v in report.screen_violations]
    assert "import-not-allowed" in codes
    # Line-precise: the violation points at the import line.
    assert all(v["line"] > 0 for v in report.screen_violations)


def test_validate_runtime_error_surfaces_as_a_structured_sandbox_error():
    report = validate_strategy(RUNTIME_ERROR)
    assert report.valid is False
    assert report.stage == "sandbox"
    assert report.sandbox_error is not None
    assert report.sandbox_error["type"] == "StrategyError"
    assert "ZeroDivisionError" in report.sandbox_error["message"]


def test_validate_static_leak_is_caught_by_the_screen_line_precise():
    report = validate_strategy(STATIC_LEAK)
    assert report.valid is False
    assert report.stage == "screen"
    codes = [v["code"] for v in report.screen_violations]
    assert "feature-lookahead" in codes


def test_validate_dynamic_leak_is_caught_by_the_sandboxed_truncation_probe():
    # A future-normalization the static screen misses; the truncation probe's
    # feature_fn runs INSIDE the sandbox (carry-forward (d)) and diffs the re-run.
    report = validate_strategy(DYNAMIC_LEAK)
    assert report.valid is True  # it runs — a leak is a warning, not un-runnable
    assert report.leak_detected is True
    cats = {f["category"] for f in report.lookahead_findings}
    assert "truncation_divergence" in cats


def test_validate_source_without_a_runnable_entry_is_static_only():
    report = validate_strategy(NO_ENTRY)
    assert report.valid is True
    assert report.dynamic_ran is False
    assert "truncation_probe" not in report.checks_run


def test_run_backtest_source_runs_validated_user_code_through_the_real_engine():
    ud = InMemoryUserData()
    out = run_backtest_source(
        ALICE,
        source=CLEAN,
        run_id="u1",
        universe=(MARKET,),
        venues=(VENUE,),
        start_ms=T0,
        end_ms=_covered_end(),
        resolution_s=3600,
        user_data=ud,
        data=_data(),
    )
    assert out.verdict == "ok"
    assert out.summary["metrics"]["n_returns"] == N - 1
    # Real engine: funding actually settled on the opened position.
    assert out.summary["cost"]["funding_settlements"] >= 1
    # The durable head is persisted, tenant-scoped, and carries the user source
    # for reproduction (the manifest's strategy_source, carry-forward (f)).
    record = ud.load_run(ALICE, "u1")
    assert record.status == "done"
    assert record.summary["strategy_source"] == CLEAN
    assert record.summary["validation"]["valid"] is True


def test_run_backtest_source_invalid_code_returns_verdict_invalid_no_run():
    ud = InMemoryUserData()
    out = run_backtest_source(
        ALICE,
        source=RUNTIME_ERROR,
        run_id="bad",
        universe=(MARKET,),
        venues=(VENUE,),
        start_ms=T0,
        end_ms=_covered_end(),
        user_data=ud,
        data=_data(),
    )
    assert out.verdict == "invalid"
    assert out.summary["validation"]["stage"] == "sandbox"
    # No engine run happened — nothing persisted under that id.
    with pytest.raises(KeyError):
        ud.load_run(ALICE, "bad")


def test_run_backtest_source_funding_gap_is_a_structured_rejection_not_an_error():
    ud = InMemoryUserData()
    out = run_backtest_source(
        ALICE,
        source=CLEAN,
        run_id="rej",
        universe=(MARKET,),
        venues=(VENUE,),
        start_ms=T0,
        end_ms=_covered_end(),
        user_data=ud,
        data=_data(with_funding=False),  # no funding → hard-gate rejection
    )
    assert out.verdict == "rejected"
    assert out.rejection is not None
    assert out.rejection.code == "funding_gap"
    assert "rejected" in out.summary
    # A rejected run is still a completed, persisted head (data, §19.1).
    assert ud.load_run(ALICE, "rej").status == "done"


def test_compile_strategy_rejects_source_with_no_strategy_subclass():
    with pytest.raises(ValidationError):
        compile_strategy(NO_ENTRY)


def test_run_results_is_tenant_scoped():
    ud = InMemoryUserData()
    run_backtest_source(
        ALICE,
        source=CLEAN,
        run_id="scoped",
        universe=(MARKET,),
        venues=(VENUE,),
        start_ms=T0,
        end_ms=_covered_end(),
        user_data=ud,
        data=_data(),
    )
    # ALICE sees a full per-trade log; BOB cannot see ALICE's run at all.
    alice_view = run_results(ALICE, "scoped", user_data=ud)
    assert alice_view["verdict"] == "ok"
    assert len(alice_view["trades"]) >= 1
    from flint.services import NotFoundError

    with pytest.raises(NotFoundError):
        run_results(BOB, "scoped", user_data=ud)
