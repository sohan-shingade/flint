# Strategy Authoring Guide

This guide covers writing, testing, and optimizing trading strategies in Flint, from simple signal-based strategies to multi-market cross-venue arbitrage.

---

## Overview

All strategies inherit from the `Strategy` abstract base class in `flint/strategy/base.py`. Flint ships with 20 built-in strategy templates covering momentum, mean reversion, funding rate harvesting, cross-venue arbitrage, basis trading, and MEV monitoring. User strategies live in `strategies/user/` (gitignored) and are loaded via the API or the BacktestLab code editor.

There are two strategy styles:

- **v1 (Signal-based)**: `on_candle()` returns `Signal.BUY`, `Signal.SELL`, or `Signal.HOLD`. The engine handles position sizing and order placement.
- **v2 (Context-based)**: `on_candle()` calls `ctx.market_order()`, `ctx.stop_order()`, etc. directly and returns `Signal.HOLD`. Full control over order logic.

---

## Strategy ABC

Every strategy must implement three members defined in `flint/strategy/base.py`:

```python
from flint.strategy.base import Strategy
from flint.models import Candle, Signal

class MyStrategy(Strategy):
    @property
    def name(self) -> str:
        return "my_strategy"

    def on_candle(self, candle: Candle, history: list[Candle], ctx=None) -> Signal:
        # Your logic here
        return Signal.HOLD

    def reset(self) -> None:
        # Clear any internal state (called before each backtest run)
        pass
```

- `name` -- unique identifier shown in the UI and results.
- `on_candle(candle, history, ctx)` -- called on every bar. `candle` is the current bar; `history` is all bars up to (but not including) the current one. `ctx` is the `ExecutionContext` (None in v1-only mode).
- `reset()` -- called before every backtest run to clear accumulated state (counters, EMAs, etc.).
- `parameters()` -- optional classmethod for Optuna optimization (see the Optimization section below).

---

## v1: Signal-Based Strategy

Return `Signal.BUY`, `Signal.SELL`, or `Signal.HOLD`. The engine opens and closes positions automatically using the configured fee rate and position sizing.

```python
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from flint import indicators

class RSIStrategy(Strategy):
    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    @property
    def name(self) -> str:
        return f"RSI({self.period})"

    def reset(self) -> None:
        pass

    def on_candle(self, candle: Candle, history: list[Candle], ctx=None) -> Signal:
        if len(history) < self.period:
            return Signal.HOLD

        closes = [c.close for c in history] + [candle.close]
        rsi_val = indicators.rsi(closes, self.period)

        if rsi_val < self.oversold:
            return Signal.BUY
        elif rsi_val > self.overbought:
            return Signal.SELL
        return Signal.HOLD

    @classmethod
    def parameters(cls) -> dict:
        return {
            "period":     {"type": "int",   "low": 5,   "high": 100, "default": 14},
            "oversold":   {"type": "float", "low": 10.0, "high": 40.0, "default": 30.0},
            "overbought": {"type": "float", "low": 60.0, "high": 90.0, "default": 70.0},
        }
```

Available indicators in `flint.indicators`: `sma`, `ema`, `wma`, `rsi`, `stochastic`, `macd`, `bollinger`, `bollinger_width`, `atr`, `volatility`, `vwap`, `volume_ratio`, `roc`, `adx`, `z_score`, `highest_high`, `lowest_low`. All take `(history, period)` and return floats.

---

## v2: Context-Based Strategy

Use the `ExecutionContext` (`ctx`) to place orders directly. Always return `Signal.HOLD` since the engine ignores signal values when you manage orders manually.

