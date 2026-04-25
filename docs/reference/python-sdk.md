# Python SDK Reference

In-process API — everything you get when you `import flint`. Use this when you don't want to run the HTTP server.

**Import root:** `flint` · **Strategy code:** `flint.strategy` · **Engine:** `flint.backtest.engine`

---

## Quick tour

```python
from flint.store import FlintStore
from flint.backtest.engine import BacktestEngine
from flint.strategy import MomentumStrategy

store = FlintStore("./data/flint.duckdb")
candles = store.query_candles("SOL-PERP", 3600, start_ts=..., end_ts=...)

engine = BacktestEngine(MomentumStrategy(), initial_capital=10_000, fee_rate=0.0005)
result = engine.run(candles)

print(result.total_pnl, result.sharpe_ratio, result.win_rate)
store.close()
```

---

## Strategy

### `flint.strategy.base.Strategy` (ABC)

Base class for every strategy.

```python
class Strategy(abc.ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def on_candle(
        self,
        candle: Candle,
        history: List[Candle],
        ctx: Optional[ExecutionContext] = None,
    ) -> Signal: ...

    @abstractmethod
    def reset(self) -> None: ...

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {}
```

#### `on_candle` contract

Called once per bar in backtest, paper, and live.

- **v1 style** — return `Signal.BUY` / `SELL` / `HOLD`. Engine handles position open/close.
- **v2 style** — use `ctx.market_order()`, `ctx.stop_order()`, etc. Always return `Signal.HOLD`.

`history` is a rolling window of past candles, most recent last; does **not** include `candle`.

#### `Strategy.parameters`

Return a dict describing optimizable hyperparameters:

```python
@classmethod
def parameters(cls) -> dict:
    return {
        "fast_period": {"type": "int",   "low": 5,   "high": 50,   "default": 12, "step": 1},
        "slow_period": {"type": "int",   "low": 20,  "high": 200,  "default": 26},
        "threshold":   {"type": "float", "low": 0.1, "high": 5.0,  "default": 1.0, "log": False},
        "mode":        {"type": "categorical", "choices": ["long", "short", "both"]},
    }
```

Consumed by:

- `flint optimize` (CLI) and `POST /api/v1/optimize/run`
- `flint.optimization.optimizer.StrategyOptimizer`

Default: empty dict → strategy is non-optimizable.

### Writing a strategy

**v1 — signal style:**

```python
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from flint.indicators import sma
from typing import List, Optional

class GoldenCross(Strategy):
    def __init__(self, fast=20, slow=50):
        self.fast, self.slow = fast, slow

    @property
    def name(self): return "golden-cross"

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < self.slow:
            return Signal.HOLD
        fast, slow = sma(history, self.fast), sma(history, self.slow)
        if fast > slow: return Signal.BUY
        if fast < slow: return Signal.SELL
        return Signal.HOLD

    def reset(self): pass

    @classmethod
    def parameters(cls):
        return {
            "fast": {"type": "int", "low": 5,  "high": 50,  "default": 20},
            "slow": {"type": "int", "low": 20, "high": 200, "default": 50},
        }
```

**v2 — context style:**

```python
def on_candle(self, candle, history, ctx=None):
    if ctx is None or len(history) < 20: return Signal.HOLD

    from flint.indicators import rsi, atr
    r, volatility = rsi(history, 14), atr(history, 14)

    if r < 30 and not ctx.positions:
        ctx.market_order(candle.market, Side.LONG, size=1.0)
        ctx.stop_order(candle.market, Side.SHORT, size=1.0,
                       trigger_price=candle.close - 2 * volatility)
    elif r > 70 and ctx.positions:
        ctx.close_position(candle.market)
        ctx.cancel_all()

    return Signal.HOLD
```

### Sandbox

User strategies loaded via `load_user_strategy()` (API, MCP, `flint backtest`) go through AST validation. Only these imports are allowed:

`flint`, `numpy`, `math`, `statistics`, `collections`, `dataclasses`, `typing`, `enum`, `abc`, `functools`, `itertools`, `operator`

Anything else raises `StrategyLoadError`. Filesystem, network, subprocess, and dunder access are blocked. `flint.strategy.loader.validate_strategy_code(code)` returns `(bool, error)` without executing.

### Loader

```python
from flint.strategy.loader import load_user_strategy, validate_strategy_code, StrategyLoadError

ok, err = validate_strategy_code(src)               # lint only
strat = load_user_strategy(src, params={"x": 1})    # instantiate
```

