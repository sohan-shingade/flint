"""ParityTest -- compare backtest engine vs paper broker on same data.

Answers: "Can I trust my backtest results?"
Runs both engines on identical candle data and compares fills, PnL, equity curves.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from ..models import Candle, FundingRate, Fill, Signal, Side

logger = logging.getLogger("flint.parity")


@dataclass
class ParityReport:
    backtest_pnl: float
    backtest_trades: int
    backtest_equity_curve: List[float]
    paper_pnl: float
    paper_trades: int
    paper_equity_curve: List[float]
    pnl_divergence_pct: float
    fill_price_mae: float
    equity_correlation: float
    trade_count_match: bool
    signal_timing_match_pct: float
    passed: bool
    threshold_pct: float = 2.0

    def to_dict(self) -> dict:
        return {
            "backtest_pnl": self.backtest_pnl,
            "backtest_trades": self.backtest_trades,
            "paper_pnl": self.paper_pnl,
            "paper_trades": self.paper_trades,
            "pnl_divergence_pct": round(self.pnl_divergence_pct, 4),
            "fill_price_mae": round(self.fill_price_mae, 6),
            "equity_correlation": round(self.equity_correlation, 6),
            "trade_count_match": self.trade_count_match,
            "signal_timing_match_pct": round(self.signal_timing_match_pct, 2),
            "passed": self.passed,
            "threshold_pct": self.threshold_pct,
        }

    def summary(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        return (
            f"Backtest PnL:  ${self.backtest_pnl:,.2f}  ({self.backtest_trades} trades)\n"
            f"Paper PnL:     ${self.paper_pnl:,.2f}  ({self.paper_trades} trades)\n"
            f"PnL Divergence: {self.pnl_divergence_pct:.2f}%\n"
            f"Fill Price MAE:  ${self.fill_price_mae:.4f}\n"
            f"Equity Corr:    {self.equity_correlation:.3f}\n"
            f"Signal Match:   {self.signal_timing_match_pct:.0f}%\n"
            f"Result: {verdict} (< {self.threshold_pct}% divergence)"
        )


class ParityTest:
    """Run backtest engine and paper broker on identical data and compare."""

    def __init__(
        self,
        strategy,
        market: str,
        candles: List[Candle],
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.0005,
        funding_rates: Optional[List[FundingRate]] = None,
        threshold_pct: float = 2.0,
    ):
        self._strategy = strategy
        self._market = market
        self._candles = candles
        self._initial_capital = initial_capital
        self._fee_rate = fee_rate
        self._funding_rates = funding_rates
        self._threshold_pct = threshold_pct

    def run(self) -> ParityReport:
        bt_result = self._run_backtest()
        paper_fills, paper_equity = self._run_paper()

        paper_pnl = paper_equity[-1] - self._initial_capital if paper_equity else 0.0
        paper_trades = len(paper_fills)

        bt_pnl = bt_result.total_pnl
        bt_trades = bt_result.total_trades

        pnl_div = abs(bt_pnl - paper_pnl) / max(abs(bt_pnl), 1.0) * 100
        fill_mae = self._compute_fill_mae(bt_result.fills, paper_fills)
        eq_corr = self._compute_correlation(bt_result.equity_curve, paper_equity)
        trade_match = bt_trades == paper_trades
        timing_match = self._compute_timing_match(bt_result.fills, paper_fills)
        passed = pnl_div < self._threshold_pct

        return ParityReport(
            backtest_pnl=bt_pnl,
            backtest_trades=bt_trades,
            backtest_equity_curve=bt_result.equity_curve,
            paper_pnl=paper_pnl,
            paper_trades=paper_trades,
            paper_equity_curve=paper_equity,
            pnl_divergence_pct=pnl_div,
            fill_price_mae=fill_mae,
            equity_correlation=eq_corr,
            trade_count_match=trade_match,
            signal_timing_match_pct=timing_match,
            passed=passed,
            threshold_pct=self._threshold_pct,
        )

    def _run_backtest(self):
        from .engine import BacktestEngine
        from ..execution.fee_models import FlatFeeModel
        from ..execution.fill_models import ClosePriceFill

        self._strategy.reset()
        engine = BacktestEngine(
            strategy=self._strategy,
            initial_capital=self._initial_capital,
            fee_rate=self._fee_rate,
            fill_model=ClosePriceFill(),
            fee_model=FlatFeeModel(fee_bps=self._fee_rate * 10_000),
            funding_rates=self._funding_rates,
        )
        return engine.run(self._candles)

    def _run_paper(self):
        from ..execution.fee_models import FlatFeeModel
        from ..execution.fill_models import ClosePriceFill
        from ..paper.context import PaperContext

        self._strategy.reset()
        fee_model = FlatFeeModel(fee_bps=self._fee_rate * 10_000)
        ctx = PaperContext(
            initial_capital=self._initial_capital,
            fill_model=ClosePriceFill(),
            fee_model=fee_model,
        )

        equity_curve: List[float] = [self._initial_capital]
        all_fills: List[Fill] = []
        history: List[Candle] = []
        has_position = False

        for i, candle in enumerate(self._candles):
            ctx.set_candle(candle)
            history.append(candle)

            try:
                result = self._strategy.on_candle(candle, history, ctx)
                if result == Signal.BUY and not has_position:
                    size = (ctx.cash * 1.0) / candle.close
                    if size > 0:
                        ctx.market_order(candle.market, Side.LONG, size)
                        has_position = True
                elif result == Signal.SELL and has_position:
                    legs = ctx.positions_for_market(candle.market)
                    if legs:
                        pos = legs[0]
                        close_side = Side.SHORT if pos.side == Side.LONG else Side.LONG
                        ctx.market_order(candle.market, close_side, pos.size)
                        has_position = False
            except Exception as e:
                logger.warning("Strategy error at candle %d: %s", i, e)

            fills = ctx.process_candle(candle)
            all_fills.extend(fills)

            # Track position state after fills
            has_position = bool(ctx.positions_for_market(candle.market))

            equity_curve.append(ctx.equity)

        # Close any remaining position at the last candle.
        # Append terminal equity rather than overwriting so the mark-to-market
        # final-bar equity is preserved — matches behavior in engine.py.
        if self._candles and has_position:
            last = self._candles[-1]
            legs = ctx.positions_for_market(last.market)
            if legs:
                pos = legs[0]
                close_side = Side.SHORT if pos.side == Side.LONG else Side.LONG
                ctx.market_order(last.market, close_side, pos.size)
                close_fills = ctx.process_candle(last)
                all_fills.extend(close_fills)
                equity_curve.append(ctx.equity)

        return all_fills, equity_curve

    def _compute_fill_mae(self, bt_fills: List[Fill], paper_fills: List[Fill]) -> float:
        """Mean absolute error between matched fill prices."""
        if not bt_fills or not paper_fills:
            return 0.0
        bt_by_ts = {f.ts: f.price for f in bt_fills}
        errors = []
        for pf in paper_fills:
            if pf.ts in bt_by_ts:
                errors.append(abs(bt_by_ts[pf.ts] - pf.price))
        return sum(errors) / len(errors) if errors else 0.0

    def _compute_correlation(self, curve_a: List[float], curve_b: List[float]) -> float:
        """Pearson correlation between equity curves."""
        import numpy as np

        min_len = min(len(curve_a), len(curve_b))
        if min_len < 2:
            return 1.0
        a = np.array(curve_a[:min_len], dtype=float)
        b = np.array(curve_b[:min_len], dtype=float)
        if np.std(a) == 0 or np.std(b) == 0:
            return 1.0
        return float(np.corrcoef(a, b)[0, 1])

    def _compute_timing_match(self, bt_fills: List[Fill], paper_fills: List[Fill]) -> float:
        """Percentage of backtest fills that have a matching paper fill timestamp."""
        if not bt_fills:
            return 100.0
        paper_ts = {f.ts for f in paper_fills}
        matched = sum(1 for f in bt_fills if f.ts in paper_ts)
        return (matched / len(bt_fills)) * 100
