# Strategy Authoring Guide

This guide covers everything you need to write, test, and optimize trading strategies in Flint.

## Overview

All strategies inherit from the `Strategy` abstract base class in `flint/strategy/base.py`. Flint includes 15 built-in strategy templates. User strategies live in `strategies/user/` (gitignored) and are loaded via the API or UI.

There are two strategy styles:

- **v1 (Signal-based)**: `on_candle()` returns `Signal.BUY`, `Signal.SELL`, or `Signal.HOLD`. The engine handles position sizing and order placement.
- **v2 (Context-based)**: `on_candle()` calls `ctx.market_order()`, `ctx.limit_order()`, etc. directly and returns `Signal.HOLD`. Full control over order logic.

---

## Strategy ABC

Every strategy must implement these three members:

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

- `name` — unique identifier shown in the UI and results.
- `on_candle(candle, history, ctx)` — called on every bar. `candle` is the current bar; `history` is all bars up to (but not including) the current one.
- `reset()` — called before every backtest run to clear accumulated state (counters, EMAs, etc.).
- `parameters()` — optional classmethod for Optuna optimization (see below).

---

## v1: Signal-Based Strategy

Return `Signal.BUY`, `Signal.SELL`, or `Signal.HOLD`. The engine opens/closes positions automatically using the configured fee rate and position sizing.

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
```

Available indicators in `flint.indicators`: `sma`, `ema`, `rsi`, `macd`, `bollinger`, `atr`, `vwap`, `adx`, and 12 more.

---

## v2: Context-Based Strategy

Use the `ExecutionContext` (`ctx`) to place orders directly. Always return `Signal.HOLD` — signals are ignored when you manage orders manually.

```python
from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side
from flint import indicators

class BollingerBreakoutStrategy(Strategy):
    def __init__(self, period: int = 20, std_dev: float = 2.0, size: float = 0.1):
        self.period = period
        self.std_dev = std_dev
        self.size = size

    @property
    def name(self) -> str:
        return f"BBBreakout({self.period})"

    def reset(self) -> None:
        pass

    def on_candle(self, candle: Candle, history: list[Candle], ctx=None) -> Signal:
        if ctx is None or len(history) < self.period:
            return Signal.HOLD

        closes = [c.close for c in history] + [candle.close]
        upper, mid, lower = indicators.bollinger(closes, self.period, self.std_dev)

        pos = ctx.position("SOL-PERP")

        if pos is None:
            if candle.close > upper:
                ctx.market_order("SOL-PERP", Side.LONG, self.size, tag="bb_break_up")
            elif candle.close < lower:
                ctx.market_order("SOL-PERP", Side.SHORT, self.size, tag="bb_break_down")
        else:
            if pos.side == Side.LONG and candle.close < mid:
                ctx.close_position("SOL-PERP")
            elif pos.side == Side.SHORT and candle.close > mid:
                ctx.close_position("SOL-PERP")

        return Signal.HOLD
```

---

## ExecutionContext API

The `ctx` parameter is an instance of `ExecutionContext` (defined in `flint/execution/context.py`). Both `BacktestContext` and live contexts implement the same interface.

### State Properties

| Property | Type | Description |
|---|---|---|
| `ctx.account` | `AccountState` | Equity, cash, unrealized PnL |
| `ctx.positions` | `list[PositionInfo]` | All open positions |
| `ctx.pending_orders` | `list[Order]` | Unfilled orders |
| `ctx.current_candle` | `Candle \| None` | The bar being processed |
| `ctx.timestamp` | `int` | Current unix timestamp (seconds) |
| `ctx.markets` | `list[str]` | Available markets in this context |

### Order Methods

```python
# Market order — immediate fill at close price
ctx.market_order(market, side, size, reduce_only=False, tag="", venue="default") -> str

# Limit order — resting order at a specific price
ctx.limit_order(market, side, size, price, reduce_only=False, tag="", venue="default") -> str

# Stop-loss order — triggers market sell at trigger_price
ctx.stop_order(market, side, size, trigger_price, tag="", venue="default") -> str

# Take-profit order
ctx.take_profit_order(market, side, size, trigger_price, tag="", venue="default") -> str

# Cancel a specific order
ctx.cancel(order_id) -> bool

# Cancel all orders (optionally for one market)
ctx.cancel_all(market=None) -> int
```

### Convenience Methods

```python
# Close entire position for market+venue
ctx.close_position(market, venue="default") -> str | None

# Get a single position
ctx.position(market, venue="default") -> PositionInfo | None

# All positions on a specific venue
ctx.venue_positions(venue) -> list[PositionInfo]