---

## ExecutionContext

`flint.execution.context.ExecutionContext` (ABC) — uniform order API across backtest / paper / live.

### State

| Property | Type | Purpose |
|---|---|---|
| `account` | `AccountState` | `equity`, `cash`, `unrealized_pnl`, `margin_used`, `free_margin`, `leverage` |
| `positions` | `List[PositionInfo]` | Open positions across all venues |
| `pending_orders` | `List[Order]` | Unfilled orders |
| `current_candle` | `Candle?` | Bar currently being processed |
| `timestamp` | int | Unix seconds |

### Order placement

All return the `order_id` (str).

```python
ctx.market_order(market, side, size, reduce_only=False, tag="", venue="default")
ctx.limit_order(market, side, size, price, reduce_only=False, tag="", venue="default")
ctx.stop_order(market, side, size, trigger_price, tag="", venue="default")
ctx.take_profit_order(market, side, size, trigger_price, tag="", venue="default")
```

`side: Side.LONG` / `Side.SHORT`. `reduce_only=True` blocks orders that would open or flip a position.

### Order management

```python
ctx.cancel(order_id) -> bool
ctx.cancel_all(market: Optional[str] = None) -> int
```

### Convenience

```python
ctx.close_position(market, venue="default") -> Optional[str]
ctx.position(market, venue="default") -> Optional[PositionInfo]
ctx.venue_positions(venue) -> List[PositionInfo]
```

### Multi-market / multi-venue data

```python
ctx.get_candles(market, lookback=50)            # candles for another market
ctx.markets                                      # list of available markets

ctx.get_funding_rate(market)                    # latest hourly rate
ctx.get_funding_rates(market, lookback=24)      # history as [(ts, rate), ...]
ctx.get_funding_by_venue(market, lookback=24)   # {venue: [(ts, rate), ...]}
ctx.get_venue_snapshots(market, lookback=24)    # {venue: [FundingRate, ...]} with mark/oracle

ctx.get_borrow_rate(market, venue)
ctx.get_borrow_rates(market, venue, lookback=24)

ctx.get_orderbook(market)                        # OrderbookSnapshot
ctx.get_impact_price(market, side, size)         # walk the book for size

ctx.get_open_interest(market)                    # (long_oi, short_oi)
ctx.get_open_interest_history(market, lookback=24)
ctx.get_oracle_price(market)                     # (price, ts)
```

### Capital allocation

```python
ctx.venue_balance(venue) -> float
ctx.venue_balances() -> dict                    # {venue: cash}
ctx.transfer(from_venue, to_venue, amount) -> bool
```

Transfers are **not instant** — capital arrives after venue-configured delays.

### Concrete implementations

| Class | File | When |
|---|---|---|
| `BacktestContext` | `execution/backtest_context.py` | Candle-replay backtests |
| `PaperBroker` | `execution/paper_broker.py` | Paper trading (same fill models, live data) |
| `LiveDriftContext` | `execution/drift_live.py` | Drift via `driftpy` + Solana RPC |
| `LiveHyperliquidContext` | `execution/hyperliquid_live.py` | Hyperliquid via REST + EIP-712 |
| `LiveCCXTContext` | `execution/ccxt_live.py` | Any CCXT exchange |
| `MultiVenueLiveContext` | `execution/multi_venue_live.py` | Routes to multiple live contexts by `venue=` |

---

## BacktestEngine

`flint.backtest.engine.BacktestEngine` — single-threaded event-driven candle replay. Rust `flint_core` takes over automatically if installed.

```python
BacktestEngine(
    strategy: Strategy,
    initial_capital: float = 10_000.0,
    fee_rate: float = 0.0005,
    fill_model: Optional[FillModel] = None,         # default: FillPipeline
    funding_rates: Optional[list[FundingRate]] = None,
    orderbook_snapshots: Optional[list] = None,
    open_interest: Optional[list] = None,
    margin_engine: Optional[MarginEngine] = None,
    capital_allocator: Optional[VenueAllocator] = None,
    max_runtime_s: float = 300.0,
)
```

### `engine.run(candles, extra_markets=None) -> BacktestResult`

- `candles`: `List[Candle]` (single-market) **or** `Dict[str, List[Candle]]` (multi-market).
- `extra_markets`: dict passed through to `ctx.get_candles()` for v2 strategies.

### `BacktestResult`

