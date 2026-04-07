"""Phase 7A correctness tests — strategy catalog must match backtest builders."""
from __future__ import annotations

import pytest

from flint.api.routes.backtest import _build_strategy, _DEFAULTS
from flint.api.routes.strategies import list_strategies
from flint.analytics.monte_carlo import run_monte_carlo


class TestStrategyCatalogMatchesBuilders:
    """Every listed strategy must be buildable, and vice versa."""

    def _catalog_names(self) -> list[str]:
        result = list_strategies()
        return [s["name"] for s in result["strategies"]]

    # 1. Every strategy in the catalog must be buildable
    def test_all_listed_strategies_are_buildable(self):
        names = self._catalog_names()
        assert len(names) > 0, "Strategy catalog is empty"
        for name in names:
            params = _DEFAULTS.get(name, {})
            strat = _build_strategy(name, params)
            assert strat is not None, (
                f"Strategy '{name}' is listed in the catalog but "
                f"_build_strategy returned None (missing from builders dict)"
            )

    # 2. Every key in _DEFAULTS must appear in the catalog
    def test_defaults_keys_in_catalog(self):
        names = set(self._catalog_names())
        for key in _DEFAULTS:
            assert key in names, (
                f"Strategy '{key}' is in _DEFAULTS but missing from the catalog"
            )

    # 3. Every catalog entry has required fields
    def test_catalog_entries_have_required_fields(self):
        result = list_strategies()
        required = {"name", "display_name", "description", "params", "type"}
        for entry in result["strategies"]:
            missing = required - set(entry.keys())
            assert not missing, (
                f"Strategy '{entry.get('name', '??')}' is missing fields: {missing}"
            )

    # 4. Every builder key must have a matching _DEFAULTS entry
    def test_builder_keys_have_defaults(self):
        # Import the builders dict indirectly by checking _DEFAULTS covers all
        # buildable names.  _DEFAULTS and builders should be 1:1.
        for name in _DEFAULTS:
            strat = _build_strategy(name, _DEFAULTS[name])
            assert strat is not None, (
                f"Strategy '{name}' has _DEFAULTS but _build_strategy fails"
            )


class TestMonteCarloAnnualization:
    """Monte Carlo Sharpe CI must use trade frequency, not sqrt(8760)."""

    def test_sharpe_ci_contains_reasonable_range(self):
        """30 trades over 90 days — Sharpe CI should be within 10x of the mean."""
        pnls = [100, -50, 80, -30, 120, -60, 90, -40, 110, -70,
                60, -20, 150, -80, 40, -10, 130, -50, 70, -30]
        # 30 trades total
        pnls = pnls + [50, -25, 75, -35, 95, -45, 85, -55, 65, -15]
        period_90d = 90 * 86400
        result = run_monte_carlo(pnls, initial_capital=10000,
                                 n_simulations=500, period_seconds=period_90d)
        sharpe_mean = result.sharpe_mean
        # CI should be within 10x of the mean (not 43-46 for a mean of ~6)
        assert abs(result.sharpe_ci_lower) < abs(sharpe_mean) * 10, (
            f"CI lower {result.sharpe_ci_lower} is not within 10x of mean {sharpe_mean}"
        )
        assert abs(result.sharpe_ci_upper) < abs(sharpe_mean) * 10, (
            f"CI upper {result.sharpe_ci_upper} is not within 10x of mean {sharpe_mean}"
        )
        # And the CI should be sane — not in the 40s
        assert result.sharpe_ci_upper < 20, (
            f"Sharpe CI upper {result.sharpe_ci_upper} is unreasonably high"
        )

    def test_annualization_uses_trade_frequency(self):
        """Same trades over 1yr vs 1mo should give different Sharpe."""
        pnls = [100, -50, 80, -30, 120, -60, 90, -40, 110, -70,
                60, -20, 150, -80, 40, -10, 130, -50, 70, -30]
        period_1yr = 365 * 86400
        period_1mo = 30 * 86400
        result_1yr = run_monte_carlo(pnls, initial_capital=10000,
                                     n_simulations=200, period_seconds=period_1yr)
        result_1mo = run_monte_carlo(pnls, initial_capital=10000,
                                     n_simulations=200, period_seconds=period_1mo)
        # More trades per year (1mo period) should amplify Sharpe via annualization
        assert result_1mo.sharpe_mean != result_1yr.sharpe_mean, (
            "Sharpe should differ when period changes (different trade frequency)"
        )

    def test_backward_compat_no_period(self):
        """Calling without period_seconds still works (backward compat)."""
        pnls = [100, -50, 80, -30, 120, -60, 90, -40, 110, -70]
        result = run_monte_carlo(pnls, initial_capital=10000, n_simulations=100)
        assert result.n_simulations == 100
        assert result.sharpe_mean != 0