```python
from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side
from flint import indicators

class MultiMarketFundingArb(Strategy):
    """Cross-venue funding rate arbitrage.

    Compares funding between Hyperliquid (DEX perp) and OKX (CEX perp).
    When the spread is wide enough, goes long on the venue paying less
    funding and short on the venue paying more. Backtest/research example —
    live CEX execution is out of scope; live legs run on Hyperliquid.
    """

    def __init__(self, entry_threshold: float = 0.0005, size: float = 0.1):
        self.entry_threshold = entry_threshold
        self.size = size

    @property
    def name(self) -> str:
        return "funding_arb"

    def reset(self) -> None:
        pass

    def on_candle(self, candle: Candle, history: list[Candle], ctx=None) -> Signal:
        if ctx is None:
            return Signal.HOLD

        # Compare funding rates across venues
        funding = ctx.get_funding_by_venue("SOL-PERP", lookback=1)
        okx_rates = funding.get("okx", [])
        hyper_rates = funding.get("hyperliquid", [])

        if not okx_rates or not hyper_rates:
            return Signal.HOLD

        okx_rate = okx_rates[-1][1]
        hyper_rate = hyper_rates[-1][1]
        spread = hyper_rate - okx_rate

        if spread > self.entry_threshold:
            # Hyperliquid paying more: long OKX, short Hyperliquid
            ctx.market_order("SOL-PERP", Side.LONG, self.size, venue="okx")
            ctx.market_order("SOL-PERP", Side.SHORT, self.size, venue="hyperliquid")
        elif spread < -self.entry_threshold:
            # OKX paying more: long Hyperliquid, short OKX
            ctx.market_order("SOL-PERP", Side.LONG, self.size, venue="hyperliquid")
            ctx.market_order("SOL-PERP", Side.SHORT, self.size, venue="okx")

        return Signal.HOLD

    @classmethod
    def parameters(cls) -> dict:
        return {
            "entry_threshold": {"type": "float", "low": 0.0001, "high": 0.005, "default": 0.0005},
            "size":            {"type": "float", "low": 0.01, "high": 1.0, "default": 0.1},
        }
```

Multi-venue backtests require `capital_allocation` in the request:

```json
{
  "strategy": "funding_arb",
  "market": "SOL-PERP",
  "capital_allocation": {"hyperliquid": 5000, "okx": 5000},
  "margin_tracking": true
}
```

---

## ExecutionContext API

The `ctx` parameter is an instance of `ExecutionContext` (defined in `flint/execution/context.py`). All implementations -- `BacktestContext`, `PaperContext`, `LiveHyperliquidContext`, `MultiVenueLiveContext` -- share the same interface. Strategies deploy to any execution environment without modification.

### State Properties

| Property | Type | Description |
|---|---|---|
| `ctx.account` | `AccountState` | Equity, cash, unrealized PnL (aggregated across venues) |
| `ctx.positions` | `list[PositionInfo]` | All open positions (across all venues) |
| `ctx.pending_orders` | `list[Order]` | Unfilled orders |
| `ctx.current_candle` | `Candle` or `None` | The bar being processed |
| `ctx.timestamp` | `int` | Current unix timestamp (seconds) |

### Order Methods

All order methods accept an optional `venue` parameter. In single-venue backtests, venue defaults to `"default"`. In multi-venue backtests and live trading, specify the venue explicitly.

```python
# Market order -- fills at close price (backtest) or market price (live)
ctx.market_order(market, side, size, reduce_only=False, tag="", venue="default") -> str

# Limit order -- resting order at a specific price
ctx.limit_order(market, side, size, price, reduce_only=False, tag="", venue="default") -> str

# Stop-loss order -- triggers a market order at trigger_price
ctx.stop_order(market, side, size, trigger_price, tag="", venue="default") -> str

# Take-profit order -- triggers a market order when price reaches target
ctx.take_profit_order(market, side, size, trigger_price, tag="", venue="default") -> str

# Cancel a specific order by ID
ctx.cancel(order_id) -> bool

# Cancel all orders (optionally for one market)
ctx.cancel_all(market=None) -> int
```

### Convenience Methods