# Venue cash balance
ctx.venue_balance(venue) -> float
ctx.venue_balances() -> dict[str, float]

# Transfer capital between venues (async — arrives after delay)
ctx.transfer(from_venue, to_venue, amount) -> bool
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

Pass a `Dict[str, List[Candle]]` to the engine to enable multi-market mode. Use `ctx.get_candles()` inside your strategy to read data from any market.

```python
def on_candle(self, candle: Candle, history: list[Candle], ctx=None) -> Signal:
    if ctx is None:
        return Signal.HOLD

    # Read BTC candles as a leading indicator
    btc = ctx.get_candles("BTC-PERP", lookback=24)
    if len(btc) < 2:
        return Signal.HOLD

    btc_momentum = (btc[-1].close - btc[-2].close) / btc[-2].close

    # Trade SOL based on BTC direction
    if btc_momentum > 0.005:
        ctx.market_order("SOL-PERP", Side.LONG, 0.1)
    elif btc_momentum < -0.005:
        ctx.close_position("SOL-PERP")

    return Signal.HOLD
```

The UI auto-detects `ctx.get_candles("MARKET")` calls in the strategy editor and prompts you to select data for each referenced market.

---

## Cross-Venue Strategies

Specify `venue=` on order methods to route to a particular venue. The `FundingArbStrategy` is a reference implementation.

```python
# Long on drift (low funding), short on hyperliquid (high funding)
ctx.market_order("SOL-PERP", Side.LONG, size, venue="drift")
ctx.market_order("SOL-PERP", Side.SHORT, size, venue="hyperliquid")
```

Enable multi-venue in the backtest request:

```json
{
  "strategy": "funding_arb",
  "market": "SOL-PERP",
  "capital_allocation": {"drift": 5000, "hyperliquid": 5000},
  "margin_tracking": true
}
```

Per-venue fee/margin configs are in `flint/execution/venue_config.py` (Drift, Hyperliquid, Binance, OKX, Bybit, dYdX).

---

## Optuna Optimization

Override `parameters()` to declare which constructor arguments the optimizer should search:

```python
@classmethod
def parameters(cls) -> dict:
    return {
        "period":    {"type": "int",   "low": 5,   "high": 100, "default": 14},
        "oversold":  {"type": "float", "low": 10.0, "high": 40.0, "default": 30.0},
        "overbought":{"type": "float", "low": 60.0, "high": 90.0, "default": 70.0},
    }
```

Supported types: `int`, `float`, `categorical` (add `"choices": [...]`).

The optimizer calls `parameters()`, samples values via Optuna, instantiates the strategy with those values, runs a full backtest, and records the Sharpe ratio as the objective. Trigger via the API:

```bash
curl -s -X POST http://localhost:8000/api/v1/optimize/run \
  -H "Content-Type: application/json" \
  -d '{"strategy": "rsi", "market": "SOL-PERP", "trials": 100,
       "start_ts": 1709251200, "end_ts": 1743465600}'
```

---

## Strategy Loader and Security

User strategies are loaded dynamically. The loader uses AST validation to block unsafe code before execution.

**Allowed imports only** — any import outside this list is rejected at load time:

```
flint, numpy, math, statistics, collections, dataclasses,
typing, enum, abc, functools, itertools, operator
```

Dangerous builtins (`eval`, `exec`, `open`, `__import__`, etc.) are also blocked. The backtest engine enforces a 300-second timeout and allows a maximum of 5 concurrent runs.

Save a user strategy via the API:

```bash
curl -s -X POST http://localhost:8000/api/v1/user-strategies \
  -H "Content-Type: application/json" \
  -d '{"name": "my_rsi", "code": "..."}'
```

---

## Examples and Templates

The 15 built-in strategies in `flint/strategy/` are all readable reference implementations:

| File | Style | Description |
|---|---|---|
| `momentum.py` | v1 | Rate-of-change momentum |
| `ema_crossover.py` | v1 | Fast/slow EMA crossover |
| `rsi.py` | v1 | RSI overbought/oversold |
| `bollinger.py` | v1 | Bollinger Band mean reversion |
| `funding_arb.py` | v2 | Cross-venue funding rate arbitrage |
| `funding_harvest.py` | v2 | Single-venue funding collection |
| `multi_venue_funding.py` | v2 | Multi-venue delta-neutral harvest |
| `mean_reversion.py` | v1 | Z-score mean reversion |
| `vwap_reversion.py` | v2 | VWAP reversion with stops |
| `grid_trader.py` | v2 | Grid trading with limit orders |

See also `docs/strategies/` for per-strategy README files.