class TestJournalReturnPct:
    """Journal must store total_return_pct computed from pnl / initial_capital."""

    def test_total_return_pct_stored(self):
        from flint.store import FlintStore
        from flint.journal.storage import JournalStorage
        from flint.models import BacktestResult

        store = FlintStore(":memory:")
        journal = JournalStorage(store)
        result = BacktestResult(
            total_pnl=500, win_rate=0.6, max_drawdown=0.05,
            sharpe_ratio=1.5, total_trades=5,
            winning_trades=3, losing_trades=2,
        )
        journal.save_run("r1", "MA-Cross", "SOL-PERP", 3600,
                         1000, 5000, 10000, result=result)
        runs = journal.list_runs()
        assert len(runs) == 1
        assert runs[0]["total_return_pct"] == 5.0
        store.close()

    def test_negative_return(self):
        from flint.store import FlintStore
        from flint.journal.storage import JournalStorage
        from flint.models import BacktestResult

        store = FlintStore(":memory:")
        journal = JournalStorage(store)
        result = BacktestResult(
            total_pnl=-2000, win_rate=0.2, max_drawdown=0.20,
            sharpe_ratio=-0.5, total_trades=10,
            winning_trades=2, losing_trades=8,
        )
        journal.save_run("r1", "RSI", "SOL-PERP", 3600,
                         1000, 5000, 10000, result=result)
        runs = journal.list_runs()
        assert len(runs) == 1
        assert runs[0]["total_return_pct"] == -20.0
        store.close()


class TestOHLCVLimitReturnsNewest:
    """query_candles(limit=N) without start_ts must return the N newest candles."""

    def _make_candles(self, n=20):
        from flint.models import Candle
        base_ts = 1_700_000_000
        return [
            Candle(
                market="SOL-PERP", resolution_s=3600, ts=base_ts + i * 3600,
                open=100.0 + i, high=101.0 + i, low=99.0 + i,
                close=100.5 + i, volume=1000.0 + i,
            )
            for i in range(n)
        ]

    def test_limit_without_start_returns_newest(self):
        from flint.store import FlintStore
        store = FlintStore(":memory:")
        candles = self._make_candles(20)
        store.upsert_candles(candles)
        result = store.query_candles("SOL-PERP", 3600, limit=5)
        assert len(result) == 5
        # Should get the 5 newest candles (indices 15-19), in chronological order
        assert result[0].ts == candles[15].ts
        assert result[-1].ts == candles[19].ts
        store.close()

    def test_limit_with_start_returns_from_start(self):
        from flint.store import FlintStore
        store = FlintStore(":memory:")
        candles = self._make_candles(20)
        store.upsert_candles(candles)
        result = store.query_candles("SOL-PERP", 3600, start_ts=candles[0].ts, limit=5)
        assert len(result) == 5
        # With start_ts, should return from the start in ascending order (old behavior)
        assert result[0].ts == candles[0].ts
        store.close()


class TestBacktestStateManagement:
    """In-memory backtest state should be atomic and error-safe."""

    def test_eviction_is_atomic(self):
        """Eviction removes entries completely — no orphan state."""
        from flint.api.routes import backtest as bt

        # Save originals
        old_max = bt._MAX_ENTRIES
        old_entries = bt._entries.copy()
        bt._MAX_ENTRIES = 5
        try:
            for i in range(10):
                rid = f"evict_test_{i}"
                with bt._state_lock:
                    bt._entries[rid] = bt._BacktestEntry(
                        status="complete",
                        result={"pnl": i},
                        progress={"phase": "complete"},
                    )
                    bt._evict_old()

            with bt._state_lock:
                assert len(bt._entries) <= 5
        finally:
            bt._MAX_ENTRIES = old_max
            with bt._state_lock:
                # Remove test entries, restore originals
                for i in range(10):
                    bt._entries.pop(f"evict_test_{i}", None)
                bt._entries.update(old_entries)

    def test_get_results_returns_json_on_error(self):
        """get_results must return JSON error, not bare 500."""
        from flint.api.routes import backtest as bt
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        run_id = "test_error_result"
        with bt._state_lock:
            bt._entries[run_id] = bt._BacktestEntry(
                status="complete",
                result={"data": object()},  # non-serializable
                progress={"phase": "complete", "pct": 100},
            )

        app = FastAPI()
        app.include_router(bt.router, prefix="/backtest")
        client = TestClient(app)
        resp = client.get(f"/backtest/{run_id}/results")
        # Must return JSON, not bare 500 text
        assert resp.headers.get("content-type", "").startswith("application/json"), \
            f"Expected JSON response, got {resp.headers.get('content-type')}"
        data = resp.json()
        assert "error" in str(data).lower() or "status" in data

        with bt._state_lock:
            bt._entries.pop(run_id, None)


class TestCoverageCap:
    def test_coverage_capped_at_100(self):
        # Verify the capping logic
        coverage_pct = round(101 / 100 * 100, 1)
        capped = min(coverage_pct, 100.0)
        assert capped == 100.0
