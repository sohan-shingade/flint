import { useState, useEffect, useCallback, useRef } from 'react'
import { useBacktest } from '../hooks/useBacktest'
import { useStrategies } from '../hooks/useStrategies'
import { useOptimize } from '../hooks/useOptimize'
import { useJournal } from '../hooks/useJournal'
import CodeEditor from '../components/CodeEditor'
import EquityCurve from '../components/EquityCurve'
import DrawdownChart from '../components/DrawdownChart'
import MetricsCard from '../components/MetricsCard'
import TradeTable from '../components/TradeTable'
import PriceChart from '../components/PriceChart'
import PnlHistogram from '../components/PnlHistogram'
import ExposureTimeline from '../components/ExposureTimeline'
import SplitMetrics from '../components/SplitMetrics'

/* ── all supported markets ───────────────────────────── */

const PERP_MARKETS = [
  'SOL-PERP', 'BTC-PERP', 'ETH-PERP', 'APT-PERP', '1MBONK-PERP',
  'POL-PERP', 'ARB-PERP', 'DOGE-PERP', 'BNB-PERP', 'SUI-PERP',
  '1MPEPE-PERP', 'OP-PERP', 'RENDER-PERP', 'XRP-PERP', 'HNT-PERP',
  'INJ-PERP', 'LINK-PERP', 'RLB-PERP', 'PYTH-PERP', 'TIA-PERP',
  'JTO-PERP', 'SEI-PERP', 'AVAX-PERP', 'WIF-PERP', 'JUP-PERP',
  'DYM-PERP', 'TAO-PERP', 'W-PERP', 'KMNO-PERP', 'TNSR-PERP',
  'DRIFT-PERP', 'CLOUD-PERP', 'IO-PERP', 'ZEX-PERP', 'POPCAT-PERP',
  '1KWEN-PERP', 'MOTHER-PERP', 'LTC-PERP', 'RAY-PERP', 'PENGU-PERP',
  'ME-PERP', 'PNUT-PERP',
]

const SPOT_MARKETS = ['SOL', 'JTO', 'WIF', 'JUP', 'DRIFT', 'POPCAT']

const RESOLUTIONS: Record<string, number> = {
  '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400,
}

const FEE_PRESETS: Record<string, { label: string; rate: number; venue: string }> = {
  // Drift Protocol
  'drift_taker':     { label: 'Drift Taker (10bps)',   rate: 0.001,   venue: 'drift' },
  'drift_maker':     { label: 'Drift Maker (-2bps)',   rate: -0.0002, venue: 'drift' },
  'drift_vip':       { label: 'Drift VIP (6bps)',      rate: 0.0006,  venue: 'drift' },
  // Hyperliquid
  'hl_taker':        { label: 'Hyperliquid Taker (3.5bps)', rate: 0.00035, venue: 'hyperliquid' },
  'hl_maker':        { label: 'Hyperliquid Maker (1bps)',   rate: 0.0001,  venue: 'hyperliquid' },
  // Binance Futures
  'binance_taker':   { label: 'Binance Taker (4.5bps)', rate: 0.00045, venue: 'binance' },
  'binance_maker':   { label: 'Binance Maker (2bps)',   rate: 0.0002,  venue: 'binance' },
  // OKX
  'okx_taker':       { label: 'OKX Taker (5bps)',       rate: 0.0005,  venue: 'okx' },
  'okx_maker':       { label: 'OKX Maker (2bps)',       rate: 0.0002,  venue: 'okx' },
  // Bybit
  'bybit_taker':     { label: 'Bybit Taker (5.5bps)',   rate: 0.00055, venue: 'bybit' },
  'bybit_maker':     { label: 'Bybit Maker (2bps)',      rate: 0.0002,  venue: 'bybit' },
  // Generic
  'flat_5bps':       { label: 'Flat 5bps',              rate: 0.0005,  venue: 'generic' },
  'flat_1bps':       { label: 'Flat 1bps',              rate: 0.0001,  venue: 'generic' },
  'zero':            { label: 'Zero Fees',              rate: 0,       venue: 'generic' },
}

/* ── strategy templates ────────────────────────────────── */

const BLANK_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List


class MyStrategy(Strategy):
    @property
    def name(self) -> str:
        return "my_strategy"

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        # Your logic here
        return Signal.HOLD

    def reset(self) -> None:
        pass
`

const MA_CROSSOVER_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class MACrossover(Strategy):
    """Moving Average Crossover — trend following.

    Buys when fast SMA crosses above slow SMA (golden cross).
    Sells when fast crosses below slow (death cross).
    """
    def __init__(self, fast_period=10, slow_period=30):
        self.fast = fast_period
        self.slow = slow_period
        self._prev_fast = None
        self._prev_slow = None

    @property
    def name(self) -> str:
        return f"MA-Crossover({self.fast}/{self.slow})"

    @classmethod
    def parameters(cls):
        return {
            "fast_period": {"type": "int", "low": 5, "high": 50, "default": 10},
            "slow_period": {"type": "int", "low": 20, "high": 200, "default": 30},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.slow:
            return Signal.HOLD
        closes = [c.close for c in history[-self.slow:]]
        fast_ma = float(np.mean(closes[-self.fast:]))
        slow_ma = float(np.mean(closes))
        signal = Signal.HOLD
        if self._prev_fast is not None:
            if self._prev_fast <= self._prev_slow and fast_ma > slow_ma:
                signal = Signal.BUY
            elif self._prev_fast >= self._prev_slow and fast_ma < slow_ma:
                signal = Signal.SELL
        self._prev_fast = fast_ma
        self._prev_slow = slow_ma
        return signal

    def reset(self) -> None:
        self._prev_fast = None
        self._prev_slow = None
`