```python
@dataclass
class BacktestResult:
    total_pnl: float
    win_rate: float
    max_drawdown: float
    sharpe_ratio: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    positions: List[Position]
    equity_curve: List[float]
    fills: List[Fill]
    total_fees: float
    funding_paid: float
    strategy_warnings: List[str]
    per_venue_pnl: Dict[str, float]
    per_venue_trades: Dict[str, int]
    per_venue_funding_income: Dict[str, float]
    total_tx_costs: float
    jupiter_borrow_paid: float
    borrow_payments: List
    margin_stats: Optional[Any]
```

Additional derived metrics (Sortino, Calmar, profit factor, turnover, Monte Carlo CIs) are computed on demand by `flint.analytics.tearsheet.generate_tearsheet(result)`.

### Fill models

```python
from flint.execution.fill_models import (
    ClosePriceFill,       # fill at candle close — cheapest, least realistic
    NextBarOpenFill,      # one-bar delay
    SlippageFill,         # flat bps
    FillPipeline,         # 4-tier: latency → impact → partial-fill (default)
)
```

See [concepts/fill-pipeline.md](../concepts/fill-pipeline.md) for the tiering logic.

---

## FlintStore

`flint.store.FlintStore` — single shared DuckDB connection, thread-safe via an internal lock.

**Never:** open a second DuckDB connection to the same file, access `store._conn` / `store._lock` from outside the class, or share a store across processes.

```python
from flint.store import FlintStore
store = FlintStore("./data/flint.duckdb")
```

### Query

```python
candles = store.query_candles(market, resolution_s, start_ts=None, end_ts=None, limit=None, venue=None)
count   = store.count_candles(market, resolution_s)

funding = store.query_funding_rates(market, start_ts=None, end_ts=None)
by_venue = store.query_funding_by_venue(market, start_ts=None, end_ts=None)

oi      = store.query_open_interest(market)
borrows = store.query_borrow_rates(market, start_ts, end_ts)
ob      = store.query_orderbook_snapshots(market, start_ts, end_ts)

synced  = store.is_range_synced(market, resolution_s, start_ts, end_ts)  # bool
fresh   = store.get_data_freshness()                                      # [{market, source, last_ts, age_s}]
```

### Upsert

```python
store.upsert_candles(candles)                   # -> int rows affected
store.upsert_funding_rates(rates)
store.upsert_open_interest(records)
store.upsert_orderbook_snapshot(snapshot)
```

### Strategy registry

```python
store.get_strategy(name)
store.upsert_strategy(strategy_id, name, code, params_json, category)
store.update_strategy_status(name, status)      # "backtest" / "paper" / "live"
```

### Journal

```python
store.save_backtest_run(run)
store.list_backtest_runs(limit=20)
store.get_backtest_run(run_id)
store.delete_backtest_run(run_id)
```

### Close

```python
store.close()                                   # always call in non-server code
```

Or use as a context manager (via `contextlib.closing`).

---

## Data models (`flint.models`)

| Model | Purpose |
|---|---|
| `Candle` | OHLCV bar. Frozen dataclass — `ts`, `open`, `high`, `low`, `close`, `volume`, `market`, `resolution_s`, `venue` |
| `Signal` | Enum: `BUY` / `SELL` / `HOLD` |
| `Side` | Enum: `LONG` / `SHORT` |
| `OrderType` | `MARKET` / `LIMIT` / `STOP_LOSS` / `TAKE_PROFIT` |
| `OrderStatus` | Backtest/paper: `PENDING` / `FILLED` / `PARTIALLY_FILLED` / `CANCELLED` |
| `OrderState` | Live full lifecycle: `PENDING` / `SUBMITTED` / `CONFIRMED` / `FILLED` / `PARTIALLY_FILLED` / `CANCELLED` / `EXPIRED` / `FAILED` |
| `TimeInForce` | `IOC` / `FOK` / `GTC` |
| `Order` | Mutable order record |
| `Fill` | Frozen fill record: price, size, fee, tx_sig, latency, impact, tx_cost |
| `Position` | Backtest position with `close(price, ts) -> pnl` |
| `PositionInfo` | Read-only view for strategies (no mutation) |
| `AccountState` | Equity / cash / margin snapshot |
| `FundingRate` | `market`, `ts`, `rate`, `oracle_price`, `mark_price`, `source` |
| `BorrowSnapshot` | Jupiter Perps borrow rate |
| `OrderbookSnapshot` | `bids`, `asks` as tuples of `OrderbookLevel` |
| `MarketInfo` | Protocol metadata (tick size, fees, oracle) |
| `PoolState` | AMM pool snapshot for arb detection |
| `ArbRoute` | Profitable arb path (pools, tokens, profit, hops) |
| `LiquidationOpportunity` | Drift/Mango position near liquidation |
| `OraclePrice`, `OpenInterest`, `Liquidation`, `WhaleTransfer`, `DexVolume`, `TokenUnlock` | Collector record types |
| `LegGroup`, `LegGroupResult` | Cross-venue paired orders |

