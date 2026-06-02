# Tutorial: Build Your First Profitable Strategy

This tutorial walks through building a custom RSI-MACD confluence strategy from scratch, optimizing it with Optuna, validating it with walk-forward analysis, and deploying it to paper trading. The entire process uses SOL-PERP as the example market.

## Prerequisites

- Flint installed and running (`flint serve` at [localhost:8000](http://localhost:8000))
- At least 90 days of SOL-PERP data downloaded (see the [Quickstart Guide](../guides/quickstart.md))

---

## Step 1: Start with the Template

Every Flint strategy inherits from the `Strategy` base class and implements three things: a `name` property, an `on_candle` method, and a `reset` method. Here is the minimal skeleton:

```python
from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side


class MyStrategy(Strategy):
    @property
    def name(self) -> str:
        return "my_strategy"

    def on_candle(self, candle, history, ctx=None):
        return Signal.HOLD

    def reset(self):
        pass
```

The `on_candle` method is called once for every candle bar during a backtest. It receives the current candle, the full history of previous candles, and an optional execution context (`ctx`). We will use the v2 context-based API, which gives full control over order placement.

---

## Step 2: Understand the Indicators

Our strategy combines two indicators: RSI and MACD. Each measures something different, and the combination filters out false signals that either indicator alone would produce.

### RSI (Relative Strength Index)

RSI measures the speed and magnitude of recent price changes on a scale from 0 to 100.

- **RSI below 30**: The asset is "oversold" -- it has dropped quickly and may be due for a bounce.
- **RSI above 70**: The asset is "overbought" -- it has risen quickly and may be due for a pullback.
- **RSI between 30 and 70**: No strong signal either way.

RSI is a mean-reversion indicator. It works well in ranging markets but generates false signals during strong trends.

### MACD (Moving Average Convergence Divergence)

MACD tracks the relationship between two exponential moving averages (a fast EMA and a slow EMA). It produces three values:

- **MACD line**: Fast EMA minus slow EMA.
- **Signal line**: A smoothed average of the MACD line.
- **Histogram**: MACD line minus signal line. When the histogram crosses from negative to positive, it signals bullish momentum. The reverse signals bearish momentum.

MACD is a trend-following indicator. It catches sustained moves but lags behind sharp reversals.

### Why Combine Them

RSI catches oversold/overbought extremes. MACD confirms the momentum direction. By requiring both indicators to agree before entering a trade, we filter out most of the false signals from either indicator alone. The tradeoff is fewer trades, but higher conviction on each one.

---

## Step 3: Define Entry and Exit Logic

The logic:

1. **Buy** when RSI is oversold (below 30) AND the MACD histogram crosses from negative to positive.
2. **Sell** when RSI is overbought (above 70) AND the MACD histogram crosses from positive to negative.
3. **Exit** any open position when the opposite confluence signal fires.

Here is the full strategy:

```python
from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side

import numpy as np


class RSIMACDStrategy(Strategy):
    def __init__(
        self,
        rsi_period=14,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        rsi_oversold=30.0,
        rsi_overbought=70.0,
    ):
        if macd_fast >= macd_slow:
            raise ValueError("macd_fast must be less than macd_slow")
        self.rsi_period = rsi_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

        # Internal state for MACD
        self._fast_ema = 0.0
        self._slow_ema = 0.0
        self._signal_ema = 0.0
        self._prev_histogram = 0.0
        self._macd_initialized = False
        self._macd_history = []

    @property
    def name(self):
        return f"RSI-MACD({self.rsi_period}, {self.macd_fast}/{self.macd_slow})"

    def reset(self):
        self._fast_ema = 0.0
        self._slow_ema = 0.0
        self._signal_ema = 0.0
        self._prev_histogram = 0.0
        self._macd_initialized = False
        self._macd_history = []

    def _compute_rsi(self, history):
        """Compute RSI from recent close prices."""
        closes = np.array([c.close for c in history[-(self.rsi_period + 1):]])
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = float(np.mean(gains))
        avg_loss = float(np.mean(losses))
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)

    def _update_macd(self, price, history):
        """Update MACD state and return the histogram, or None if not ready."""
        if not self._macd_initialized:
            if len(history) < self.macd_slow:
                return None
            closes = [c.close for c in history[-self.macd_slow:]]
            self._fast_ema = sum(closes[-self.macd_fast:]) / self.macd_fast
            self._slow_ema = sum(closes) / self.macd_slow
            self._macd_initialized = True
            macd_val = self._fast_ema - self._slow_ema
            self._macd_history.append(macd_val)
            self._signal_ema = macd_val
            self._prev_histogram = 0.0
            return None

        fast_mult = 2 / (self.macd_fast + 1)
        slow_mult = 2 / (self.macd_slow + 1)
        self._fast_ema = price * fast_mult + self._fast_ema * (1 - fast_mult)
        self._slow_ema = price * slow_mult + self._slow_ema * (1 - slow_mult)

        macd_val = self._fast_ema - self._slow_ema
        self._macd_history.append(macd_val)
        if len(self._macd_history) > self.macd_signal * 2:
            self._macd_history = self._macd_history[-self.macd_signal:]

        if len(self._macd_history) < self.macd_signal:
            return None

        if len(self._macd_history) == self.macd_signal:
            self._signal_ema = sum(self._macd_history) / self.macd_signal
            self._prev_histogram = macd_val - self._signal_ema
            return None

        signal_mult = 2 / (self.macd_signal + 1)
        self._signal_ema = (
            macd_val * signal_mult + self._signal_ema * (1 - signal_mult)
        )

        histogram = macd_val - self._signal_ema
        prev = self._prev_histogram
        self._prev_histogram = histogram
        return histogram if prev != 0 else None

    def on_candle(self, candle, history, ctx=None):
        min_needed = max(self.rsi_period + 1, self.macd_slow)
        if len(history) < min_needed:
            return Signal.HOLD

        # Compute indicators
        rsi = self._compute_rsi(history)
        prev_hist = self._prev_histogram
        histogram = self._update_macd(candle.close, history)

        if histogram is None:
            return Signal.HOLD

        # Confluence signals
        rsi_bullish = rsi < self.rsi_oversold
        rsi_bearish = rsi > self.rsi_overbought
        macd_bullish = prev_hist <= 0 and histogram > 0
        macd_bearish = prev_hist >= 0 and histogram < 0

        buy_signal = rsi_bullish and macd_bullish
        sell_signal = rsi_bearish and macd_bearish

        # v2: use execution context for order placement
        if ctx is not None:
            pos = ctx.position(candle.market)
            if pos is None:
                if buy_signal:
                    size = (ctx.account.cash * 0.9) / candle.close
                    if size > 0:
                        ctx.market_order(candle.market, Side.LONG, size)
                elif sell_signal:
                    size = (ctx.account.cash * 0.9) / candle.close
                    if size > 0:
                        ctx.market_order(candle.market, Side.SHORT, size)
            else:
                if buy_signal or sell_signal:
                    ctx.close_position(candle.market)
            return Signal.HOLD

        # v1 fallback for signal-based mode
        if buy_signal:
            return Signal.BUY
        elif sell_signal:
            return Signal.SELL
        return Signal.HOLD
```

Key decisions in this code:

- **Position sizing**: We use 90% of available cash on each entry, leaving a 10% buffer for fees and slippage.
- **Exit logic**: We close on any opposite confluence signal. This is simple but effective -- the strategy does not try to hold through adverse signals.
- **v1/v2 dual support**: When `ctx` is provided (backtest or paper trading), the strategy uses direct order placement. When `ctx` is None (rare, signal mode only), it falls back to returning Signal.BUY/SELL.

---

## Step 4: Add Optimization Parameters

To enable Optuna optimization, add a `parameters()` classmethod that defines the search space for each tunable parameter. Each parameter specifies its type (`int` or `float`) and the range to search.

Add this method to the strategy class:

```python
    @classmethod
    def parameters(cls):
        return {
            "rsi_period": {"type": "int", "low": 5, "high": 30, "default": 14},
            "macd_fast": {"type": "int", "low": 5, "high": 20, "default": 12},
            "macd_slow": {"type": "int", "low": 15, "high": 50, "default": 26},
            "macd_signal": {"type": "int", "low": 5, "high": 15, "default": 9},
            "rsi_oversold": {"type": "float", "low": 15, "high": 40, "default": 30},
            "rsi_overbought": {"type": "float", "low": 60, "high": 85, "default": 70},
        }
```

Each key in the returned dictionary must match a constructor parameter name exactly. Optuna will sample values within the `low`/`high` range, construct the strategy with those values, run a full backtest, and record the result.

The `default` field is optional but recommended. It tells the optimizer what value to use as a starting point and documents the "no optimization" baseline.

---

## Step 5: Backtest It

Paste the full strategy code (including the `parameters()` method) into BacktestLab's code editor, or save it as a file and submit via the API.

### Via the UI

1. Go to **BacktestLab**.
2. Switch to the **Custom Code** tab.
3. Paste the strategy code.
4. Set market to **SOL-PERP**, date range to the last 90 days, capital to **10000**.
5. Click **Run Backtest**.

### Via the API

```bash
curl -s -X POST http://localhost:8000/api/v1/backtest/run \
  -H "Content-Type: application/json" \
  -d '{
    "code": "<paste your strategy code here>",
    "market": "SOL-PERP",
    "start_ts": 1735689600,
    "end_ts": 1743465600,
    "initial_capital": 10000,
    "fee_rate": 0.0006
  }' | python3 -m json.tool
```

Check the results. With default parameters on SOL-PERP, expect a modest result -- the defaults are not tuned for any specific market regime. A Sharpe between 0.3 and 1.0 with the default parameters is normal and indicates the strategy has something to work with. If the Sharpe is negative with defaults, the confluence logic may not suit the current SOL-PERP regime.

---

## Step 6: Optimize with Optuna

Now let Optuna find better parameters.

### Via the UI

1. After the backtest finishes, click **OPTIMIZE**.
2. Set trials to **100** (more trials means a more thorough search, but takes longer).
3. Keep the metric as **sharpe_ratio**.
4. Click **Run**.

### Via the API

```bash
curl -s -X POST http://localhost:8000/api/v1/optimize/run \
  -H "Content-Type: application/json" \
  -d '{
    "code": "<paste your strategy code here>",
    "market": "SOL-PERP",
    "start_ts": 1735689600,
    "end_ts": 1743465600,
    "initial_capital": 10000,
    "metric": "sharpe_ratio",
    "trials": 100
  }' | python3 -m json.tool
```

Poll for results:

```bash
curl -s http://localhost:8000/api/v1/optimize/<run_id>/results | python3 -m json.tool
```

### Reading Optimization Output

The results include:

- **best_params** -- The parameter values that produced the highest Sharpe ratio.
- **best_value** -- The Sharpe ratio achieved with those parameters.
- **convergence** -- An array of `[trial_number, best_value_so_far]` pairs. If the curve flattens early, the optimizer has converged.
- **param_importance** -- Ranking of which parameters mattered most. If `rsi_period` has importance 0.45 and `macd_signal` has 0.02, the RSI period is far more influential.
- **top_trials** -- The 20 best trials with full metrics (Sharpe, PnL, drawdown, win rate, trade count).

A realistic optimized result on SOL-PERP might look like: Sharpe of 1.2-1.8, max drawdown of 12-18%, win rate of 45-55%, and 30-80 trades over 90 days. These numbers vary significantly depending on the market regime during the test period.

> **Warning**: Do not trust optimized results at face value. A Sharpe of 3.0+ from optimization almost always indicates overfitting. The next step is critical.

---

## Step 7: Walk-Forward Validation

Walk-forward analysis tests whether the optimized parameters generalize to unseen data.

### Via the UI

1. After optimization completes, click **WALK-FORWARD**.
2. Set windows to **5**, train/test split to **70/30**.
3. Click **Run**.

### Via the API

```bash
curl -s -X POST http://localhost:8000/api/v1/optimize/walk-forward \
  -H "Content-Type: application/json" \
  -d '{
    "code": "<paste your strategy code here>",
    "market": "SOL-PERP",
    "start_ts": 1735689600,
    "end_ts": 1743465600,
    "initial_capital": 10000,
    "metric": "sharpe_ratio",
    "n_windows": 5,
    "train_pct": 0.7,
    "trials_per_window": 30
  }' | python3 -m json.tool
```

### Interpreting the Results

Each window shows an in-sample Sharpe (from the training period) and an out-of-sample Sharpe (from the test period). The key numbers:

- **avg_oos_sharpe** -- Average out-of-sample Sharpe across all windows. This is the most honest estimate of future performance. If it is positive, the strategy has predictive value.
- **overfitting_ratio** -- Out-of-sample performance divided by in-sample performance. A value near 1.0 means minimal overfitting. Below 0.3 means the in-sample results do not generalize.
- **parameter_stability** -- How much the optimal parameters vary across windows. High stability means the strategy is robust; high variance means it is fragile.

### Decision Framework

| avg_oos_sharpe | overfitting_ratio | Decision |
|---|---|---|
| Above 0.5 | Above 0.5 | Deploy to paper trading |
| Above 0.5 | Below 0.5 | Simplify the strategy (fewer parameters) and re-validate |
| Below 0.5 | Any | Rethink the strategy logic or try a different market |
| Negative | Any | Do not deploy. The strategy does not work out of sample |

In a bear market (2025-2026), even a good strategy may have a modest positive or slightly negative total return. The goal is risk-adjusted performance, not absolute returns. A strategy with a 0.7 Sharpe that limits drawdowns to 15% is more valuable than one that returned 50% but had a 40% drawdown along the way.

---

## Step 8: Deploy to Paper Trading

If walk-forward validation passes, deploy the strategy to paper trading.

### Via the UI

Click **Deploy to Paper** on the backtest result page. The strategy starts running against live Hyperliquid prices immediately.

### Via the API

```bash
curl -s -X POST http://localhost:8000/api/v1/paper/start \
  -H "Content-Type: application/json" \
  -d '{
    "code": "<paste your strategy code here>",
    "market": "SOL-PERP",
    "initial_capital": 10000,
    "params": {
      "rsi_period": 10,
      "macd_fast": 8,
      "macd_slow": 21,
      "macd_signal": 7,
      "rsi_oversold": 25,
      "rsi_overbought": 72
    },
    "venue": "hyperliquid"
  }' | python3 -m json.tool
```

Replace the params with the best values from your optimization.

### Monitoring

Go to the **Paper Trading** page in the UI. Watch for:

- **Equity curve** -- Is it trending in the expected direction?
- **Trade frequency** -- Does it match the backtest? If the backtest made 50 trades in 90 days, paper trading should average roughly 1 trade every 2 days.
- **Drawdown** -- If it exceeds the max drawdown from walk-forward testing, stop the session and investigate.

Run paper trading for at least 2 weeks before drawing conclusions. Market regimes shift, and a few days of data is not enough to validate a strategy.

---

## Summary

The full workflow:

1. Write the strategy with clear entry/exit logic.
2. Add `parameters()` for optimization.
3. Backtest with defaults to confirm the logic works.
4. Optimize with Optuna (50-100 trials).
5. Validate with walk-forward (5 windows, 70/30 split).
6. If avg_oos_sharpe > 0.5 and overfitting_ratio > 0.5, deploy to paper.
7. Monitor paper trading for 2-4 weeks.

The most common mistake is skipping step 5. Optimization always produces impressive-looking numbers. Walk-forward is what separates strategies that work from strategies that were overfit to historical noise.