const RSI_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class RSIMeanReversion(Strategy):
    """RSI Mean Reversion — buy oversold, sell overbought."""
    def __init__(self, period=14, oversold=30, overbought=70):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    @property
    def name(self) -> str:
        return f"RSI({self.period})"

    @classmethod
    def parameters(cls):
        return {
            "period": {"type": "int", "low": 5, "high": 30, "default": 14},
            "oversold": {"type": "float", "low": 15, "high": 40, "default": 30},
            "overbought": {"type": "float", "low": 60, "high": 85, "default": 70},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.period + 1:
            return Signal.HOLD
        closes = [c.close for c in history[-(self.period + 1):]]
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = float(np.mean(gains)) if len(gains) > 0 else 0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0
        if avg_loss == 0:
            rsi = 100.0
        else:
            rsi = 100 - (100 / (1 + avg_gain / avg_loss))
        if rsi < self.oversold:
            return Signal.BUY
        elif rsi > self.overbought:
            return Signal.SELL
        return Signal.HOLD

    def reset(self) -> None:
        pass
`

const MEAN_REVERSION_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class MeanReversion(Strategy):
    """Z-Score Mean Reversion with stop-loss.

    Buys when price is N standard deviations below the rolling mean.
    Sells when price reverts. Solana perps mean-revert well on hourly.
    """
    def __init__(self, period=20, entry_z=2.0, exit_z=0.5):
        self.period = period
        self.entry_z = entry_z
        self.exit_z = exit_z

    @property
    def name(self) -> str:
        return f"MeanReversion(z={self.entry_z})"

    @classmethod
    def parameters(cls):
        return {
            "period": {"type": "int", "low": 10, "high": 50, "default": 20},
            "entry_z": {"type": "float", "low": 1.0, "high": 3.5, "default": 2.0},
            "exit_z": {"type": "float", "low": 0.0, "high": 1.5, "default": 0.5},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.period:
            return Signal.HOLD
        closes = np.array([c.close for c in history[-self.period:]])
        mean = float(np.mean(closes))
        std = float(np.std(closes))
        if std == 0:
            return Signal.HOLD
        z = (candle.close - mean) / std
        if z <= -self.entry_z:
            return Signal.BUY
        elif z >= self.entry_z:
            return Signal.SELL
        elif abs(z) <= self.exit_z:
            return Signal.SELL
        return Signal.HOLD

    def reset(self) -> None:
        pass
`

const BREAKOUT_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class BreakoutMomentum(Strategy):
    """Breakout with volume confirmation.

    Enters on breakout above N-period high when volume spikes.
    Uses ATR-based trailing stop concept for exits.
    """
    def __init__(self, lookback=20, volume_mult=1.5):
        self.lookback = lookback
        self.volume_mult = volume_mult

    @property
    def name(self) -> str:
        return f"Breakout({self.lookback})"

    @classmethod
    def parameters(cls):
        return {
            "lookback": {"type": "int", "low": 10, "high": 50, "default": 20},
            "volume_mult": {"type": "float", "low": 1.0, "high": 3.0, "default": 1.5},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.lookback + 1:
            return Signal.HOLD
        window = history[-(self.lookback + 1):-1]
        highest = max(c.high for c in window)
        avg_vol = sum(c.volume for c in window) / len(window)
        if candle.close > highest and candle.volume > avg_vol * self.volume_mult:
            return Signal.BUY
        # Exit on weakness
        lowest = min(c.low for c in window[-5:]) if len(window) >= 5 else candle.low
        if candle.close < lowest:
            return Signal.SELL
        return Signal.HOLD

    def reset(self) -> None:
        pass
`

const FUNDING_HARVEST_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List


class FundingHarvest(Strategy):
    """Funding Rate Harvest — Solana-native strategy.

    Collects funding payments by trading against the crowd.
    Goes long when funding is deeply negative (longs get paid).
    Uses price deviation from mean as a proxy for funding direction.

    On Drift, funding is paid hourly. Negative funding = shorts pay longs.
    """
    def __init__(self, lookback=24, threshold_pct=2.0):
        self.lookback = lookback
        self.threshold = threshold_pct / 100

    @property
    def name(self) -> str:
        return f"FundingHarvest({self.lookback})"

    @classmethod
    def parameters(cls):
        return {
            "lookback": {"type": "int", "low": 8, "high": 72, "default": 24},
            "threshold_pct": {"type": "float", "low": 0.5, "high": 5.0, "default": 2.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.lookback:
            return Signal.HOLD
        closes = [c.close for c in history[-self.lookback:]]
        mean_price = sum(closes) / len(closes)
        deviation = (candle.close - mean_price) / mean_price
        if deviation < -self.threshold:
            return Signal.BUY
        elif deviation > self.threshold:
            return Signal.SELL
        return Signal.HOLD

    def reset(self) -> None:
        pass
`

const GRID_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List


class GridTrader(Strategy):
    """Grid Trading — buy low, sell high in ranging markets.

    Buys when price drops below the rolling mean by grid_pct.
    Sells when price rises above the rolling mean by grid_pct.
    Works best on sideways/ranging markets.
    """
    def __init__(self, grid_pct=2.0, lookback=20):
        self.grid_pct = grid_pct / 100
        self.lookback = lookback
        self._in_position = False

    @property
    def name(self) -> str:
        return f"Grid({self.grid_pct*100:.1f}%)"

    @classmethod
    def parameters(cls):
        return {
            "grid_pct": {"type": "float", "low": 0.5, "high": 5.0, "default": 2.0},
            "lookback": {"type": "int", "low": 10, "high": 50, "default": 20},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.lookback:
            return Signal.HOLD

        closes = [c.close for c in history[-self.lookback:]]
        mid = sum(closes) / len(closes)

        if candle.close < mid * (1 - self.grid_pct) and not self._in_position:
            self._in_position = True
            return Signal.BUY
        elif candle.close > mid * (1 + self.grid_pct) and self._in_position:
            self._in_position = False
            return Signal.SELL
        return Signal.HOLD

    def reset(self) -> None:
        self._in_position = False
`

const DUAL_TF_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class DualTimeframe(Strategy):
    """Dual Timeframe — trend + entry alignment.

    Uses long-period SMA for trend direction, short-period momentum for entry.
    Only enters in the direction of the higher-timeframe trend.
    """
    def __init__(self, trend_period=50, entry_period=10, threshold=0.02):
        self.trend_period = trend_period
        self.entry_period = entry_period
        self.threshold = threshold

    @property
    def name(self) -> str:
        return f"DualTF({self.trend_period}/{self.entry_period})"

    @classmethod
    def parameters(cls):
        return {
            "trend_period": {"type": "int", "low": 30, "high": 100, "default": 50},
            "entry_period": {"type": "int", "low": 5, "high": 20, "default": 10},
            "threshold": {"type": "float", "low": 0.005, "high": 0.05, "default": 0.02},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.trend_period:
            return Signal.HOLD
        long_closes = np.array([c.close for c in history[-self.trend_period:]])
        trend_up = float(np.mean(long_closes)) > float(np.mean(long_closes[:-1]))
        short = [c.close for c in history[-self.entry_period:]]
        momentum = (short[-1] - short[0]) / short[0] if short[0] else 0
        if trend_up and momentum > self.threshold:
            return Signal.BUY
        elif not trend_up and momentum < -self.threshold:
            return Signal.SELL
        return Signal.HOLD

    def reset(self) -> None:
        pass
`

const CROSS_MARKET_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List


class BtcCorrelation(Strategy):
    """Cross-Market Correlation — trade SOL based on BTC momentum.

    Uses ctx.get_candles('BTC-PERP') to peek at BTC while trading SOL.
    Goes long SOL when BTC shows strong upward momentum.
    Demonstrates multi-market data access (v2 feature).

    Run on SOL-PERP with BTC-PERP data also in the DB.
    """
    def __init__(self, btc_lookback=12, threshold_pct=2.0):
        self.btc_lookback = btc_lookback
        self.threshold = threshold_pct / 100

    @property
    def name(self) -> str:
        return f"BTC-Correlation({self.btc_lookback})"

    @classmethod
    def parameters(cls):
        return {
            "btc_lookback": {"type": "int", "low": 6, "high": 48, "default": 12},
            "threshold_pct": {"type": "float", "low": 0.5, "high": 5.0, "default": 2.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.btc_lookback:
            return Signal.HOLD

        # Check BTC momentum via cross-market access
        if ctx is not None:
            btc = ctx.get_candles("BTC-PERP", self.btc_lookback)
            if len(btc) >= 2:
                btc_return = (btc[-1].close - btc[0].close) / btc[0].close
                if btc_return > self.threshold:
                    return Signal.BUY
                elif btc_return < -self.threshold:
                    return Signal.SELL
                return Signal.HOLD

        # Fallback: use SOL momentum when no cross-market data
        old = history[-self.btc_lookback].close
        ret = (candle.close - old) / old
        if ret > self.threshold:
            return Signal.BUY
        elif ret < -self.threshold:
            return Signal.SELL
        return Signal.HOLD

    def reset(self) -> None:
        pass
`

const STOP_LOSS_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side
from typing import List
import numpy as np


class MomentumWithStops(Strategy):
    """Momentum with Stop-Loss & Take-Profit (v2 Context API).

    Demonstrates the ExecutionContext order management:
    - ctx.market_order() to enter positions
    - ctx.stop_order() to attach stop-loss
    - ctx.take_profit_order() to set profit target

    Enters on breakout with automatic risk management.
    """
    def __init__(self, lookback=20, entry_pct=3.0, stop_pct=2.0, tp_pct=6.0):
        self.lookback = lookback
        self.entry_pct = entry_pct / 100
        self.stop_pct = stop_pct / 100
        self.tp_pct = tp_pct / 100
        self._in_trade = False

    @property
    def name(self) -> str:
        return f"MomentumStops({self.lookback})"

    @classmethod
    def parameters(cls):
        return {
            "lookback": {"type": "int", "low": 10, "high": 50, "default": 20},
            "entry_pct": {"type": "float", "low": 1.0, "high": 8.0, "default": 3.0},
            "stop_pct": {"type": "float", "low": 1.0, "high": 5.0, "default": 2.0},
            "tp_pct": {"type": "float", "low": 2.0, "high": 15.0, "default": 6.0},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.lookback:
            return Signal.HOLD

        if ctx is not None:
            # v2 mode: use context for order management
            if not ctx.positions and not self._in_trade:
                old_price = history[-self.lookback].close
                ret = (candle.close - old_price) / old_price

                if ret > self.entry_pct:
                    # Strong upward momentum — go long with stops
                    size = (ctx.account.cash * 0.9) / candle.close
                    if size > 0:
                        ctx.market_order(candle.market, Side.LONG, size)
                        ctx.stop_order(candle.market, Side.SHORT, size,
                                       candle.close * (1 - self.stop_pct))
                        ctx.take_profit_order(candle.market, Side.SHORT, size,
                                              candle.close * (1 + self.tp_pct))
                        self._in_trade = True

            elif ctx.positions:
                self._in_trade = True
            else:
                self._in_trade = False

            return Signal.HOLD

        # v1 fallback
        old_price = history[-self.lookback].close
        ret = (candle.close - old_price) / old_price
        if ret > self.entry_pct:
            return Signal.BUY
        elif ret < -self.stop_pct:
            return Signal.SELL
        return Signal.HOLD

    def reset(self) -> None:
        self._in_trade = False
`

const VOLATILITY_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class VolatilityBreakout(Strategy):
    """Volatility Breakout — trade expansions from low-vol squeezes.

    Detects when volatility compresses (Bollinger Band width narrows),
    then enters on the breakout direction. Uses ATR for position sizing.
    """
    def __init__(self, bb_period=20, squeeze_threshold=0.03, atr_period=14):
        self.bb_period = bb_period
        self.squeeze_threshold = squeeze_threshold
        self.atr_period = atr_period
        self._was_squeezed = False

    @property
    def name(self) -> str:
        return f"VolBreakout({self.bb_period})"

    @classmethod
    def parameters(cls):
        return {
            "bb_period": {"type": "int", "low": 10, "high": 40, "default": 20},
            "squeeze_threshold": {"type": "float", "low": 0.01, "high": 0.06, "default": 0.03},
            "atr_period": {"type": "int", "low": 7, "high": 21, "default": 14},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < max(self.bb_period, self.atr_period) + 1:
            return Signal.HOLD

        closes = np.array([c.close for c in history[-self.bb_period:]])
        sma = float(np.mean(closes))
        std = float(np.std(closes))
        if sma == 0:
            return Signal.HOLD

        # Bollinger Band width as % of price
        bb_width = (2 * std) / sma

        # Squeeze detection
        is_squeezed = bb_width < self.squeeze_threshold

        if self._was_squeezed and not is_squeezed:
            # Breakout from squeeze
            if candle.close > sma:
                self._was_squeezed = False
                return Signal.BUY
            elif candle.close < sma:
                self._was_squeezed = False
                return Signal.SELL

        self._was_squeezed = is_squeezed

        # Exit when price returns to mean
        if not is_squeezed and abs(candle.close - sma) / sma < 0.005:
            return Signal.SELL

        return Signal.HOLD

    def reset(self) -> None:
        self._was_squeezed = False
`

const MULTI_INDICATOR_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class MultiIndicator(Strategy):
    """Multi-Indicator Confluence — RSI + MACD + Volume confirmation.

    Only enters when multiple indicators agree. Demonstrates
    combining several technical signals for higher-conviction trades.
    Fewer trades but better win rate.
    """
    def __init__(self, rsi_period=14, macd_fast=12, macd_slow=26, vol_mult=1.5):
        self.rsi_period = rsi_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.vol_mult = vol_mult
        self._prev_macd = 0.0
        self._prev_signal = 0.0

    @property
    def name(self) -> str:
        return "MultiIndicator"

    @classmethod
    def parameters(cls):
        return {
            "rsi_period": {"type": "int", "low": 7, "high": 21, "default": 14},
            "macd_fast": {"type": "int", "low": 8, "high": 16, "default": 12},
            "macd_slow": {"type": "int", "low": 20, "high": 32, "default": 26},
            "vol_mult": {"type": "float", "low": 1.0, "high": 3.0, "default": 1.5},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        n = max(self.macd_slow + 9, self.rsi_period + 1, 20)
        if len(history) < n:
            return Signal.HOLD

        closes = np.array([c.close for c in history[-n:]])
        volumes = np.array([c.volume for c in history[-20:]])

        # RSI
        deltas = np.diff(closes[-(self.rsi_period + 1):])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = float(np.mean(gains)) if len(gains) > 0 else 0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 1
        rsi = 100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 50

        # MACD
        ema_fast = float(np.mean(closes[-self.macd_fast:]))
        ema_slow = float(np.mean(closes[-self.macd_slow:]))
        macd = ema_fast - ema_slow
        signal_line = (macd + self._prev_macd) / 2

        # Volume confirmation
        avg_vol = float(np.mean(volumes)) if len(volumes) > 0 else 1
        vol_spike = candle.volume > avg_vol * self.vol_mult

        # Confluence: buy when RSI oversold + MACD cross up + volume spike
        buy_signal = (rsi < 35 and macd > signal_line and
                      self._prev_macd <= self._prev_signal and vol_spike)

        # Confluence: sell when RSI overbought + MACD cross down
        sell_signal = (rsi > 65 and macd < signal_line and
                       self._prev_macd >= self._prev_signal)

        self._prev_macd = macd
        self._prev_signal = signal_line

        if buy_signal:
            return Signal.BUY
        elif sell_signal:
            return Signal.SELL
        return Signal.HOLD

    def reset(self) -> None:
        self._prev_macd = 0.0
        self._prev_signal = 0.0
`

const SCALPER_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class Scalper(Strategy):
    """Scalper — fast mean-reversion on short timeframes.

    Enters on extreme short-term deviation from VWAP-like average.
    Exits quickly near the mean. Designed for 5m-15m candles.
    High trade frequency, small gains per trade.
    """
    def __init__(self, window=10, entry_dev=0.008, exit_dev=0.002):
        self.window = window
        self.entry_dev = entry_dev
        self.exit_dev = exit_dev

    @property
    def name(self) -> str:
        return f"Scalper({self.window})"

    @classmethod
    def parameters(cls):
        return {
            "window": {"type": "int", "low": 5, "high": 20, "default": 10},
            "entry_dev": {"type": "float", "low": 0.003, "high": 0.02, "default": 0.008},
            "exit_dev": {"type": "float", "low": 0.001, "high": 0.005, "default": 0.002},
        }

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.window:
            return Signal.HOLD

        # Volume-weighted average price (approximation)
        recent = history[-self.window:]
        total_vol = sum(c.volume for c in recent)
        if total_vol == 0:
            return Signal.HOLD
        vwap = sum(c.close * c.volume for c in recent) / total_vol

        deviation = (candle.close - vwap) / vwap

        if deviation < -self.entry_dev:
            return Signal.BUY    # price well below VWAP — buy dip
        elif deviation > self.entry_dev:
            return Signal.SELL   # price well above VWAP — sell rip
        elif abs(deviation) < self.exit_dev:
            return Signal.SELL   # near VWAP — close position

        return Signal.HOLD

    def reset(self) -> None:
        pass
`

interface TemplateInfo {
  label: string
  code: string
  category: string
  hint?: string
}

const TEMPLATES: Record<string, TemplateInfo> = {
  blank:       { label: 'Blank Strategy',       code: BLANK_TEMPLATE,           category: 'basic' },
  ma_crossover:{ label: 'MA Crossover',         code: MA_CROSSOVER_TEMPLATE,    category: 'trend',
    hint: 'Any perp market, 1h. Needs 30+ days of data.' },
  rsi:         { label: 'RSI Mean Reversion',    code: RSI_TEMPLATE,             category: 'mean-rev',
    hint: 'Any market, 1h. Needs 15+ candles.' },
  mean_rev:    { label: 'Z-Score Reversion',     code: MEAN_REVERSION_TEMPLATE,  category: 'mean-rev',
    hint: 'Any market, 1h. Best in ranging markets.' },
  breakout:    { label: 'Breakout Momentum',     code: BREAKOUT_TEMPLATE,        category: 'trend',
    hint: 'Any market, 1h-4h. Volume confirmation.' },
  vol_breakout:{ label: 'Volatility Breakout',   code: VOLATILITY_TEMPLATE,      category: 'trend',
    hint: 'Any market, 1h. Trades squeeze-to-expansion. 30+ days.' },
  multi_ind:   { label: 'Multi-Indicator',       code: MULTI_INDICATOR_TEMPLATE, category: 'mean-rev',
    hint: 'Any market, 1h. RSI + MACD + volume confluence. Fewer but higher-quality signals.' },
  scalper:     { label: 'Scalper (VWAP)',        code: SCALPER_TEMPLATE,         category: 'mean-rev',
    hint: 'Any market, best on 5m-15m. High frequency, small gains. Needs tight spreads.' },
  funding:     { label: 'Funding Harvest',       code: FUNDING_HARVEST_TEMPLATE, category: 'solana',
    hint: 'Perp markets only. Uses funding rate proxy. 1h.' },
  grid:        { label: 'Grid Trader',           code: GRID_TEMPLATE,            category: 'solana',
    hint: 'Any perp. Best in ranging/sideways markets. 1h.' },
  dual_tf:     { label: 'Dual Timeframe',        code: DUAL_TF_TEMPLATE,         category: 'trend',
    hint: 'Any market, 1h. Trend + momentum alignment. 60+ days.' },
  cross_mkt:   { label: 'BTC Correlation',       code: CROSS_MARKET_TEMPLATE,    category: 'advanced',
    hint: 'Run on SOL-PERP. Uses ctx.get_candles("BTC-PERP") for cross-market signals. Needs BTC data in DB.' },
  stop_loss:   { label: 'Momentum + Stops (v2)', code: STOP_LOSS_TEMPLATE,       category: 'advanced',
    hint: 'Any perp, 1h. Demonstrates v2 context API: market_order + stop_order + take_profit_order.' },
}

/* ── run history entry ───────────────────────────────── */

interface RunRecord {
  id: string
  strategy: string
  market: string
  resolution: string
  pnl: number
  sharpe: number
  trades: number
  ts: number
}

/* ── component ─────────────────────────────────────────── */

export default function BacktestLab() {
  const { run, status, results, error, progress } = useBacktest()
  const { run: runOptimize, status: optStatus, results: optResults, error: optError, progress: optProgress } = useOptimize()
  const { runs: journalRuns, refresh: refreshJournal, deleteRun: deleteJournalRun } = useJournal()
  const [showJournal, setShowJournal] = useState(false)
  const [optTrials, setOptTrials] = useState(50)
  const [optMetric, setOptMetric] = useState('sharpe_ratio')
  const { strategies: savedStrategies, save, load, remove, validate, loading: savingStrategy } = useStrategies()

  // Editor state
  const [code, setCode] = useState(BLANK_TEMPLATE)
  const [currentName, setCurrentName] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)

  // Config state
  const [market, setMarket] = useState('SOL-PERP')
  const [resolution, setResolution] = useState('1h')
  const [startDate, setStartDate] = useState('2025-01-01')
  const [endDate, setEndDate] = useState('2025-01-31')
  const [capital, setCapital] = useState(10000)
  const [feePreset, setFeePreset] = useState('drift_taker')
  const [feeRate, setFeeRate] = useState(0.001)

  // Run history (session-local)
  const [runHistory, setRunHistory] = useState<RunRecord[]>([])
  const [showHistory, setShowHistory] = useState(false)

  // Available markets from API
  const [availableMarkets, setAvailableMarkets] = useState<string[]>([])

  // Data availability
  const [dataCheck, setDataCheck] = useState<{
    has_data: boolean, covers_range: boolean, will_download: boolean,
    candle_count: number, total_in_db: number,
  } | null>(null)
  const [dataCheckLoading, setDataCheckLoading] = useState(false)
  const [configError, setConfigError] = useState<string | null>(null)

  // Active template hint
  const [activeTemplateKey, setActiveTemplateKey] = useState<string | null>(null)

  const codeRef = useRef(code)
  codeRef.current = code

  // Fetch available markets on mount + auto-detect best date range
  useEffect(() => {
    fetch('/api/v1/data/markets')
      .then(r => r.json())
      .then(data => {
        if (data.markets && data.markets.length > 0) {
          const nameSet: Set<string> = new Set(data.markets.map((m: any) => m.market))
          setAvailableMarkets(Array.from(nameSet))

          // Auto-set date range to match available data for current market
          const marketData = data.markets.find((m: any) => m.market === market && m.resolution_s === (RESOLUTIONS[resolution] || 3600))
          if (marketData && marketData.first_ts && marketData.last_ts) {
            const first = new Date(marketData.first_ts * 1000)
            const last = new Date(marketData.last_ts * 1000)
            // Use last 90 days of available data, or full range if shorter
            const ninetyDaysAgo = new Date(last.getTime() - 90 * 86400000)
            const autoStart = ninetyDaysAgo > first ? ninetyDaysAgo : first
            setStartDate(autoStart.toISOString().split('T')[0])
            setEndDate(last.toISOString().split('T')[0])
          }
        }
      })
      .catch(() => {})
  }, [])

  // Validate config and check data availability on every change
  useEffect(() => {
    const startTs = Math.floor(new Date(startDate + 'T00:00:00Z').getTime() / 1000)
    const endTs = Math.floor(new Date(endDate + 'T23:59:59Z').getTime() / 1000)
    const res_s = RESOLUTIONS[resolution] || 3600

    // Validate fields
    if (!startDate || !endDate) {
      setConfigError('Enter start and end dates')
      setDataCheck(null)
      return
    }
    if (isNaN(startTs) || isNaN(endTs)) {
      setConfigError('Invalid date format — use YYYY-MM-DD')
      setDataCheck(null)
      return
    }
    if (startTs >= endTs) {
      setConfigError('Start date must be before end date')
      setDataCheck(null)
      return
    }
    const durationH = (endTs - startTs) / 3600
    if (durationH < 10 * (res_s / 3600)) {
      setConfigError(`Range too short — need at least ${(10 * res_s / 3600).toFixed(0)}h for ${resolution} candles`)
      setDataCheck(null)
      return
    }
    if (capital <= 0) {
      setConfigError('Capital must be > 0')
      setDataCheck(null)
      return
    }

    setConfigError(null)
    setDataCheckLoading(true)

    const controller = new AbortController()
    fetch(`/api/v1/data/check?market=${encodeURIComponent(market)}&resolution_s=${res_s}&start_ts=${startTs}&end_ts=${endTs}`, { signal: controller.signal })
      .then(r => r.json())
      .then(d => {
        if (d && typeof d.candle_count === 'number' && typeof d.has_data === 'boolean') {
          setDataCheck(d)
        } else {
          setDataCheck(null)
        }
        setDataCheckLoading(false)
      })
      .catch(() => {
        setDataCheck(null)
        setDataCheckLoading(false)
      })

    return () => controller.abort()
  }, [market, resolution, startDate, endDate, capital])

  // Sync fee rate with preset
  useEffect(() => {
    const preset = FEE_PRESETS[feePreset]
    if (preset) setFeeRate(preset.rate)
  }, [feePreset])

  // Save to run history when results arrive
  useEffect(() => {
    if (status === 'complete' && results) {
      setRunHistory(prev => [{
        id: Math.random().toString(36).slice(2, 8),
        strategy: results.strategy_name || 'custom',
        market: results.market || market,
        resolution,
        pnl: results.metrics?.total_pnl ?? 0,
        sharpe: results.metrics?.sharpe_ratio ?? 0,
        trades: results.trades?.length ?? 0,
        ts: Date.now(),
      }, ...prev].slice(0, 20))
    }
  }, [status, results])

  /* ── handlers ──────────────────────────────────────── */

  const handleCodeChange = useCallback((value: string) => {
    setCode(value)
    setDirty(true)
    setValidationError(null)
  }, [])

  const handleTemplateSelect = useCallback((key: string) => {
    if (dirty && !window.confirm('You have unsaved changes. Discard?')) return
    const tpl = TEMPLATES[key]
    if (tpl) {
      setCode(tpl.code)
      setCurrentName(null)
      setDirty(false)
      setValidationError(null)
      setActiveTemplateKey(key)
    }
  }, [dirty])

  const handleLoadStrategy = useCallback(async (name: string) => {
    if (dirty && !window.confirm('You have unsaved changes. Discard?')) return
    const loaded = await load(name)
    setCode(loaded)
    setCurrentName(name)
    setDirty(false)
    setValidationError(null)
    setActiveTemplateKey(null)
  }, [load, dirty])

  const handleDeleteStrategy = useCallback(async (name: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (window.confirm(`Delete strategy "${name}"?`)) {
      await remove(name)
      if (currentName === name) {
        setCurrentName(null)
        setCode(BLANK_TEMPLATE)
        setDirty(false)
      }
    }
  }, [remove, currentName])

  const handleSave = useCallback(async () => {
    let name = currentName
    if (!name) {
      const input = window.prompt('Strategy name:')
      if (!input) return
      name = input.trim().replace(/\s+/g, '_').toLowerCase()
      if (!name) return
    }
    await save(name, codeRef.current)
    setCurrentName(name)
    setDirty(false)
  }, [currentName, save])

  const handleValidate = useCallback(async () => {
    const result = await validate(codeRef.current)
    if (result.valid) {
      setValidationError(null)
    } else {
      setValidationError(result.error || 'Validation failed')
    }
    return result.valid
  }, [validate])

  const handleRun = useCallback(async () => {
    setValidationError(null)
    const startTs = Math.floor(new Date(startDate + 'T00:00:00Z').getTime() / 1000)
    const endTs = Math.floor(new Date(endDate + 'T23:59:59Z').getTime() / 1000)

    // Client-side date validation
    if (isNaN(startTs) || isNaN(endTs)) {
      setValidationError('Invalid date format')
      return
    }
    if (startTs >= endTs) {
      setValidationError('Start date must be before end date')
      return
    }
    const res_s = RESOLUTIONS[resolution] || 3600
    const durationH = (endTs - startTs) / 3600
    const minCandles = 10
    if (durationH < minCandles * (res_s / 3600)) {
      setValidationError(`Date range too short — need at least ${minCandles} candles (${(minCandles * res_s / 3600).toFixed(0)}h for ${resolution})`)
      return
    }

    run({
      strategy: 'custom',
      code: codeRef.current,
      market,
      resolution_s: res_s,
      start_ts: startTs,
      end_ts: endTs,
      initial_capital: capital,
      fee_rate: Math.max(feeRate, 0),
      params: {},
    })
  }, [run, market, resolution, startDate, endDate, capital, feeRate])

  const handleOptimize = useCallback(async () => {
    setValidationError(null)
    const startTs = Math.floor(new Date(startDate + 'T00:00:00Z').getTime() / 1000)
    const endTs = Math.floor(new Date(endDate + 'T23:59:59Z').getTime() / 1000)
    if (isNaN(startTs) || isNaN(endTs) || startTs >= endTs) {
      setValidationError('Invalid date range for optimization')
      return
    }
    runOptimize({
      code: codeRef.current,
      market,
      resolution_s: RESOLUTIONS[resolution] || 3600,
      start_ts: startTs,
      end_ts: endTs,
      initial_capital: capital,
      fee_rate: Math.max(feeRate, 0),
      metric: optMetric,
      trials: optTrials,
    })
  }, [runOptimize, market, resolution, startDate, endDate, capital, feeRate, optMetric, optTrials])

  // Refresh journal when a backtest completes
  useEffect(() => {
    if (status === 'complete') refreshJournal()
  }, [status, refreshJournal])

  const handleExport = useCallback(() => {
    if (!results) return
    const blob = new Blob([JSON.stringify(results, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `flint-${results.strategy_name}-${results.market}-${Date.now()}.json`
    a.click()
    URL.revokeObjectURL(url)
  }, [results])

  /* ── keyboard shortcuts ────────────────────────────── */

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        handleSave()
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault()
        handleRun()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handleSave, handleRun])

  /* ── style helpers ─────────────────────────────────── */

  const inputClass = 'w-full bg-void border border-border text-terminal text-xs px-2.5 py-2 focus:border-amber/50 focus:outline-none transition-colors'
  const labelClass = 'block text-[10px] text-ghost tracking-[0.15em] mb-1.5'

  return (
    <div className="space-y-4">
      {/* ── header ─────────────────────────────────────── */}
      <div className="flex items-baseline gap-4 flex-wrap">
        <h1 className="font-[var(--font-display)] text-2xl text-white/90 italic">Strategy Lab</h1>
        <span className="text-[10px] text-ghost tracking-[0.2em]">
          // {currentName ? currentName.toUpperCase() : 'UNSAVED'}
          {dirty && <span className="text-amber ml-1">*</span>}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setShowHistory(!showHistory)}
            className={`px-2.5 py-1 text-[10px] tracking-[0.1em] border transition-all ${
              showHistory ? 'border-amber/50 text-amber' : 'border-border text-ghost hover:text-terminal'
            }`}
          >
            HISTORY ({runHistory.length})
          </button>
          <select
            onChange={(e) => handleTemplateSelect(e.target.value)}
            value=""
            className="bg-void border border-border text-ghost text-[11px] px-2 py-1.5 focus:border-amber/50 focus:outline-none"
          >
            <option value="" disabled>Start from template...</option>
            <optgroup label="Basic">
              {Object.entries(TEMPLATES).filter(([, t]) => t.category === 'basic').map(([key, tpl]) => (
                <option key={key} value={key}>{tpl.label}</option>
              ))}
            </optgroup>
            <optgroup label="Trend Following">
              {Object.entries(TEMPLATES).filter(([, t]) => t.category === 'trend').map(([key, tpl]) => (
                <option key={key} value={key}>{tpl.label}</option>
              ))}
            </optgroup>
            <optgroup label="Mean Reversion">
              {Object.entries(TEMPLATES).filter(([, t]) => t.category === 'mean-rev').map(([key, tpl]) => (
                <option key={key} value={key}>{tpl.label}</option>
              ))}
            </optgroup>
            <optgroup label="Solana Native">
              {Object.entries(TEMPLATES).filter(([, t]) => t.category === 'solana').map(([key, tpl]) => (
                <option key={key} value={key}>{tpl.label}</option>
              ))}
            </optgroup>
            <optgroup label="Advanced (v2 API)">
              {Object.entries(TEMPLATES).filter(([, t]) => t.category === 'advanced').map(([key, tpl]) => (
                <option key={key} value={key}>{tpl.label}</option>
              ))}
            </optgroup>
          </select>
        </div>
      </div>

      {/* ── run history panel ──────────────────────────── */}
      {showHistory && runHistory.length > 0 && (
        <div className="border border-border bg-surface/60 backdrop-blur" style={{ animation: 'fadeUp 0.3s ease' }}>
          <div className="px-3 py-2 border-b border-border flex items-center gap-2">
            <span className="w-2 h-2 bg-amber/60" />
            <span className="text-[10px] text-ghost tracking-[0.2em]">RUN.HISTORY</span>
            <span className="text-[10px] text-ghost/40 ml-auto">{runHistory.length} runs this session</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] font-mono">
              <thead>
                <tr className="text-ghost/60 text-[9px] tracking-wider">
                  <th className="text-left px-3 py-1.5">STRATEGY</th>
                  <th className="text-left px-3 py-1.5">MARKET</th>
                  <th className="text-left px-3 py-1.5">TF</th>
                  <th className="text-right px-3 py-1.5">PNL</th>
                  <th className="text-right px-3 py-1.5">SHARPE</th>
                  <th className="text-right px-3 py-1.5">TRADES</th>
                  <th className="text-right px-3 py-1.5">TIME</th>
                </tr>
              </thead>
              <tbody>
                {runHistory.map((r) => (
                  <tr key={r.id} className="border-t border-border/50 hover:bg-amber-glow transition-colors">
                    <td className="px-3 py-1.5 text-terminal">{r.strategy}</td>
                    <td className="px-3 py-1.5 text-ghost">{r.market}</td>
                    <td className="px-3 py-1.5 text-ghost">{r.resolution}</td>
                    <td className={`px-3 py-1.5 text-right ${r.pnl >= 0 ? 'text-gain' : 'text-loss'}`}>
                      {r.pnl >= 0 ? '+' : ''}{r.pnl.toFixed(2)}
                    </td>
                    <td className="px-3 py-1.5 text-right text-terminal">{r.sharpe.toFixed(2)}</td>
                    <td className="px-3 py-1.5 text-right text-ghost">{r.trades}</td>
                    <td className="px-3 py-1.5 text-right text-ghost/50">
                      {new Date(r.ts).toLocaleTimeString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── strategy tabs ──────────────────────────────── */}
      <div className="flex items-center gap-0.5 overflow-x-auto pb-1">
        <button
          onClick={() => {
            if (dirty && !window.confirm('You have unsaved changes. Discard?')) return
            setCurrentName(null)
            setCode(BLANK_TEMPLATE)
            setDirty(false)
            setActiveTemplateKey(null)
            setValidationError(null)
          }}
          className={`px-3 py-1.5 text-[10px] tracking-[0.12em] border transition-all whitespace-nowrap ${
            currentName === null && !activeTemplateKey
              ? 'border-amber/50 bg-amber-glow text-amber'
              : 'border-border text-ghost hover:text-terminal hover:border-border-bright'
          }`}
        >
          + NEW
        </button>
        {savedStrategies.map((s) => (
          <button
            key={s.name}
            onClick={() => handleLoadStrategy(s.name)}
            className={`group px-3 py-1.5 text-[10px] tracking-[0.12em] border transition-all whitespace-nowrap flex items-center gap-2 ${
              currentName === s.name
                ? 'border-amber/50 bg-amber-glow text-amber'
                : 'border-border text-ghost hover:text-terminal hover:border-border-bright'
            }`}
          >
            {s.name}
            {currentName === s.name && dirty && <span className="text-amber">*</span>}
            <span
              onClick={(e) => handleDeleteStrategy(s.name, e)}
              className="opacity-0 group-hover:opacity-60 hover:!opacity-100 text-loss cursor-pointer"
            >
              x
            </span>
          </button>
        ))}
      </div>

      {/* ── main split layout ──────────────────────────── */}
      <div className="flex gap-4" style={{ minHeight: 'calc(100vh - 180px)' }}>
        {/* LEFT — editor */}
        <div className="flex flex-col border border-border bg-surface/60 backdrop-blur" style={{ flex: '0 0 55%', minWidth: 0 }}>
          {/* editor toolbar */}
          <div className="px-3 py-2 border-b border-border flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 bg-amber/60" />
              <span className="text-[10px] text-ghost tracking-[0.2em]">EDITOR</span>
              <span className="text-[10px] text-ghost/40 ml-2">python</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleValidate}
                className="px-2.5 py-1 text-[10px] tracking-[0.1em] border border-border text-ghost hover:text-terminal hover:border-border-bright transition-all"
              >
                VALIDATE
              </button>
              <button
                onClick={handleSave}
                disabled={savingStrategy}
                className="px-2.5 py-1 text-[10px] tracking-[0.1em] border border-border text-ghost hover:text-amber hover:border-amber/30 transition-all disabled:opacity-40"
                title="Ctrl+S"
              >
                {savingStrategy ? 'SAVING...' : 'SAVE'}
              </button>
            </div>
          </div>

          {/* Monaco editor */}
          <div className="flex-1 min-h-0">
            <CodeEditor value={code} onChange={handleCodeChange} />
          </div>

          {/* validation errors */}
          {validationError && (
            <div className="px-3 py-2 border-t border-loss/30 bg-loss/5 text-loss text-[11px] font-mono">
              <span className="text-loss/60 mr-2">[ERR]</span>{validationError}
            </div>
          )}
        </div>

        {/* RIGHT — config + results */}
        <div className="flex-1 min-w-0 flex flex-col gap-4 overflow-y-auto" style={{ maxHeight: 'calc(100vh - 140px)' }}>
          {/* config panel */}
          <div className="border border-border bg-surface/60 backdrop-blur">
            <div className="px-3 py-2 border-b border-border flex items-center gap-2">
              <span className="w-2 h-2 bg-amber/60" />
              <span className="text-[10px] text-ghost tracking-[0.2em]">CONFIG.PARAMS</span>
            </div>
            <div className="p-3 grid grid-cols-2 gap-3">
              <div>
                <label className={labelClass}>MARKET</label>
                <select value={market} onChange={(e) => setMarket(e.target.value)} className={inputClass}>
                  <optgroup label="Perp Markets">
                    {(availableMarkets.length > 0
                      ? availableMarkets.filter(m => m.includes('-PERP'))
                      : PERP_MARKETS
                    ).map(m => <option key={m}>{m}</option>)}
                  </optgroup>
                  <optgroup label="Spot Markets">
                    {(availableMarkets.length > 0
                      ? availableMarkets.filter(m => !m.includes('-PERP'))
                      : SPOT_MARKETS
                    ).map(m => <option key={m}>{m}</option>)}
                  </optgroup>
                </select>
              </div>
              <div>
                <label className={labelClass}>TIMEFRAME</label>
                <select value={resolution} onChange={(e) => setResolution(e.target.value)} className={inputClass}>
                  {Object.keys(RESOLUTIONS).map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
              <div>
                <label className={labelClass}>CAPITAL</label>
                <input type="number" value={capital} onChange={(e) => setCapital(+e.target.value)} className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>FEE.MODEL</label>
                <select value={feePreset} onChange={(e) => setFeePreset(e.target.value)} className={inputClass}>
                  <optgroup label="Drift Protocol">
                    {Object.entries(FEE_PRESETS).filter(([,p]) => p.venue === 'drift').map(([key, p]) => (
                      <option key={key} value={key}>{p.label}</option>
                    ))}
                  </optgroup>
                  <optgroup label="Hyperliquid">
                    {Object.entries(FEE_PRESETS).filter(([,p]) => p.venue === 'hyperliquid').map(([key, p]) => (
                      <option key={key} value={key}>{p.label}</option>
                    ))}
                  </optgroup>
                  <optgroup label="Binance Futures">
                    {Object.entries(FEE_PRESETS).filter(([,p]) => p.venue === 'binance').map(([key, p]) => (
                      <option key={key} value={key}>{p.label}</option>
                    ))}
                  </optgroup>
                  <optgroup label="OKX">
                    {Object.entries(FEE_PRESETS).filter(([,p]) => p.venue === 'okx').map(([key, p]) => (
                      <option key={key} value={key}>{p.label}</option>
                    ))}
                  </optgroup>
                  <optgroup label="Bybit">
                    {Object.entries(FEE_PRESETS).filter(([,p]) => p.venue === 'bybit').map(([key, p]) => (
                      <option key={key} value={key}>{p.label}</option>
                    ))}
                  </optgroup>
                  <optgroup label="Generic">
                    {Object.entries(FEE_PRESETS).filter(([,p]) => p.venue === 'generic').map(([key, p]) => (
                      <option key={key} value={key}>{p.label}</option>
                    ))}
                  </optgroup>
                </select>
              </div>
              <div>
                <label className={labelClass}>START</label>
                <input
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  className={`${inputClass} ${configError && configError.includes('date') ? 'border-loss/50' : ''}`}
                />
              </div>
              <div>
                <label className={labelClass}>END</label>
                <input
                  type="date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  className={`${inputClass} ${configError && configError.includes('date') ? 'border-loss/50' : ''}`}
                />
              </div>
            </div>

            {/* ── status indicator: validation + data check + run readiness ── */}
            <div className="mx-3 mb-2 space-y-1.5">
              {/* config validation error */}
              {configError && (
                <div className="px-2.5 py-1.5 text-[10px] tracking-wider border border-loss/20 bg-loss/5 text-loss/80">
                  {configError}
                </div>
              )}

              {/* data check result */}
              {!configError && dataCheckLoading && (
                <div className="px-2.5 py-1.5 text-[10px] tracking-wider border border-border text-ghost/50">
                  Checking data availability...
                </div>
              )}
              {!configError && !dataCheckLoading && dataCheck && (
                <div className={`px-2.5 py-1.5 text-[10px] tracking-wider border ${
                  dataCheck.covers_range
                    ? 'border-gain/20 bg-gain/5 text-gain/80'
                    : 'border-amber/20 bg-amber/5 text-amber/80'
                }`}>
                  {dataCheck.covers_range ? (
                    <>{dataCheck.candle_count.toLocaleString()} candles ready — instant backtest</>
                  ) : dataCheck.has_data ? (
                    <>Partial data ({dataCheck.candle_count} candles) — will download rest from Drift S3</>
                  ) : (
                    <>Will download from Drift S3 (10-60s depending on range)</>
                  )}
                </div>
              )}

              {/* template hint */}
              {activeTemplateKey && TEMPLATES[activeTemplateKey]?.hint && (
                <div className="px-2.5 py-1.5 text-[10px] text-ghost/50 border border-border/30 tracking-wider">
                  {TEMPLATES[activeTemplateKey].hint}
                </div>
              )}
            </div>

            <div className="px-3 pb-3">
              {status === 'running' && progress ? (
                /* ── progress bar ─────────────────────── */
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-[10px] tracking-wider">
                    <span className={progress.phase === 's3' ? 'text-amber animate-pulse' : 'text-amber'}>
                      {progress.phase === 's3' ? 'DOWNLOADING' :
                       progress.phase === 'cached' ? 'CACHING' :
                       progress.phase === 'backtest' ? 'BACKTESTING' :
                       progress.phase === 'tearsheet' ? 'ANALYZING' :
                       progress.phase.toUpperCase()}
                    </span>
                    <span className="text-ghost/60">
                      {progress.elapsed_s.toFixed(1)}s
                      {progress.candles > 0 && ` · ${progress.candles.toLocaleString()} candles`}
                    </span>
                  </div>
                  <div className="w-full h-2.5 bg-border overflow-hidden">
                    <div
                      className={`h-full transition-all duration-300 ease-out ${
                        progress.phase === 's3' ? 'bg-amber/80' : 'bg-amber'
                      }`}
                      style={{ width: `${progress.pct}%` }}
                    />
                  </div>
                  <div className="text-[10px] text-ghost/60 text-center tracking-wider">
                    {progress.detail}
                  </div>
                  {progress.pct > 0 && progress.pct < 100 && (
                    <div className="text-[9px] text-ghost/30 text-center">
                      {progress.pct}%
                    </div>
                  )}
                </div>
              ) : optStatus === 'running' && optProgress ? (
                /* ── optimization progress ─────────────── */
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-[10px] tracking-wider">
                    <span className="text-amber animate-pulse">OPTIMIZING</span>
                    <span className="text-ghost/60">{optProgress.elapsed_s?.toFixed(1)}s</span>
                  </div>
                  <div className="w-full h-2.5 bg-border overflow-hidden">
                    <div className="h-full bg-amber transition-all duration-500" style={{ width: `${optProgress.pct}%` }} />
                  </div>
                  <div className="text-[10px] text-ghost/60 text-center">{optProgress.detail}</div>
                </div>
              ) : (
                /* ── run + optimize buttons ────────────── */
                <>
                  <div className="flex gap-2">
                    <button
                      onClick={handleRun}
                      disabled={!!configError}
                      className={`flex-1 px-4 py-3 text-sm font-semibold tracking-[0.15em] transition-all duration-200 ${
                        configError
                          ? 'bg-border text-ghost/40 cursor-not-allowed'
                          : 'bg-amber text-void hover:bg-amber-dim'
                      }`}
                      title={configError || 'Ctrl+Enter'}
                    >
                      {configError ? 'FIX CONFIG'
                        : dataCheck && dataCheck.will_download ? '> DOWNLOAD + RUN'
                        : '> RUN_BACKTEST'}
                    </button>
                    <button
                      onClick={handleOptimize}
                      disabled={!!configError}
                      className="px-4 py-3 text-[11px] font-semibold tracking-[0.15em] border border-amber/40 text-amber hover:bg-amber/10 disabled:border-border disabled:text-ghost/30 transition-all"
                      title="Optimize strategy parameters"
                    >
                      OPTIMIZE
                    </button>
                  </div>
                  <div className="flex items-center gap-2 mt-2">
                    <select value={optMetric} onChange={e => setOptMetric(e.target.value)}
                      className="bg-void border border-border text-ghost text-[10px] px-2 py-1 flex-1">
                      <option value="sharpe_ratio">Sharpe</option>
                      <option value="total_pnl">PnL</option>
                      <option value="calmar">Calmar</option>
                      <option value="win_rate">Win Rate</option>
                    </select>
                    <input type="number" value={optTrials} onChange={e => setOptTrials(+e.target.value)}
                      className="bg-void border border-border text-ghost text-[10px] px-2 py-1 w-16 text-center" min={5} max={500} />
                    <span className="text-[9px] text-ghost/40">trials</span>
                  </div>
                  <div className="text-[9px] text-ghost/40 mt-1.5 text-center tracking-wider">
                    Ctrl+Enter to run &middot; Ctrl+S to save
                  </div>
                </>
              )}
            </div>
          </div>

          {/* error display */}
          {error && (
            <div className="border border-loss/30 bg-loss/5 px-3 py-2.5 text-loss text-xs">
              <span className="text-loss/60 mr-2">[ERR]</span>{error}
            </div>
          )}

          {/* optimization results (in right panel for visibility) */}
          {optResults && (
            <div className="border border-amber/30 bg-surface/60 p-3" style={{ animation: 'fadeUp 0.3s ease' }}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] text-amber tracking-[0.2em]">OPTIMIZATION COMPLETE</span>
                <span className="text-[9px] text-ghost/40">{optResults.n_trials} trials</span>
              </div>
              <div className="mb-2">
                <span className="text-[9px] text-ghost/50">BEST {optResults.metric.toUpperCase()}</span>
                <span className="text-lg text-amber font-semibold ml-2">{optResults.best_value}</span>
              </div>
              <div className="grid grid-cols-2 gap-1.5 mb-2">
                {Object.entries(optResults.best_params).map(([k, v]) => (
                  <div key={k} className="bg-void/50 px-2 py-1 border border-border/30">
                    <div className="text-[8px] text-ghost/40">{k}</div>
                    <div className="text-[11px] text-amber font-mono">{String(v)}</div>
                  </div>
                ))}
              </div>
              {/* Top 3 trials */}
              {optResults.trials.slice(0, 3).map((t, i) => (
                <div key={i} className="flex items-center justify-between text-[10px] py-0.5 border-t border-border/20">
                  <span className="text-ghost/50">#{i+1}</span>
                  <span className="text-terminal">{t.metric_value.toFixed(3)}</span>
                  <span className={t.total_pnl >= 0 ? 'text-gain' : 'text-loss'}>${t.total_pnl >= 0 ? '+' : ''}{t.total_pnl.toFixed(0)}</span>
                  <span className="text-ghost/40">{t.total_trades}t</span>
                </div>
              ))}
            </div>
          )}

          {/* optimization error */}
          {optError && (
            <div className="border border-loss/30 bg-loss/5 px-3 py-2 text-loss text-[10px]">
              <span className="text-loss/60 mr-1">[OPT]</span>{optError}
            </div>
          )}

          {/* compact metrics summary (stays in right panel) */}
          {results && (
            <div className="border border-border bg-surface/60 backdrop-blur p-3" style={{ animation: 'fadeUp 0.3s ease' }}>
              <div className="flex items-baseline gap-3 mb-2">
                <span className="font-[var(--font-display)] text-base text-white/90 italic">Results</span>
                <span className="text-[10px] text-amber tracking-wider">{results.strategy_name}</span>
                <button
                  onClick={handleExport}
                  className="ml-auto px-2 py-0.5 text-[9px] tracking-[0.1em] border border-border text-ghost hover:text-amber hover:border-amber/30 transition-all"
                >
                  EXPORT
                </button>
              </div>

              {/* key metrics in compact grid */}
              <div className="grid grid-cols-3 gap-2 text-[11px] font-mono">
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">PNL</div>
                  <div className={results.metrics?.total_pnl >= 0 ? 'text-gain' : 'text-loss'}>
                    ${results.metrics?.total_pnl >= 0 ? '+' : ''}{results.metrics?.total_pnl?.toFixed(2)}
                  </div>
                </div>
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">SHARPE</div>
                  <div className="text-terminal">{results.metrics?.sharpe_ratio?.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">TRADES</div>
                  <div className="text-terminal">{results.trades?.length || 0}</div>
                </div>
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">WIN.RATE</div>
                  <div className="text-terminal">{(results.metrics?.win_rate * 100)?.toFixed(1)}%</div>
                </div>
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">MAX.DD</div>
                  <div className="text-loss">{(results.metrics?.max_drawdown * 100)?.toFixed(1)}%</div>
                </div>
                <div>
                  <div className="text-[8px] text-ghost/50 tracking-wider">SORTINO</div>
                  <div className="text-terminal">{results.metrics?.sortino_ratio?.toFixed(2)}</div>
                </div>
              </div>

              {progress && progress.phase === 'done' && (
                <div className="mt-2 text-[9px] text-ghost/40 tracking-wider">
                  {progress.elapsed_s.toFixed(1)}s &middot; {progress.candles?.toLocaleString()} candles &middot; {results.market} &middot; {resolution}
                </div>
              )}

              {results.trades?.length === 0 && (
                <div className="mt-2 text-[10px] text-amber/70">
                  No trades — extend date range or adjust parameters
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── FULL-WIDTH RESULTS BELOW ───────────────────── */}
      {results && results.trades?.length > 0 && (
        <div className="space-y-4 mt-4" style={{ animation: 'fadeUp 0.5s ease' }}>

          {/* Equity + Drawdown side by side */}
          <div className="grid grid-cols-2 gap-4">
            <div className="border border-border bg-surface/60 backdrop-blur p-3">
              <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">EQUITY.CURVE</div>
              <EquityCurve equity={results.equity_curve} buyHold={results.buy_hold_equity} height={220} />
            </div>
            <div className="border border-border bg-surface/60 backdrop-blur p-3">
              <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">DRAWDOWN</div>
              <DrawdownChart drawdown={results.drawdown_curve} height={220} />
            </div>
          </div>

          {/* Price chart with trade markers */}
          {results.equity_curve?.length > 0 && (
            <div className="border border-border bg-surface/60 backdrop-blur p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] text-ghost tracking-[0.2em]">PRICE.ACTION + TRADES</span>
                <span className="text-[9px] text-ghost/40">
                  <span className="inline-block w-2 h-2 bg-gain rounded-full mr-1" />entry
                  <span className="inline-block w-2 h-2 bg-loss ml-2 mr-1" />exit (loss)
                </span>
              </div>
              <PriceChart candles={results.buy_hold_equity || results.equity_curve} trades={results.trades} height={260} />
            </div>
          )}

          {/* Metrics + Split side by side */}
          <div className="grid grid-cols-2 gap-4">
            <div className="border border-border bg-surface/60 backdrop-blur p-3">
              <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">ALL.METRICS</div>
              <MetricsCard metrics={results.metrics} />
            </div>
            <div className="border border-border bg-surface/60 backdrop-blur p-3">
              <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">LONG.SHORT.BREAKDOWN</div>
              <SplitMetrics trades={results.trades} />
            </div>
          </div>

          {/* PnL distribution + Exposure side by side */}
          {results.trades?.length > 1 && (
            <div className="grid grid-cols-2 gap-4">
              <div className="border border-border bg-surface/60 backdrop-blur p-3">
                <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">PNL.DISTRIBUTION</div>
                <PnlHistogram trades={results.trades} height={160} />
              </div>
              <div className="border border-border bg-surface/60 backdrop-blur p-3">
                <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">EXPOSURE.TIMELINE</div>
                <ExposureTimeline
                  trades={results.trades}
                  startTs={results.period_start || results.equity_curve?.[0]?.[0] || 0}
                  endTs={results.period_end || results.equity_curve?.[results.equity_curve.length - 1]?.[0] || 0}
                  height={160}
                />
              </div>
            </div>
          )}

          {/* Monthly returns */}
          {results.monthly_returns && Object.keys(results.monthly_returns).length > 0 && (
            <div className="border border-border bg-surface/60 backdrop-blur p-3">
              <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">MONTHLY.RETURNS</div>
              <div className="grid grid-cols-13 gap-px text-[9px] text-center">
                <div className="text-ghost py-1">YR</div>
                {['J','F','M','A','M','J','J','A','S','O','N','D'].map(m =>
                  <div key={m} className="text-ghost/50 py-1">{m}</div>
                )}
                {Object.entries(results.monthly_returns as Record<string, Record<number, number>>).map(([year, months]) => (
                  <div key={year} className="contents">
                    <div className="text-ghost py-1">{year.slice(2)}</div>
                    {Array.from({ length: 12 }, (_, i) => {
                      const ret = months[i + 1]
                      return (
                        <div key={i} className={`py-1 ${
                          ret === undefined ? 'bg-border/30' :
                          ret >= 0 ? 'bg-gain/10 text-gain' : 'bg-loss/10 text-loss'
                        }`}>
                          {ret !== undefined ? `${ret > 0 ? '+' : ''}${ret}` : ''}
                        </div>
                      )
                    })}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Trade log */}
          <div className="border border-border bg-surface/60 backdrop-blur">
            <div className="px-3 py-2 border-b border-border flex items-center justify-between">
              <span className="text-[10px] text-ghost tracking-[0.2em]">TRADE.LOG</span>
              <span className="text-[10px] text-amber/60">{results.trades.length} executions</span>
            </div>
            <div className="p-3">
              <TradeTable trades={results.trades} />
            </div>
          </div>

          <div className="text-[10px] text-ghost/40 text-center tracking-wider pb-4">
            BUY_HOLD: {results.buy_hold_return_pct?.toFixed(2)}% &middot; FEE: {FEE_PRESETS[feePreset]?.label || 'custom'}
          </div>
        </div>
      )}

      {/* ── OPTIMIZATION RESULTS ───────────────────── */}
      {optStatus === 'running' && optProgress && (
        <div className="mt-4 border border-amber/30 bg-amber/5 p-4">
          <div className="flex items-center justify-between text-[10px] tracking-wider mb-2">
            <span className="text-amber">OPTIMIZING — {optProgress.detail}</span>
            <span className="text-ghost/50">{optProgress.elapsed_s?.toFixed(1)}s</span>
          </div>
          <div className="w-full h-2 bg-border overflow-hidden">
            <div className="h-full bg-amber transition-all duration-500" style={{ width: `${optProgress.pct}%` }} />
          </div>
        </div>
      )}
      {optError && (
        <div className="mt-4 border border-loss/30 bg-loss/5 px-3 py-2.5 text-loss text-xs">
          <span className="text-loss/60 mr-2">[OPT ERR]</span>{optError}
        </div>
      )}
      {optResults && (
        <div className="mt-4 space-y-4" style={{ animation: 'fadeUp 0.3s ease' }}>
          <div className="border border-amber/30 bg-surface/60 p-4">
            <div className="flex items-baseline gap-3 mb-3">
              <span className="font-[var(--font-display)] text-base text-white/90 italic">Optimization Results</span>
              <span className="text-[10px] text-amber">{optResults.strategy_name}</span>
              <span className="text-[10px] text-ghost">{optResults.n_trials} trials &middot; {optResults.metric}</span>
            </div>
            <div className="mb-3">
              <span className="text-[10px] text-ghost/50 tracking-wider">BEST {optResults.metric.toUpperCase()}: </span>
              <span className="text-lg text-amber font-semibold">{optResults.best_value}</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-3">
              {Object.entries(optResults.best_params).map(([k, v]) => (
                <div key={k} className="bg-void/50 px-2 py-1.5 border border-border/50">
                  <div className="text-[8px] text-ghost/50 tracking-wider">{k}</div>
                  <div className="text-[12px] text-amber font-mono">{String(v)}</div>
                </div>
              ))}
            </div>
            {optResults.trials.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-[11px] font-mono">
                  <thead>
                    <tr className="text-[9px] text-ghost/50 tracking-wider">
                      <th className="text-left px-2 py-1">#</th>
                      <th className="text-right px-2 py-1">{optResults.metric.toUpperCase()}</th>
                      <th className="text-right px-2 py-1">PNL</th>
                      <th className="text-right px-2 py-1">TRADES</th>
                      <th className="text-left px-2 py-1">PARAMS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {optResults.trials.slice(0, 10).map((t, i) => (
                      <tr key={i} className="border-t border-border/20 hover:bg-amber-glow/30">
                        <td className="px-2 py-1 text-ghost/50">{i + 1}</td>
                        <td className="px-2 py-1 text-right text-terminal">{t.metric_value.toFixed(4)}</td>
                        <td className={`px-2 py-1 text-right ${t.total_pnl >= 0 ? 'text-gain' : 'text-loss'}`}>
                          ${t.total_pnl >= 0 ? '+' : ''}{t.total_pnl.toFixed(0)}
                        </td>
                        <td className="px-2 py-1 text-right text-ghost">{t.total_trades}</td>
                        <td className="px-2 py-1 text-ghost/60 text-[10px]">
                          {Object.entries(t.params).map(([k, v]) => `${k}=${v}`).join(', ')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── JOURNAL — past backtest runs ────────────── */}
      <div className="mt-6 mb-4">
        <button
          onClick={() => { setShowJournal(!showJournal); if (!showJournal) refreshJournal() }}
          className={`text-[10px] tracking-[0.15em] px-3 py-1.5 border transition-all ${
            showJournal ? 'border-amber/40 text-amber bg-amber-glow' : 'border-border text-ghost hover:text-terminal'
          }`}
        >
          JOURNAL ({journalRuns.length} runs)
        </button>
      </div>
      {showJournal && journalRuns.length > 0 && (
        <div className="border border-border bg-surface/60 mb-8" style={{ animation: 'fadeUp 0.3s ease' }}>
          <div className="px-4 py-2 border-b border-border flex items-center justify-between">
            <span className="text-[10px] text-ghost tracking-[0.2em]">BACKTEST.JOURNAL</span>
            <span className="text-[10px] text-ghost/40">{journalRuns.length} saved runs</span>
          </div>
          <div className="overflow-x-auto max-h-[300px] overflow-y-auto">
            <table className="w-full text-[11px] font-mono">
              <thead className="sticky top-0 bg-surface">
                <tr className="text-[9px] text-ghost/50 tracking-wider border-b border-border">
                  <th className="text-left px-3 py-1.5">STRATEGY</th>
                  <th className="text-left px-3 py-1.5">MARKET</th>
                  <th className="text-right px-3 py-1.5">PNL</th>
                  <th className="text-right px-3 py-1.5">SHARPE</th>
                  <th className="text-right px-3 py-1.5">TRADES</th>
                  <th className="text-right px-3 py-1.5">MAX DD</th>
                  <th className="text-left px-3 py-1.5">DATE</th>
                  <th className="px-3 py-1.5"></th>
                </tr>
              </thead>
              <tbody>
                {journalRuns.map((r) => (
                  <tr key={r.run_id} className="border-t border-border/20 hover:bg-amber-glow/30">
                    <td className="px-3 py-1.5 text-terminal">{r.strategy_name}</td>
                    <td className="px-3 py-1.5 text-ghost/60">{r.market}</td>
                    <td className={`px-3 py-1.5 text-right ${r.total_pnl >= 0 ? 'text-gain' : 'text-loss'}`}>
                      ${r.total_pnl >= 0 ? '+' : ''}{r.total_pnl?.toFixed(0)}
                    </td>
                    <td className="px-3 py-1.5 text-right text-terminal">{r.sharpe_ratio?.toFixed(2)}</td>
                    <td className="px-3 py-1.5 text-right text-ghost">{r.total_trades}</td>
                    <td className="px-3 py-1.5 text-right text-loss">{(r.max_drawdown * 100)?.toFixed(1)}%</td>
                    <td className="px-3 py-1.5 text-ghost/40 text-[10px]">
                      {new Date(r.created_at * 1000).toLocaleDateString()}
                    </td>
                    <td className="px-3 py-1.5">
                      <button onClick={() => deleteJournalRun(r.run_id)} className="text-ghost/30 hover:text-loss text-[10px]">x</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* results with 0 trades — show equity only, full width */}
      {results && (!results.trades || results.trades.length === 0) && results.equity_curve?.length > 0 && (
        <div className="mt-4 border border-border bg-surface/60 backdrop-blur p-3" style={{ animation: 'fadeUp 0.3s ease' }}>
          <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">EQUITY.CURVE (no trades)</div>
          <EquityCurve equity={results.equity_curve} buyHold={results.buy_hold_equity} height={200} />
        </div>
      )}
    </div>
  )
}
