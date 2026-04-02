"""Tests for tx_cost integration."""
import pytest
from flint.models import Fill, BacktestResult, Side

class TestFillTxCost:
    def test_default_zero(self):
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0, fee=0.075, ts=1000)
        assert fill.tx_cost == 0.0

    def test_with_tx_cost(self):
        fill = Fill(market="SOL-PERP", side=Side.LONG, price=150.0, size=10.0,
                    fee=0.075, ts=1000, tx_cost=0.005)
        assert fill.tx_cost == 0.005

class TestBacktestResultTxCosts:
    def test_default_zero(self):
        result = BacktestResult(total_pnl=100, win_rate=0.5, max_drawdown=0.1,
            sharpe_ratio=1.5, total_trades=10, winning_trades=5, losing_trades=5)
        assert result.total_tx_costs == 0.0

    def test_with_tx_costs(self):
        result = BacktestResult(total_pnl=100, win_rate=0.5, max_drawdown=0.1,
            sharpe_ratio=1.5, total_trades=10, winning_trades=5, losing_trades=5,
            total_tx_costs=0.05)
        assert result.total_tx_costs == 0.05
