"""Tests for ParityTest -- backtest vs paper comparison."""
from flint.models import Candle
from flint.backtest.parity import ParityTest, ParityReport


def _make_candles(n=100, start_ts=0, market="SOL-PERP"):
    candles = []
    price = 100.0
    for i in range(n):
        price = price + 0.5 + (i % 3 - 1) * 0.2
        candles.append(Candle(
            ts=start_ts + i * 3600, open=price - 0.1, high=price + 0.3,
            low=price - 0.3, close=price, volume=1000.0,
            market=market, resolution_s=3600,
        ))
    return candles


class TestParityReport:
    def test_to_dict(self):
        report = ParityReport(
            backtest_pnl=100.0, backtest_trades=5, backtest_equity_curve=[10000, 10100],
            paper_pnl=98.0, paper_trades=5, paper_equity_curve=[10000, 10098],
            pnl_divergence_pct=2.0, fill_price_mae=0.05,
            equity_correlation=0.999, trade_count_match=True,
            signal_timing_match_pct=100.0, passed=True,
        )
        d = report.to_dict()
        assert d["pnl_divergence_pct"] == 2.0
        assert d["passed"] is True

    def test_summary_contains_key_info(self):
        report = ParityReport(
            backtest_pnl=100.0, backtest_trades=5, backtest_equity_curve=[],
            paper_pnl=98.0, paper_trades=5, paper_equity_curve=[],
            pnl_divergence_pct=2.0, fill_price_mae=0.05,
            equity_correlation=0.999, trade_count_match=True,
            signal_timing_match_pct=100.0, passed=True,
        )
        s = report.summary()
        assert "PASS" in s


class TestParityTest:
    def test_produces_report(self):
        candles = _make_candles(100)
        from flint.strategy.momentum import MomentumStrategy
        strategy = MomentumStrategy(lookback=5, threshold_pct=0.5)
        pt = ParityTest(
            strategy=strategy, market="SOL-PERP", candles=candles,
            initial_capital=10000.0, fee_rate=0.0005,
        )
        report = pt.run()
        assert isinstance(report, ParityReport)

    def test_report_has_equity_curves(self):
        candles = _make_candles(50)
        from flint.strategy.momentum import MomentumStrategy
        strategy = MomentumStrategy(lookback=5, threshold_pct=0.5)
        pt = ParityTest(
            strategy=strategy, market="SOL-PERP", candles=candles,
            initial_capital=10000.0, fee_rate=0.0005,
        )
        report = pt.run()
        assert len(report.backtest_equity_curve) > 0
        assert len(report.paper_equity_curve) > 0


class TestCLIParity:
    def test_parity_help(self):
        from typer.testing import CliRunner
        from flint.cli import app
        runner = CliRunner()
        result = runner.invoke(app, ["parity", "--help"])
        assert result.exit_code == 0
        assert "strategy" in result.output.lower() or "market" in result.output.lower()