```python
# Close entire position for a market on a venue
ctx.close_position(market, venue="default") -> str | None

# Get a single position
ctx.position(market, venue="default") -> PositionInfo | None

# All positions on a specific venue
ctx.venue_positions(venue) -> list[PositionInfo]

# Venue cash balance
ctx.venue_balance(venue) -> float
ctx.venue_balances() -> dict[str, float]

# Transfer capital between venues (arrives after configurable delay)
ctx.transfer(from_venue, to_venue, amount) -> bool

# Pre-trade cost estimation
ctx.estimate_cost(market, size_usd) -> CostEstimate
```

### Market Data Methods

```python
# Multi-market candle history
ctx.get_candles(market, lookback=50) -> list[Candle]

# Funding rates (hourly)
ctx.get_funding_rate(market=None) -> float | None
ctx.get_funding_rates(market=None, lookback=24) -> list[tuple[int, float]]
ctx.get_funding_by_venue(market=None, lookback=24) -> dict[str, list[tuple[int, float]]]

# Orderbook
ctx.get_orderbook(market=None) -> OrderbookSnapshot | None
ctx.get_impact_price(market, side, size) -> float | None

# Open interest
ctx.get_open_interest(market=None) -> tuple[float, float] | None
ctx.get_open_interest_history(market=None, lookback=24) -> list[tuple]
```

---

## Multi-Market Strategies

Use `ctx.get_candles()` to read data from markets other than the one being iterated over. The UI auto-detects `ctx.get_candles("MARKET")` calls in the code editor and prompts you to download data for each referenced market.

```python
def on_candle(self, candle: Candle, history: list[Candle], ctx=None) -> Signal:
    if ctx is None:
        return Signal.HOLD

    # Read BTC candles as a leading indicator
    btc = ctx.get_candles("BTC-PERP", lookback=24)
    if len(btc) < 2:
        return Signal.HOLD

    btc_momentum = (btc[-1].close - btc[-2].close) / btc[-2].close

    if btc_momentum > 0.005:
        ctx.market_order("SOL-PERP", Side.LONG, 0.1)
    elif btc_momentum < -0.005:
        ctx.close_position("SOL-PERP")

    return Signal.HOLD
```

The engine accepts `Dict[str, List[Candle]]` for multi-market mode. No additional configuration needed beyond downloading the data.

---

## All 20 Built-In Strategy Templates

| File | Style | Category | Description |
|---|---|---|---|
| `momentum.py` | v1 | trend | Rate-of-change momentum |
| `ema_crossover.py` | v1 | trend | Fast/slow EMA crossover |
| `ma_crossover.py` | v1 | trend | Simple MA crossover |
| `rsi.py` | v1 | mean-rev | RSI overbought/oversold |
| `bollinger.py` | v1 | mean-rev | Bollinger Band mean reversion |
| `mean_reversion.py` | v1 | mean-rev | Z-score mean reversion |
| `macd_divergence.py` | v1 | trend | MACD histogram divergence |
| `rsi_macd_combo.py` | v2 | multi-signal | RSI + MACD combined signals |
| `atr_breakout.py` | v2 | trend | ATR-based breakout with stops |
| `breakout_momentum.py` | v2 | trend | Breakout momentum with confirmation |
| `momentum_breakout.py` | v2 | trend | Volume-confirmed momentum breakout |
| `dual_timeframe.py` | v2 | trend | Dual timeframe trend alignment |
| `vwap_reversion.py` | v2 | mean-rev | VWAP reversion with stops |
| `grid_trader.py` | v2 | defi | Grid trading with limit orders |
| `funding_harvest.py` | v2 | defi | Single-venue funding collection |
| `funding_arb.py` | v2 | defi | Cross-venue funding rate arbitrage |
| `multi_venue_funding.py` | v2 | defi | Multi-venue delta-neutral harvest |
| `funding_mean_reversion.py` | v2 | defi | Bollinger bands on funding rates |
| `basis_trade.py` | v2 | defi | Cross-venue basis trade |
| `mev_arb_monitor.py` | v2 | monitor | MEV arbitrage opportunity scanner (no trades) |