Frozen dataclasses are safe to hash/pass across threads. Mutable ones (`Order`, `Position`, `AccountState`) must not be shared between strategy and engine contexts.

---

## Indicators (`flint.indicators`)

All take `(history: List[Candle], period: int)` and return `float` (or raise `IndexError` if history is shorter than `period`).

```python
from flint.indicators import (
    sma, ema, wma,                                 # moving averages
    rsi, stochastic, macd,                         # oscillators
    bollinger, bollinger_width,                    # bands
    atr, volatility,                               # volatility
    vwap, volume_ratio,                            # volume
    roc, adx,                                      # momentum / trend
    z_score,                                        # statistical
    highest_high, lowest_low,                      # structural
)
```

See [reference/indicators.md](indicators.md) for signatures, return shapes (scalar vs tuple), and formulas.

---

## Configuration

```python
from flint.config import load_config, FlintConfig

cfg = load_config()                              # merges env + .env + flint.yaml + defaults
cfg.db_path, cfg.default_markets, cfg.live_network
```

See [reference/config.md](config.md) for the full schema.

---

## Providers

`flint.providers.registry.ProviderRegistry` + concrete classes in `flint.providers.*`. All inherit `DataProvider`.

```python
from flint.providers.drift_candles import DriftCandleProvider
from flint.providers.funding_rates import BinanceFundingProvider

p = DriftCandleProvider()
candles = p.fetch_candles("SOL-PERP", 3600, start_ts, end_ts)
p.close()
```

Full provider list: [reference/data-providers.md](data-providers.md).

### Adding a provider

Inherit `DataProvider`, implement `fetch_candles()` / `fetch_funding()` as appropriate, register in `flint/providers/__init__.py`, toggle in `flint.yaml`. Walkthrough: [tutorials/06-custom-data-provider.md](../tutorials/06-custom-data-provider.md).

---

## Built-in strategies

All 20 are importable from `flint.strategy`:

```python
from flint.strategy import (
    MACrossoverStrategy, EMACrossoverStrategy, RSIStrategy, BollingerStrategy,
    MomentumStrategy, FundingHarvestStrategy, MeanReversionStrategy,
    BreakoutMomentumStrategy, GridTraderStrategy, DualTimeframeStrategy,
    VWAPReversionStrategy, MACDDivergenceStrategy, ATRBreakoutStrategy,
    MultiVenueFundingStrategy, RSIMACDComboStrategy, FundingMeanReversionStrategy,
    MomentumBreakoutStrategy, FundingArbStrategy, BasisTradeStrategy, MevArbMonitor,
)
```

Default parameters and categorization: [reference/strategy-templates.md](strategy-templates.md).

---

## Optimization

```python
from flint.optimization.optimizer import StrategyOptimizer, OptimizationResult

optimizer = StrategyOptimizer(
    strategy_class=MyStrategy,
    candles=candles,
    metric="sharpe_ratio",
    n_trials=100,
    initial_capital=10_000,
)
result = optimizer.optimize()
result.best_value, result.best_params, result.trials
```

Walk-forward: `flint.optimization.walk_forward.run_walk_forward(...)`.

---

## Analytics

```python
from flint.analytics.tearsheet import generate_tearsheet
from flint.analytics.monte_carlo import run_monte_carlo
from flint.analytics.correlation import compute_correlation_matrix

tear = generate_tearsheet(result)                              # full metrics
mc   = run_monte_carlo(result.equity_curve, iterations=500)    # bootstrap CIs
corr = compute_correlation_matrix({"SOL-PERP": c1, "BTC-PERP": c2})
```

---

## See also

- [REST API](rest-api.md) — same functionality, over HTTP
- [CLI](cli.md) — `flint backtest`, `flint optimize`, etc.
- [Strategy templates](strategy-templates.md) — full catalog with defaults
- [Indicators](indicators.md) — signatures and formulas
- [concepts/execution-contexts.md](../concepts/execution-contexts.md) — backtest vs paper vs live semantics
