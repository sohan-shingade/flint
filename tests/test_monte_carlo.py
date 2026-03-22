"""Tests for Monte Carlo simulation."""
from flint.analytics.monte_carlo import run_monte_carlo, MonteCarloResult


class TestMonteCarlo:
    def test_basic_simulation(self):
        # 20 trades, mix of wins and losses
        pnls = [100, -50, 80, -30, 120, -60, 90, -40, 110, -70,
                60, -20, 150, -80, 40, -10, 130, -50, 70, -30]
        result = run_monte_carlo(pnls, initial_capital=10000, n_simulations=500)
        assert isinstance(result, MonteCarloResult)
        assert result.n_simulations == 500
        assert result.sharpe_mean != 0
        assert result.sharpe_ci_lower < result.sharpe_ci_upper
        assert result.max_dd_mean > 0

    def test_confidence_intervals(self):
        pnls = [50, -20, 30, -10, 40, -15, 25, -5, 35, -12] * 5
        result = run_monte_carlo(pnls, n_simulations=1000)
        # Sharpe CI varies (path-dependent), so lower < upper
        assert result.sharpe_ci_lower < result.sharpe_ci_upper
        # Total PnL is the same regardless of trade order (sum is invariant),
        # so CI is a single point — that's correct
        assert result.pnl_ci_lower <= result.pnl_ci_upper
        assert result.max_dd_ci_lower < result.max_dd_ci_upper

    def test_p_value_winning_strategy(self):
        # Clearly winning strategy — all positive trades
        pnls = [100, 80, 120, 90, 110, 95, 105, 85, 115, 100] * 3
        result = run_monte_carlo(pnls, n_simulations=500)
        assert result.sharpe_p_value < 0.05  # should be significant
        assert result.pnl_p_value < 0.05

    def test_p_value_losing_strategy(self):
        # Clearly losing strategy
        pnls = [-100, -80, -120, -90, -110, -95, -105, -85, -115, -100]
        result = run_monte_carlo(pnls, n_simulations=500)
        assert result.pnl_p_value > 0.5  # most simulations lose money

    def test_equity_bands(self):
        pnls = [50, -20, 30, -10, 40] * 4
        result = run_monte_carlo(pnls, n_simulations=100)
        assert len(result.equity_p5) == len(pnls) + 1
        assert len(result.equity_p50) == len(pnls) + 1
        assert len(result.equity_p95) == len(pnls) + 1
        # p5 should be below p95
        assert result.equity_p5[-1] <= result.equity_p95[-1]

    def test_probability_of_ruin(self):
        # Strategy with big losses — should have some ruin probability
        pnls = [500, -2000, 500, -2000, 500, -2000, 500, -2000, 500, -2000]
        result = run_monte_carlo(pnls, initial_capital=10000,
                                 n_simulations=500, ruin_threshold=0.50)
        assert result.probability_of_ruin > 0

    def test_empty_trades(self):
        result = run_monte_carlo([], n_simulations=100)
        assert result.n_simulations == 0

    def test_too_few_trades(self):
        result = run_monte_carlo([100], n_simulations=100)
        assert result.n_simulations == 0