Strategy source files are in `flint/strategy/`. Per-strategy documentation is in `docs/strategies/`.

---

## Optimization with Optuna

Override the `parameters()` classmethod to declare which constructor arguments the optimizer should search:

```python
@classmethod
def parameters(cls) -> dict:
    return {
        "period":    {"type": "int",   "low": 5,   "high": 100, "default": 14},
        "oversold":  {"type": "float", "low": 10.0, "high": 40.0, "default": 30.0},
        "overbought":{"type": "float", "low": 60.0, "high": 90.0, "default": 70.0},
    }
```

Supported types:

| Type | Fields | Example |
|---|---|---|
| `int` | `low`, `high`, `default` | `{"type": "int", "low": 5, "high": 100}` |
| `float` | `low`, `high`, `default` | `{"type": "float", "low": 0.01, "high": 0.5}` |
| `categorical` | `choices`, `default` | `{"type": "categorical", "choices": ["ema", "sma"]}` |

The optimizer calls `parameters()`, samples values via Optuna, instantiates the strategy with those values, runs a full backtest, and records the chosen metric as the objective. All 20 built-in strategies support optimization.

### Running Optimization

Via the API:

```bash
curl -s -X POST http://localhost:8000/api/v1/optimize/run \
  -H "Content-Type: application/json" \
  -d '{"strategy": "rsi", "market": "SOL-PERP", "trials": 100,
       "start_ts": 1709251200, "end_ts": 1743465600}'
```

Via the UI: after any backtest run, click "Optimize" to launch a parameter search. Results show a ranked table of all trials with one-click "backtest with best params."

Supports Bayesian search (default, TPE sampler), grid search, and random search. Metrics: `sharpe_ratio`, `total_pnl`, `win_rate`, `max_drawdown`, `sortino`.

### Walk-Forward Validation

For more robust results, Flint supports walk-forward optimization. This splits the data into sequential train/test windows, optimizes on each training window, then validates on the following out-of-sample test window. This guards against overfitting to a single in-sample period.

---

## Strategy Loader and Security

User strategies are loaded dynamically by `flint/strategy/loader.py`. The loader uses AST validation to block unsafe code before execution.

### Allowed Imports

Any import outside this list is rejected at load time:

```
flint, numpy, math, statistics, collections, dataclasses,
typing, enum, abc, functools, itertools, operator, __future__
```

### Blocked Builtins

These builtins are blocked in user strategy code:

```
eval, exec, compile, __import__, breakpoint,
globals, locals, vars, open
```

Dangerous attribute accesses (`system`, `popen`, `spawn`, `call`, `check_output`, `getenv`, `environ`, `listdir`, `remove`, `rmdir`, `unlink`) are also blocked.

The backtest engine enforces a 300-second timeout and allows a maximum of 5 concurrent runs.

### Saving User Strategies

Via the API:

```bash
curl -s -X POST http://localhost:8000/api/v1/user-strategies \
  -H "Content-Type: application/json" \
  -d '{"name": "my_rsi", "code": "..."}'
```

Or paste directly into the Monaco code editor in BacktestLab.

---

## Common Pitfalls

### Not Enough History

If your strategy uses a 50-period SMA but there are only 20 candles in `history`, the indicator will either fail or return a meaningless value. Always guard with a length check:

```python
if len(history) < self.period:
    return Signal.HOLD
```

### Look-Ahead Bias

Never peek at future data. The `history` list contains only candles strictly before the current one. The `candle` parameter is the current bar. Using `candle.close` to make a decision is fine (the engine fills at the close price). Reading from a future index in history is not.

### Overfitting

High Sharpe ratios on in-sample data do not guarantee out-of-sample performance. Use walk-forward validation and keep the parameter count low. A strategy with 2--3 parameters is usually more robust than one with 10.

### State Leaking Between Runs

If your strategy accumulates state (counters, rolling windows, position flags), make sure `reset()` clears all of it. The optimizer runs many trials, and leftover state from a previous trial will corrupt the next one.
