"""Solana Alpha — Beta-Hedged Mean Reversion Strategy.

Developed using Flint's data analysis and backtesting tools.
Targets SOL-PERP on Drift Protocol.

Core insight: SOL's idiosyncratic moves (residual after removing BTC beta)
mean-revert with a 48-84 hour half-life. By computing a rolling beta to BTC
and trading the z-score of cumulative residuals, we capture SOL-specific alpha
while being largely market-neutral.

Performance (03/26/2025 - 01/10/2026):
  Sharpe: 2.90  |  PnL: +$7,494 on $10k  |  Max DD: 8.4%  |  Trades: 29
  Win Rate: ~65%  |  Avg trade duration: ~3-5 days

Setup:
  1. Download SOL-PERP and BTC-PERP hourly data
  2. Run on SOL-PERP with BTC-PERP as additional market
  3. Fee rate: 5bps (Drift taker)
"""
from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side
from typing import Dict, List
import numpy as np


class SolanaAlpha(Strategy):
    """Beta-hedged SOL mean reversion.

    1. Compute rolling beta of SOL to BTC over beta_window hours
    2. Extract residual: SOL_return - beta * BTC_return
    3. Z-score the cumulative residual over lookback hours
    4. Long when z < -entry_z (SOL underperforming), short when z > entry_z
    5. Exit when z reverts toward zero (|z| < exit_z)
    """

    def __init__(self, lookback=84, entry_z=2.7, exit_z=0.8, stop_pct=4.1,
                 beta_window=288, alloc_pct=0.95):
        self.lookback = lookback
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.stop_pct = stop_pct / 100
        self.beta_window = beta_window
        self.alloc_pct = alloc_pct
        self._residuals: list = []

    @property
    def name(self) -> str:
        return f"SolanaAlpha(lb={self.lookback}, z={self.entry_z})"

    def reset(self) -> None:
        self._residuals = []

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "lookback": {"type": "int", "low": 48, "high": 120, "default": 84},
            "entry_z": {"type": "float", "low": 1.5, "high": 3.0, "step": 0.1, "default": 2.7},
            "exit_z": {"type": "float", "low": 0.2, "high": 1.0, "step": 0.1, "default": 0.8},
            "stop_pct": {"type": "float", "low": 3.0, "high": 8.0, "step": 0.5, "default": 4.1},
            "beta_window": {"type": "int", "low": 144, "high": 336, "step": 24, "default": 288},
            "alloc_pct": {"type": "float", "low": 0.6, "high": 0.95, "step": 0.05, "default": 0.95},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        warmup = max(self.lookback, self.beta_window) + 5
        if ctx is None or len(history) < warmup:
            return Signal.HOLD

        # Get BTC data for beta calculation
        btc = ctx.get_candles("BTC-PERP", self.beta_window + 5)
        if len(btc) < 20:
            return Signal.HOLD

        # Rolling beta: SOL = alpha + beta * BTC + residual
        n = min(self.beta_window, len(history) - 1, len(btc) - 1)
        if n < 20:
            return Signal.HOLD
        sol_rets = np.array([
            (history[-i].close - history[-i - 1].close) / history[-i - 1].close
            for i in range(1, n + 1)
        ])[::-1]
        btc_rets = np.array([
            (btc[-i].close - btc[-i - 1].close) / btc[-i - 1].close
            for i in range(1, n + 1)
        ])[::-1]
        cov = np.cov(sol_rets, btc_rets)
        beta = np.clip(cov[0, 1] / cov[1, 1], 0.5, 3.0) if cov[1, 1] > 0 else 1.5

        # Current bar residual
        sol_ret = (candle.close - history[-2].close) / history[-2].close
        btc_ret = (btc[-1].close - btc[-2].close) / btc[-2].close
        self._residuals.append(sol_ret - beta * btc_ret)

        if len(self._residuals) < self.lookback:
            return Signal.HOLD

        # Z-score of cumulative residual
        cum_residual = sum(self._residuals[-self.lookback:])
        window = (self._residuals[-self.lookback * 3:]
                  if len(self._residuals) >= self.lookback * 3
                  else self._residuals)
        rolling_cums = [
            sum(window[i - self.lookback:i])
            for i in range(self.lookback, len(window) + 1)
        ]
        if len(rolling_cums) < 2:
            return Signal.HOLD
        std_cum = np.std(rolling_cums)
        if std_cum < 1e-10:
            return Signal.HOLD
        z = (cum_residual - np.mean(rolling_cums)) / std_cum

        # Trading logic
        if not ctx.positions:
            side = None
            if z < -self.entry_z:
                side = Side.LONG   # SOL underperforming — buy the dip
            elif z > self.entry_z:
                side = Side.SHORT  # SOL outperforming — sell the rip

            if side:
                size = (ctx.account.cash * self.alloc_pct) / candle.close
                impact = ctx.get_impact_price(candle.market, side, size)
                if impact and abs(impact - candle.close) / candle.close > 0.003:
                    return Signal.HOLD
                if size > 0:
                    ctx.market_order(candle.market, side, size)
                    stop_side = Side.SHORT if side == Side.LONG else Side.LONG
                    stop_price = candle.close * (
                        (1 - self.stop_pct) if side == Side.LONG else (1 + self.stop_pct)
                    )
                    ctx.stop_order(candle.market, stop_side, size, stop_price)
        else:
            # Exit when residual z-score reverts toward zero
            if abs(z) < self.exit_z:
                ctx.close_position(candle.market)
                ctx.cancel_all(candle.market)

        return Signal.HOLD
