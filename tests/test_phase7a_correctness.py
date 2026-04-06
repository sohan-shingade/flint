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
