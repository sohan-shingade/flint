export interface DocCodeBlock {
  language: string
  code: string
}

export interface DocTopic {
  id: string
  title: string
  content: string // HTML string
  codeBlocks?: DocCodeBlock[]
}

export interface DocSection {
  id: string
  title: string
  topics: DocTopic[]
}

export const docsContent: DocSection[] = [
  // ──────────────────────────────────────────────────
  // SECTION 1: GETTING STARTED
  // ──────────────────────────────────────────────────
  {
    id: 'getting-started',
    title: 'GETTING STARTED',
    topics: [
      {
        id: 'installation',
        title: 'Installation',
        content: `
          <p>Flint requires <strong>Python 3.9+</strong> and runs on macOS, Linux, and WSL. The engine uses DuckDB for local analytics storage — no external database needed.</p>
          <h4>Prerequisites</h4>
          <ul>
            <li>Python 3.9 or later</li>
            <li>pip or a PEP-517-compatible installer</li>
            <li>Node.js 18+ (for the dashboard UI)</li>
          </ul>
          <h4>Install from source</h4>
          <p>Clone the repository and install in editable mode. The <code>[dev]</code> extra pulls in pytest and other development tools.</p>
          <h4>Start the stack</h4>
          <p>The API server runs on <code>localhost:8000</code> by default. The UI dev server proxies API calls automatically.</p>
        `,
        codeBlocks: [
          {
            language: 'bash',
            code: `git clone https://github.com/your-org/flint.git
cd flint
pip install -e ".[dev]"`,
          },
          {
            language: 'bash',
            code: `# Start the API server
uvicorn flint.api.app:app --reload

# In a second terminal, start the UI
cd ui && npm install && npm run dev`,
          },
        ],
      },
      {
        id: 'quick-start',
        title: 'Quick Start',
        content: `
          <p>The fastest way to see Flint in action is to run a backtest against built-in sample data. This walkthrough takes about two minutes.</p>
          <h4>1. Collect candle data</h4>
          <p>Use the collector API to fetch historical candles from Drift. The collector stores data locally in DuckDB, so subsequent runs are nearly instant.</p>
          <h4>2. Run a backtest</h4>
          <p>Hit the backtest endpoint with a strategy name and market. The engine replays candles through your strategy and returns a full performance report including equity curve, drawdown, Sharpe ratio, and individual trades.</p>
          <h4>3. Open the dashboard</h4>
          <p>Navigate to <code>http://localhost:5173</code> and click <strong>LAB</strong> in the top nav. Select a strategy, tune parameters, and visualize results — all without writing any code.</p>
        `,
        codeBlocks: [
          {
            language: 'bash',
            code: `# Trigger a candle collection job
curl -X POST http://localhost:8000/api/v1/collector/collect \\
  -H "Content-Type: application/json" \\
  -d '{"market": "SOL-PERP", "data_type": "candles"}'`,
          },
          {
            language: 'bash',
            code: `# Run an MA crossover backtest
curl http://localhost:8000/api/v1/backtest/run \\
  -H "Content-Type: application/json" \\
  -d '{
    "strategy": "ma_crossover",
    "market": "SOL-PERP",
    "resolution_s": 3600,
    "params": {"fast_period": 10, "slow_period": 30}
  }'`,
          },
        ],
      },
      {
        id: 'project-structure',
        title: 'Project Structure',
        content: `
          <p>Flint is organised as a Python package with a React UI. The layout follows a clean separation between engine, API, and frontend.</p>
          <h4>Key directories</h4>
          <ul>
            <li><code>flint/strategy/</code> — Strategy base class and built-in strategies</li>
            <li><code>flint/models.py</code> — Core data models (Candle, Signal, Position, etc.)</li>
            <li><code>flint/backtest/</code> — Backtest engine that replays candles through strategies</li>
            <li><code>flint/api/</code> — FastAPI routes for backtest, data, and collector</li>
            <li><code>flint/collector/</code> — Data collector service for Drift and DEX data</li>
            <li><code>flint/store/</code> — DuckDB storage layer for candles, funding rates, orderbooks</li>
            <li><code>ui/</code> — React + TypeScript + Tailwind dashboard</li>
            <li><code>data/</code> — Local DuckDB database files (auto-created)</li>
          </ul>
          <h4>Configuration</h4>
          <p>Flint reads from environment variables. Copy <code>.env.example</code> to <code>.env</code> and adjust as needed. The only required setting for local development is the Solana RPC URL.</p>
        `,
        codeBlocks: [
          {
            language: 'text',
            code: `flint/
  strategy/
    base.py          # Strategy ABC
    ma_crossover.py  # Built-in MA crossover
    rsi.py           # Built-in RSI mean reversion
    loader.py        # Dynamic strategy loading
  models.py          # Candle, Signal, Trade, Position, ...
  backtest/
    engine.py        # Core backtest loop
  api/
    app.py           # FastAPI application
    routes/          # Route modules
  collector/
    service.py       # Data collection orchestration
  store/
    duckdb_store.py  # DuckDB read/write layer
ui/
  src/
    pages/           # React page components
    components/      # Reusable UI components`,
          },
        ],
      },
    ],
  },

  // ──────────────────────────────────────────────────
  // SECTION 2: SOLANA FOR QUANTS
  // ──────────────────────────────────────────────────
  {
    id: 'solana-for-quants',
    title: 'SOLANA FOR QUANTS',
    topics: [
      {
        id: 'slots-vs-time',
        title: 'Slots vs Time',
        content: `
          <p>If you come from TradFi or CEX trading, you think in <strong>timestamps</strong>: candles close at fixed UTC intervals, order books update in real-time, and latency is measured in milliseconds of network round-trip.</p>
          <p>Solana introduces a different primitive: the <strong>slot</strong>. A slot is the basic unit of block production, roughly 400ms under normal conditions. Validators take turns producing blocks in a fixed schedule, and every transaction is anchored to a slot number rather than a wall-clock timestamp.</p>
          <h4>Why this matters for quant strategies</h4>
          <ul>
            <li><strong>Slot drift:</strong> Solana's target is 400ms/slot, but actual slot times vary from 300ms to 600ms+ depending on network load. A strategy that assumes constant time intervals will accumulate timing errors.</li>
            <li><strong>Skip slots:</strong> When a leader validator is offline, its slots are skipped. Your data may have gaps that don't correspond to missing data — they correspond to missing blocks.</li>
            <li><strong>Ordering guarantees:</strong> Within a slot, transaction order is determined by the leader. Across slots, ordering is strict. This is why MEV on Solana looks different from Ethereum.</li>
          </ul>
          <p>Flint normalises slot-based data into time-bucketed candles for backtesting, but the raw slot numbers are preserved in models like <code>FundingRate</code> and <code>OrderbookSnapshot</code> for strategies that need sub-candle precision.</p>
        `,
      },
      {
        id: 'drift-protocol',
        title: 'Drift Protocol',
        content: `
          <p><strong>TradFi analogy:</strong> Drift is the Solana equivalent of a perpetual futures exchange like Binance Futures or dYdX, but fully on-chain with a hybrid orderbook + AMM design.</p>
          <p>Drift Protocol is the primary venue Flint connects to for perpetual futures data. It operates a virtual AMM (vAMM) backed by an insurance fund, combined with a decentralised limit order book (DLOB).</p>
          <h4>Key concepts for quants</h4>
          <ul>
            <li><strong>Market indices:</strong> Each perp market has a numeric index (SOL-PERP = 0, BTC-PERP = 1, etc.). Flint maps these to human-readable symbols.</li>
            <li><strong>Oracle-driven pricing:</strong> Drift uses Pyth oracles for mark price calculations. The oracle price feeds directly into funding rate computation — a familiar concept if you've traded perps on Binance.</li>
            <li><strong>vAMM mechanics:</strong> When the DLOB doesn't have matching liquidity, trades fill against the vAMM at an oracle-adjusted price. This means slippage behaves differently from a pure CLOB exchange.</li>
            <li><strong>Cross-margin:</strong> Drift supports cross-margining across perp and spot positions, similar to portfolio margin at a prime broker.</li>
          </ul>
          <p>Flint's <code>MarketInfo</code> model captures the key parameters for each Drift market: tick size, min order size, and fee schedule.</p>
        `,
      },
      {
        id: 'amm-pools',
        title: 'AMM Pools',
        content: `
          <p><strong>TradFi analogy:</strong> An AMM pool is like a market maker that quotes both sides using a mathematical formula instead of a human trader or HFT bot. Think of it as a deterministic, transparent specialist on the NYSE floor.</p>
          <p>Solana hosts several DEXes that use constant-product AMMs (Raydium, Orca) and concentrated liquidity AMMs (Orca Whirlpools, Raydium CLMM). Understanding how they work is critical for arbitrage and execution strategies.</p>
          <h4>Constant-product pools</h4>
          <p>The classic <code>x * y = k</code> invariant. When you buy token A, you deposit token B, and the price moves along a hyperbolic curve. Slippage increases with trade size relative to pool reserves.</p>
          <h4>Concentrated liquidity</h4>
          <p>Similar to Uniswap v3 on Ethereum. LPs concentrate capital in a price range, giving better capital efficiency but requiring active management — very similar to maintaining a market-making inventory on a CLOB.</p>
          <h4>Implications for strategy development</h4>
          <ul>
            <li><strong>Predictable pricing:</strong> Unlike a CLOB where the book can change at any moment, AMM prices are a deterministic function of reserves. You can calculate exact execution prices before submitting a transaction.</li>
            <li><strong>Atomic composability:</strong> Multiple AMM swaps can be chained in a single Solana transaction. This enables atomic arbitrage that is impossible on CEXes.</li>
          </ul>
          <p>Flint's <code>PoolState</code> model captures reserve levels and fee rates, and computes mid prices via the <code>price_a_in_b</code> and <code>price_b_in_a</code> properties.</p>
        `,
        codeBlocks: [
          {
            language: 'python',
            code: `# PoolState model — reserves-based pricing
from flint.models import PoolState

pool = PoolState(
    pool_address="7XaWhH...",
    dex="raydium",
    token_a_mint="So11111...",  # SOL
    token_b_mint="EPjFWdd...",  # USDC
    reserve_a=150_000.0,
    reserve_b=22_500_000.0,
    fee_rate=0.0025,
)
print(f"SOL price: \${pool.price_a_in_b:.2f}")
# SOL price: $150.00`,
          },
        ],
      },
      {
        id: 'funding-rates',
        title: 'Funding Rates',
        content: `
          <p><strong>TradFi analogy:</strong> Funding rates on perps are conceptually similar to the cost of carry in futures markets, or the overnight financing rate on a CFD position. They are the mechanism that keeps perpetual futures prices anchored to spot.</p>
          <p>On Drift, funding is calculated and settled every hour. When the perp trades above the oracle price, longs pay shorts (positive funding). When it trades below, shorts pay longs (negative funding).</p>
          <h4>Funding rate mechanics</h4>
          <ul>
            <li><strong>Calculation:</strong> <code>funding_rate = (mark_price - oracle_price) / oracle_price / 24</code> — normalized to an hourly rate.</li>
            <li><strong>Settlement:</strong> Funding is settled automatically against user accounts. Unlike CEXes, there's no "funding timestamp" where the rate is applied — it accrues continuously and settles on-chain.</li>
            <li><strong>Magnitude:</strong> Typical funding rates on Drift range from -0.01% to +0.05% per hour during normal conditions. During volatility spikes, rates can exceed 0.5%/hr — creating significant opportunities.</li>
          </ul>
          <h4>Strategy implications</h4>
          <p>Funding rate harvesting — collecting funding by holding the side that receives payment — is one of the highest-risk-adjusted strategies in DeFi perps. The key insight is that on Solana, you can delta-hedge your perp position atomically with a spot position on an AMM, eliminating directional risk while collecting funding.</p>
          <p>Flint's <code>FundingRate</code> model stores the raw rate along with oracle and mark prices for each hourly observation.</p>
        `,
        codeBlocks: [
          {
            language: 'python',
            code: `# Analyzing funding rate data
from flint.models import FundingRate

rate = FundingRate(
    market="SOL-PERP",
    ts=1710000000,
    rate=0.00035,       # 0.035% per hour
    oracle_price=148.52,
    mark_price=149.10,
    slot=250_000_000,
)

# Annualized funding: 0.035% * 24 * 365 = 306.6%
annual = rate.rate * 24 * 365 * 100
print(f"Annualized funding: {annual:.1f}%")`,
          },
        ],
      },
      {
        id: 'mev-on-solana',
        title: 'MEV on Solana',
        content: `
          <p><strong>TradFi analogy:</strong> MEV (Maximal Extractable Value) on Solana is analogous to latency arbitrage and order flow internalisation in traditional markets. The validator that produces a block has the same structural advantage as a co-located HFT firm on a traditional exchange.</p>
          <p>Solana MEV differs fundamentally from Ethereum MEV because Solana has no public mempool by default. Transactions are streamed directly to the current leader validator via QUIC, making the MEV landscape more about validator-side ordering than searcher-side bidding.</p>
          <h4>MEV types on Solana</h4>
          <ul>
            <li><strong>DEX arbitrage:</strong> Price discrepancies between AMM pools. Atomic multi-hop swaps in a single transaction. This is the bread and butter — high frequency, low margin.</li>
            <li><strong>Liquidations:</strong> Monitoring Drift/Mango positions approaching their liquidation threshold. The liquidator earns a fee for closing unhealthy positions.</li>
            <li><strong>Oracle front-running:</strong> When a Pyth oracle update will move the mark price significantly, a searcher can position themselves before the update lands. Drift has mechanisms to mitigate this, but opportunities still exist on smaller protocols.</li>
            <li><strong>JIT (Just-In-Time) liquidity:</strong> Providing liquidity into a concentrated AMM pool just before a large swap, earning fees, then withdrawing. Analogous to HFT market-making on traditional exchanges.</li>
          </ul>
          <h4>Jito and block space auctions</h4>
          <p>Jito introduces a bundle system and tip mechanism that gives searchers a way to bid for transaction priority — similar to Flashbots on Ethereum but adapted for Solana's architecture. Flint tracks MEV opportunities through the <code>ArbRoute</code> and <code>LiquidationOpportunity</code> models.</p>
        `,
      },
      {
        id: 'on-chain-order-books',
        title: 'On-chain Order Books',
        content: `
          <p><strong>TradFi analogy:</strong> Drift's Decentralised Limit Order Book (DLOB) functions like a central limit order book (CLOB) on a traditional exchange — bids, asks, price-time priority — except the matching happens on-chain with permissionless access to the order flow.</p>
          <p>On-chain CLOBs on Solana are feasible because of Solana's high throughput (~3,000 TPS) and low fees (~$0.0003/tx). This isn't possible on Ethereum L1 where each order placement/cancellation costs several dollars in gas.</p>
          <h4>How Drift's DLOB works</h4>
          <ul>
            <li><strong>Limit orders:</strong> Users submit limit orders that are stored on-chain. "Keeper" bots (similar to keepers in MakerDAO) match orders when conditions are met.</li>
            <li><strong>Market orders:</strong> Fill against the DLOB first, then any residual fills against the vAMM at oracle-adjusted price.</li>
            <li><strong>Trigger orders:</strong> Stop-loss and take-profit orders that keepers execute when the oracle price crosses a threshold.</li>
          </ul>
          <h4>Data implications</h4>
          <p>Flint captures orderbook snapshots via the <code>OrderbookSnapshot</code> model, which stores the top N bid/ask levels with their sizes. For strategies that need depth-of-book analytics — spread analysis, order flow imbalance, Kyle's lambda — this is the raw material.</p>
        `,
        codeBlocks: [
          {
            language: 'python',
            code: `# Working with orderbook snapshots
from flint.models import OrderbookSnapshot, OrderbookLevel

snapshot = OrderbookSnapshot(
    market="SOL-PERP",
    ts=1710000000,
    bids=(
        OrderbookLevel(price=148.50, size=120.0),
        OrderbookLevel(price=148.40, size=85.0),
        OrderbookLevel(price=148.30, size=200.0),
    ),
    asks=(
        OrderbookLevel(price=148.60, size=95.0),
        OrderbookLevel(price=148.70, size=150.0),
        OrderbookLevel(price=148.80, size=60.0),
    ),
    slot=250_000_000,
)

spread = snapshot.asks[0].price - snapshot.bids[0].price
mid = (snapshot.asks[0].price + snapshot.bids[0].price) / 2
spread_bps = (spread / mid) * 10_000
print(f"Spread: \${spread:.2f} (\${spread_bps:.1f} bps)")`,
          },
        ],
      },
    ],
  },

  // ──────────────────────────────────────────────────
  // SECTION 3: STRATEGY API
  // ──────────────────────────────────────────────────
  {
    id: 'strategy-api',
    title: 'STRATEGY API',
    topics: [
      {
        id: 'strategy-base-class',
        title: 'Strategy Base Class',
        content: `
          <p>Every Flint strategy extends the <code>Strategy</code> abstract base class. This provides a minimal, opinionated interface: receive a candle, return a signal. The engine handles position management, PnL tracking, and risk controls.</p>
          <h4>Required methods</h4>
          <ul>
            <li><code>name</code> (property) — A human-readable identifier for the strategy. Used in logging, API responses, and the UI.</li>
            <li><code>on_candle(candle, history)</code> — The core decision function. Called once per candle with the current candle and a list of all prior candles. Returns a <code>Signal</code> enum: <code>BUY</code>, <code>SELL</code>, or <code>HOLD</code>.</li>
            <li><code>reset()</code> — Clears internal state. Called before each backtest run to ensure no data leakage between parameter sweeps or walk-forward windows.</li>
          </ul>
          <h4>Design philosophy</h4>
          <p>The strategy interface is deliberately simple. Complex strategies compose the same primitives: compute indicators from the history list, apply decision logic, return a signal. The engine converts signals into positions using configurable sizing and risk rules.</p>
        `,
        codeBlocks: [
          {
            language: 'python',
            code: `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List


class MyStrategy(Strategy):
    """Template for a custom Flint strategy."""

    def __init__(self, param_a: int = 10) -> None:
        self.param_a = param_a
        self._internal_state: float = 0.0

    @property
    def name(self) -> str:
        return f"MyStrategy(a={self.param_a})"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        if len(history) < self.param_a:
            return Signal.HOLD

        # Your logic here
        closes = [c.close for c in history[-self.param_a:]]
        avg = sum(closes) / len(closes)

        if candle.close > avg * 1.02:
            return Signal.BUY
        elif candle.close < avg * 0.98:
            return Signal.SELL
        return Signal.HOLD

    def reset(self) -> None:
        self._internal_state = 0.0`,
          },
        ],
      },
      {
        id: 'signals',
        title: 'Signals',
        content: `
          <p>The <code>Signal</code> enum is the atomic output of every strategy decision. Flint uses a deliberately constrained signal space to keep the strategy interface clean and the backtest engine straightforward.</p>
          <h4>Signal types</h4>
          <ul>
            <li><code>Signal.BUY</code> — Enter a long position (or close a short). If already long, this is a no-op in the default engine configuration.</li>
            <li><code>Signal.SELL</code> — Enter a short position (or close a long). If already short, this is a no-op.</li>
            <li><code>Signal.HOLD</code> — Maintain the current position. No action taken. This should be your default return when conditions are uncertain.</li>
          </ul>
          <h4>Signal flow in the engine</h4>
          <p>When the backtest engine receives a signal, it maps it to a position action based on the current state:</p>
          <ul>
            <li><strong>No position + BUY</strong> → open long at current candle close</li>
            <li><strong>Long + SELL</strong> → close long, record PnL</li>
            <li><strong>No position + SELL</strong> → open short at current candle close</li>
            <li><strong>Short + BUY</strong> → close short, record PnL</li>
          </ul>
          <p>The <code>Position</code> model tracks entry/exit prices, timestamps, and computed PnL for each round-trip trade.</p>
        `,
        codeBlocks: [
          {
            language: 'python',
            code: `from flint.models import Signal

# Signals are simple enums
assert Signal.BUY.value == "buy"
assert Signal.SELL.value == "sell"
assert Signal.HOLD.value == "hold"

# Pattern: guard clause for insufficient data
def on_candle(self, candle, history):
    if len(history) < self.lookback:
        return Signal.HOLD  # Not enough data yet

    # ... compute indicators ...

    if buy_condition:
        return Signal.BUY
    elif sell_condition:
        return Signal.SELL
    return Signal.HOLD  # Default: do nothing`,
          },
        ],
      },
      {
        id: 'candle-data',
        title: 'Candle Data',
        content: `
          <p>The <code>Candle</code> dataclass is the fundamental data unit in Flint. Every strategy receives candles as input, and the backtest engine uses candle prices for position entry/exit.</p>
          <h4>Candle fields</h4>
          <ul>
            <li><code>ts</code> (int) — Unix timestamp in seconds, representing the bucket start time.</li>
            <li><code>open</code> / <code>high</code> / <code>low</code> / <code>close</code> (float) — Standard OHLC prices.</li>
            <li><code>volume</code> (float) — Trading volume in base asset units (e.g. SOL, not USD).</li>
            <li><code>market</code> (str) — Market identifier, e.g. <code>"SOL-PERP"</code>.</li>
            <li><code>resolution_s</code> (int) — Candle width in seconds. Common values: 60 (1m), 300 (5m), 900 (15m), 3600 (1h), 86400 (1d).</li>
          </ul>
          <h4>Candle immutability</h4>
          <p>Candles are frozen dataclasses — once created, their fields cannot be modified. This prevents accidental look-ahead bias in strategies that might otherwise mutate historical data.</p>
          <h4>Working with history</h4>
          <p>The <code>history</code> parameter in <code>on_candle</code> contains all prior candles in chronological order. Use slicing to extract the window you need. The current candle is <em>not</em> included in the history list — it is passed as the first argument.</p>
        `,
        codeBlocks: [
          {
            language: 'python',
            code: `from flint.models import Candle
import numpy as np

# Candle is a frozen (immutable) dataclass
candle = Candle(
    ts=1710000000,
    open=148.50,
    high=149.20,
    low=147.80,
    close=149.10,
    volume=12500.5,
    market="SOL-PERP",
    resolution_s=3600,
)

# Common pattern: extract close prices as numpy array
def get_closes(history: list[Candle], n: int) -> np.ndarray:
    return np.array([c.close for c in history[-n:]])

# Typical indicator calculation
closes = get_closes(history, 20)
sma_20 = float(np.mean(closes))
std_20 = float(np.std(closes))
upper_band = sma_20 + 2 * std_20
lower_band = sma_20 - 2 * std_20`,
          },
        ],
      },
      {
        id: 'backtest-engine',
        title: 'Backtest Engine',
        content: `
          <p>The backtest engine is an event-driven loop that replays historical candles through a strategy instance. It handles position management, PnL calculation, and performance analytics.</p>
          <h4>How it works</h4>
          <ol>
            <li>Load candles from DuckDB for the specified market and time range</li>
            <li>Initialise the strategy and call <code>reset()</code></li>
            <li>For each candle: append to history, call <code>on_candle()</code>, process the returned signal</li>
            <li>Track equity curve, positions, and drawdown throughout</li>
            <li>Compute summary metrics and return a <code>BacktestResult</code></li>
          </ol>
          <h4>BacktestResult fields</h4>
          <ul>
            <li><code>total_pnl</code> — Net profit/loss in USD</li>
            <li><code>win_rate</code> — Percentage of profitable round-trip trades</li>
            <li><code>max_drawdown</code> — Largest peak-to-trough equity decline</li>
            <li><code>sharpe_ratio</code> — Risk-adjusted return (annualised, assuming hourly candles)</li>
            <li><code>total_trades</code> / <code>winning_trades</code> / <code>losing_trades</code> — Trade counts</li>
            <li><code>positions</code> — List of all <code>Position</code> objects with full entry/exit details</li>
            <li><code>equity_curve</code> — List of equity values at each candle for charting</li>
          </ul>
          <h4>API usage</h4>
          <p>The backtest is exposed at <code>POST /api/v1/backtest/run</code>. Pass a strategy name, market, resolution, and optional parameters. For custom strategies uploaded via the Strategy Lab, use the strategy file's name.</p>
        `,
        codeBlocks: [
          {
            language: 'python',
            code: `from flint.models import BacktestResult

# BacktestResult is returned by the engine
result = BacktestResult(
    total_pnl=1250.50,
    win_rate=0.62,
    max_drawdown=-0.08,
    sharpe_ratio=1.85,
    total_trades=47,
    winning_trades=29,
    losing_trades=18,
    positions=[...],
    equity_curve=[10000, 10050, 10120, ...],
)

# Key metrics for strategy evaluation
print(f"PnL: \${result.total_pnl:.2f}")
print(f"Win rate: {result.win_rate:.0%}")
print(f"Max DD: {result.max_drawdown:.1%}")
print(f"Sharpe: {result.sharpe_ratio:.2f}")`,
          },
        ],
      },
      {
        id: 'analytics',
        title: 'Analytics',
        content: `
          <p>Flint computes a standard set of performance analytics that map directly to what you'd see on a prime broker's risk dashboard or a fund factsheet. If you've used QuantConnect, Zipline, or Backtrader, these metrics will be familiar.</p>
          <h4>Core metrics</h4>
          <ul>
            <li><strong>Sharpe Ratio:</strong> Annualised risk-adjusted return. Computed as <code>(mean_return / std_return) * sqrt(periods_per_year)</code>. A Sharpe above 1.5 is strong; above 2.5 is exceptional.</li>
            <li><strong>Max Drawdown:</strong> The largest peak-to-trough decline in the equity curve. This is the single most important risk metric — it tells you the worst-case scenario your strategy has historically experienced.</li>
            <li><strong>Win Rate:</strong> Percentage of trades that were profitable. A trend-following strategy might have a 35% win rate but still be highly profitable if winners are much larger than losers.</li>
            <li><strong>Profit Factor:</strong> Gross profits divided by gross losses. A profit factor above 1.5 suggests a robust edge.</li>
          </ul>
          <h4>Equity curve and drawdown</h4>
          <p>The equity curve is stored as a list of cumulative PnL values at each candle. The UI renders this as a line chart in the LAB page, along with a separate drawdown chart showing underwater equity over time.</p>
          <h4>Interpreting results in DeFi context</h4>
          <p>Traditional metrics apply, but DeFi adds wrinkles: gas costs (trivial on Solana), slippage in AMM pools (can be modelled), and funding rate costs/income (can dominate perp strategy PnL). Always account for these when evaluating backtest results.</p>
        `,
      },
    ],
  },

  // ──────────────────────────────────────────────────
  // SECTION 4: EXAMPLES
  // ──────────────────────────────────────────────────
  {
    id: 'examples',
    title: 'EXAMPLES',
    topics: [
      {
        id: 'ma-crossover',
        title: 'MA Crossover',
        content: `
          <p>The moving average crossover is the quintessential trend-following strategy. When the fast SMA crosses above the slow SMA ("golden cross"), go long. When it crosses below ("death cross"), exit or go short.</p>
          <h4>How it works</h4>
          <p>The strategy tracks two simple moving averages of the close price. It compares the current crossover state with the previous candle's state to detect transitions. This avoids generating repeated signals while the fast MA stays above/below the slow MA.</p>
          <h4>Parameter tuning</h4>
          <ul>
            <li><strong>fast_period:</strong> Shorter window (default 10). Lower values react faster but generate more noise.</li>
            <li><strong>slow_period:</strong> Longer window (default 30). Higher values provide smoother signals but increase lag.</li>
          </ul>
          <h4>Performance characteristics</h4>
          <p>MA crossover works best in trending markets. In choppy, range-bound conditions it generates frequent whipsaws (false signals). For SOL-PERP, periods around 10/30 on hourly candles have historically produced reasonable results.</p>
        `,
        codeBlocks: [
          {
            language: 'python',
            code: `"""MA Crossover — Flint built-in strategy."""
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class MACrossoverStrategy(Strategy):
    def __init__(self, fast_period: int = 10, slow_period: int = 30) -> None:
        if fast_period >= slow_period:
            raise ValueError("fast_period must be < slow_period")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self._prev_fast: float = 0.0
        self._prev_slow: float = 0.0

    @property
    def name(self) -> str:
        return f"MA-Crossover({self.fast_period}/{self.slow_period})"

    def reset(self) -> None:
        self._prev_fast = 0.0
        self._prev_slow = 0.0

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        if len(history) < self.slow_period:
            return Signal.HOLD

        closes = np.array([c.close for c in history[-self.slow_period:]])
        fast_ma = float(np.mean(closes[-self.fast_period:]))
        slow_ma = float(np.mean(closes))

        signal = Signal.HOLD
        if self._prev_fast != 0.0 and self._prev_slow != 0.0:
            if self._prev_fast <= self._prev_slow and fast_ma > slow_ma:
                signal = Signal.BUY
            elif self._prev_fast >= self._prev_slow and fast_ma < slow_ma:
                signal = Signal.SELL

        self._prev_fast = fast_ma
        self._prev_slow = slow_ma
        return signal`,
          },
        ],
      },
      {
        id: 'rsi-mean-reversion',
        title: 'RSI Mean Reversion',
        content: `
          <p>The RSI mean-reversion strategy buys when the market is oversold and sells when overbought. It assumes that extreme RSI readings revert to the mean — the opposite assumption from trend-following.</p>
          <h4>How it works</h4>
          <p>The Relative Strength Index (RSI) measures the magnitude of recent gains versus losses on a 0-100 scale. When RSI drops below the oversold threshold (default 30), the strategy enters long, expecting a bounce. When RSI rises above the overbought threshold (default 70), it exits or goes short.</p>
          <h4>Signal generation</h4>
          <p>Crucially, the strategy detects <em>threshold crossings</em>, not just levels. It triggers a BUY only when RSI crosses below the oversold level (was above, now below), and a SELL only when it crosses above overbought. This prevents repeated signals during extended oversold/overbought periods.</p>
          <h4>DeFi considerations</h4>
          <p>Mean-reversion works particularly well on SOL-PERP during funding rate extremes. When funding is heavily positive (market is overextended long), RSI overbought signals tend to be more reliable because the funding cost creates natural selling pressure.</p>
        `,
        codeBlocks: [
          {
            language: 'python',
            code: `"""RSI Mean Reversion — Flint built-in strategy."""
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class RSIStrategy(Strategy):
    def __init__(self, period: int = 14, oversold: float = 30,
                 overbought: float = 70) -> None:
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self._prev_rsi: float = 50.0

    @property
    def name(self) -> str:
        return f"RSI({self.period}, {self.oversold}/{self.overbought})"

    def reset(self) -> None:
        self._prev_rsi = 50.0

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        if len(history) < self.period + 1:
            return Signal.HOLD

        closes = np.array([c.close for c in history[-(self.period + 1):]])
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = float(np.mean(gains))
        avg_loss = float(np.mean(losses))

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - 100 / (1 + rs)

        signal = Signal.HOLD
        if self._prev_rsi >= self.oversold and rsi < self.oversold:
            signal = Signal.BUY
        elif self._prev_rsi <= self.overbought and rsi > self.overbought:
            signal = Signal.SELL

        self._prev_rsi = rsi
        return signal`,
          },
        ],
      },
      {
        id: 'funding-rate-harvest',
        title: 'Funding Rate Harvest',
        content: `
          <p>Funding rate harvesting is a delta-neutral strategy that collects funding payments without taking directional risk. It's one of the highest Sharpe strategies in DeFi perps — conceptually similar to a cash-and-carry trade in traditional futures markets.</p>
          <h4>The core idea</h4>
          <ol>
            <li>When funding is positive (longs pay shorts), <strong>go short perp + buy spot</strong>. You collect funding while your net exposure is zero.</li>
            <li>When funding is negative (shorts pay longs), <strong>go long perp + sell spot</strong>. Same idea, opposite direction.</li>
            <li>Close both legs when funding normalises or flips direction.</li>
          </ol>
          <h4>Why Solana makes this better</h4>
          <p>On a CEX, you'd need accounts on two platforms (spot + perp) and manage cross-exchange risk. On Solana, both legs can be executed atomically in a single transaction, eliminating leg risk entirely.</p>
          <h4>Implementation sketch</h4>
          <p>The strategy below is simplified — it only manages the perp leg. A production version would also manage the spot hedge. The signal logic watches for funding rate extremes (above 2 standard deviations from the trailing mean) as entry triggers.</p>
        `,
        codeBlocks: [
          {
            language: 'python',
            code: `"""Funding Rate Harvest — conceptual implementation."""
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class FundingHarvestStrategy(Strategy):
    """
    Simplified funding rate harvester.
    Goes short when funding is abnormally positive (collect funding),
    goes long when funding is abnormally negative.

    In production, pair with a spot hedge for delta neutrality.
    """

    def __init__(self, lookback: int = 48, z_threshold: float = 2.0) -> None:
        self.lookback = lookback
        self.z_threshold = z_threshold
        self._funding_history: list[float] = []

    @property
    def name(self) -> str:
        return f"FundingHarvest(lb={self.lookback}, z={self.z_threshold})"

    def reset(self) -> None:
        self._funding_history = []

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        # Approximate funding from price deviation
        # (real version would use FundingRate data directly)
        if len(history) < 2:
            return Signal.HOLD

        # Use price momentum as a proxy for funding pressure
        ret = (candle.close - history[-1].close) / history[-1].close
        self._funding_history.append(ret)

        if len(self._funding_history) < self.lookback:
            return Signal.HOLD

        recent = np.array(self._funding_history[-self.lookback:])
        mean = float(np.mean(recent))
        std = float(np.std(recent))
        if std == 0:
            return Signal.HOLD

        z_score = (recent[-1] - mean) / std

        if z_score > self.z_threshold:
            return Signal.SELL   # Funding likely positive → short to collect
        elif z_score < -self.z_threshold:
            return Signal.BUY    # Funding likely negative → long to collect
        return Signal.HOLD`,
          },
        ],
      },
      {
        id: 'amm-arbitrage',
        title: 'AMM Arbitrage',
        content: `
          <p>AMM arbitrage exploits price discrepancies between different liquidity pools on Solana DEXes. When the same asset is priced differently on Raydium and Orca, a profit can be extracted by buying cheap and selling expensive — atomically in a single transaction.</p>
          <h4>How it works</h4>
          <ol>
            <li>Monitor reserves across multiple AMM pools for the same token pair</li>
            <li>Compute the implied price on each pool using the constant-product formula</li>
            <li>When the price difference exceeds transaction costs (fees + rent), execute a swap on the cheap pool and a reverse swap on the expensive pool</li>
            <li>Pocket the difference minus fees</li>
          </ol>
          <h4>Solana advantages</h4>
          <ul>
            <li><strong>Atomic execution:</strong> Both swaps execute in one transaction. If either fails, neither executes. No leg risk.</li>
            <li><strong>Low fees:</strong> ~$0.0003 per transaction means even tiny price discrepancies can be profitable.</li>
            <li><strong>No mempool:</strong> Transactions go directly to the leader validator, reducing the risk of being front-run by other arbitrageurs.</li>
          </ul>
          <h4>Using Flint's models</h4>
          <p>The <code>ArbRoute</code> model captures profitable routes with input/output amounts and profit in basis points. The <code>PoolState</code> model provides the reserve data needed to compute prices and simulate swap outputs.</p>
        `,
        codeBlocks: [
          {
            language: 'python',
            code: `"""AMM Arbitrage detection using Flint models."""
from flint.models import PoolState, ArbRoute


def detect_arb(pool_a: PoolState, pool_b: PoolState,
               input_amount: float) -> ArbRoute | None:
    """
    Check for arbitrage between two pools for the same pair.
    Returns an ArbRoute if profitable, None otherwise.
    """
    # Price on each pool (including fees)
    price_a = pool_a.price_a_in_b * (1 - pool_a.fee_rate)
    price_b = pool_b.price_a_in_b * (1 - pool_b.fee_rate)

    # Buy on cheaper pool, sell on expensive pool
    if price_a < price_b:
        buy_pool, sell_pool = pool_a, pool_b
    else:
        buy_pool, sell_pool = pool_b, pool_a
        price_a, price_b = price_b, price_a

    # Simulate: spend USDC on cheap pool → get SOL → sell on expensive pool
    tokens_received = input_amount / price_a
    output_amount = tokens_received * price_b
    profit = output_amount - input_amount
    profit_bps = (profit / input_amount) * 10_000

    # Only profitable if > 1 bps (to cover tx costs)
    if profit_bps < 1.0:
        return None

    return ArbRoute(
        pools=(buy_pool.pool_address, sell_pool.pool_address),
        tokens=("USDC", "SOL", "USDC"),
        input_amount=input_amount,
        output_amount=output_amount,
        profit=profit,
        profit_bps=profit_bps,
    )`,
          },
        ],
      },
    ],
  },
]
