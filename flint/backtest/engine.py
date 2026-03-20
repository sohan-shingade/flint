"""Backtest engine — feeds candles through a strategy and tracks performance."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..models import BacktestResult, Candle, Position, Signal
from ..strategy.base import Strategy


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.0005,  # 5 bps
        position_size: float = 1.0,  # fraction of capital per trade
    ) -> None:
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.position_size = position_size

    def run(self, candles: List[Candle]) -> BacktestResult:
        self.strategy.reset()
        capital = self.initial_capital
        equity_curve: List[float] = []
        positions: List[Position] = []
        current_pos: Optional[Position] = None
        history: List[Candle] = []

        for candle in candles:
            history.append(candle)
            signal = self.strategy.on_candle(candle, history)
            price = candle.close

            if signal == Signal.BUY and current_pos is None:
                size = (capital * self.position_size) / price
                fee = size * price * self.fee_rate
                capital -= fee
                current_pos = Position(
                    entry_price=price,
                    size=size,
                    entry_ts=candle.ts,
                )

            elif signal == Signal.SELL and current_pos is not None:
                pnl = current_pos.close(price, candle.ts)
                fee = abs(current_pos.size) * price * self.fee_rate
                capital += pnl - fee
                positions.append(current_pos)
                current_pos = None

            unrealised = 0.0
            if current_pos is not None:
                unrealised = (price - current_pos.entry_price) * current_pos.size
            equity_curve.append(capital + unrealised)

        # close open position at last price
        if current_pos is not None and candles:
            last = candles[-1]
            pnl = current_pos.close(last.close, last.ts)
            fee = abs(current_pos.size) * last.close * self.fee_rate
            capital += pnl - fee
            positions.append(current_pos)
            equity_curve[-1] = capital

        winners = [p for p in positions if p.pnl > 0]
        losers = [p for p in positions if p.pnl <= 0]
        total_trades = len(positions)
        win_rate = len(winners) / total_trades if total_trades else 0.0

        # max drawdown
        max_dd = _max_drawdown(equity_curve) if equity_curve else 0.0

        # sharpe ratio (annualised, assuming 1h candles → 8760 periods/yr)
        sharpe = _sharpe_ratio(equity_curve)

        return BacktestResult(
            total_pnl=capital - self.initial_capital,
            win_rate=win_rate,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            total_trades=total_trades,
            winning_trades=len(winners),
            losing_trades=len(losers),
            positions=positions,
            equity_curve=equity_curve,
        )


def _max_drawdown(equity: List[float]) -> float:
    if not equity:
        return 0.0
    arr = np.array(equity)
    peak = np.maximum.accumulate(arr)
    dd = (peak - arr) / np.where(peak == 0, 1, peak)
    return float(np.max(dd))


def _sharpe_ratio(equity: List[float], periods_per_year: float = 8760) -> float:
    if len(equity) < 2:
        return 0.0
    arr = np.array(equity)
    returns = np.diff(arr) / arr[:-1]
    if np.std(returns) == 0:
        return 0.0
    return float(np.mean(returns) / np.std(returns) * np.sqrt(periods_per_year))
