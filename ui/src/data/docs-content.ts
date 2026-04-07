export interface DocCodeBlock {
  language: string
  code: string
}

export interface DocTopic {
  id: string
  title: string
  content: string
  codeBlocks?: DocCodeBlock[]
}

export interface DocSection {
  id: string
  title: string
  topics: DocTopic[]
}

export const docsContent: DocSection[] = [
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // TUTORIAL (first section — guides newcomers)
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {
    id: 'tutorial',
    title: 'TUTORIAL',
    topics: [
      {
        id: 'tutorial-overview',
        title: 'Your First Hour with Flint',
        content: `
          <p>This tutorial takes you from zero to running your own optimized strategy in about 30 minutes. No API keys needed. No Solana wallet needed. Everything is free.</p>
          <h4>What You'll Do</h4>
          <ol>
            <li><strong>Install Flint</strong> (2 min) — one pip install, Docker, or curl one-liner</li>
            <li><strong>Run a sample backtest</strong> (3 min) — see results immediately</li>
            <li><strong>Understand the results</strong> (5 min) — PnL, Sharpe, drawdown, Monte Carlo</li>
            <li><strong>Write your own strategy</strong> (10 min) — from template to custom logic</li>
            <li><strong>Optimize parameters</strong> (5 min) — find the best settings automatically</li>
            <li><strong>Try the web UI</strong> (5 min) — visual Strategy Lab with charts</li>
          </ol>
          <h4>What You'll Need</h4>
          <ul>
            <li>Python 3.10+ and pip (or Docker)</li>
            <li>A terminal</li>
            <li>That's it. No API keys, no wallet, no database setup.</li>
          </ul>
          <h4>20+ Strategies, Multiple Venues</h4>
          <p>Flint ships with 20+ built-in strategy templates spanning trend-following, mean reversion, funding arbitrage, basis trading, and MEV monitoring. The onboarding wizard lets you pick your primary venue (Drift or Hyperliquid) and download matching data in one step.</p>
        `,
      },
      {
        id: 'tutorial-step1',
        title: 'Step 1: Install & Initialize',
        content: `
          <p>Pick any install method below. The <code>flint init</code> command does everything after install: creates config, downloads 90 days of SOL-PERP data from Drift, and runs a sample backtest to prove it works.</p>
        `,
        codeBlocks: [
          { language: 'bash', code: `# Option A: one-line install (recommended — handles Python, Node, UI build)
curl -fsSL https://raw.githubusercontent.com/sohan-shingade/flint/main/install.sh | bash

# Option B: pip install from PyPI
pip install flint-trading   # installs as "flint" CLI command; import name is "flint"
flint init

# Option C: Docker
git clone https://github.com/sohan-shingade/flint.git && cd flint
docker compose up   # open localhost:8000, wizard handles the rest

# Option D: from source
git clone <repo-url>
cd flint
pip install -e .
flint init` },
          { language: 'text', code: `Output:
╭─────────────────────────────────╮
│ Flint Init                      │
│ Setting up your trading...      │
╰─────────────────────────────────╯
  ✓ Created flint.yaml
  ✓ Data directory ready
  ✓ Strategies directory ready
  Backfilling 90 days of SOL-PERP data...
  ━━━━━━━━━━━━━━━━━━━━ 100%
  ✓ Stored 2,160 candles for SOL-PERP
  Running sample backtest (MA Crossover 10/30)...

  Backtest Results — MA-Crossover(10/30)
  ┌──────────────────┬────────────┐
  │ Total PnL        │   $+892.41 │
  │ Sharpe Ratio     │       0.78 │
  │ Win Rate         │      42.3% │
  └──────────────────┴────────────┘
  Flint is ready!` },
        ],
      },
      {
        id: 'tutorial-step2',
        title: 'Step 2: Run a Backtest from CLI',
        content: `
          <p>Let's backtest one of the built-in strategies on a full year of data. Flint will automatically download the data if it's not cached locally.</p>
        `,
        codeBlocks: [
          { language: 'bash', code: `# Create a strategy file from a template
flint new my_first --v2

# Edit it (or use as-is for now)
# Then backtest it:
flint backtest strategies/user/my_first.py \\
  --market SOL-PERP \\
  --start 2024-06-01 \\
  --end 2024-12-31 \\
  --capital 10000` },
          { language: 'text', code: `What happens:
1. Checks local DB → no data for Jun-Dec 2024
2. Downloads from Drift Data API → 4,380 candles in ~2 seconds
3. Caches locally → next run is instant
4. Runs your strategy → prints results table + equity sparkline` },
        ],
      },
      {
        id: 'tutorial-step3',
        title: 'Step 3: Understanding Results',
        content: `
          <p>Every backtest produces a <strong>tearsheet</strong> with these key metrics:</p>
          <table>
            <tr><td><strong>Total PnL</strong></td><td>Net profit/loss after fees. Green = good.</td></tr>
            <tr><td><strong>Sharpe Ratio</strong></td><td>Risk-adjusted return. > 1.0 is decent, > 2.0 is great, < 0 means you're losing.</td></tr>
            <tr><td><strong>Max Drawdown</strong></td><td>Worst peak-to-trough loss. < 15% is conservative, > 30% is aggressive.</td></tr>
            <tr><td><strong>Win Rate</strong></td><td>% of profitable trades. 40-60% is normal for trend-following.</td></tr>
            <tr><td><strong>Profit Factor</strong></td><td>Gross wins / gross losses. > 1.5 is good.</td></tr>
          </table>
          <h4>Monte Carlo Confidence (automatic)</h4>
          <p>If your strategy has 5+ trades, Flint automatically shuffles the trade sequence 500 times to answer: <em>"Is this result skill or luck?"</em></p>
          <ul>
            <li><strong>Sharpe p-value</strong> — probability the Sharpe is actually ≤ 0. Want < 0.05.</li>
            <li><strong>Sharpe CI</strong> — 90% confidence interval. If it crosses zero, the edge might not be real.</li>
            <li><strong>Probability of ruin</strong> — % of simulations with > 50% drawdown. Want < 5%.</li>
          </ul>
        `,
      },
      {
        id: 'tutorial-step4',
        title: 'Step 4: Write Your Own Strategy',
        content: `
          <p>Open <code>strategies/user/my_first.py</code> and replace the logic. Here's a simple mean-reversion strategy you can start with:</p>
          <p><strong>The idea:</strong> When SOL drops 2% below its 24-hour average, buy. When it rises 2% above, sell. Simple, but it works in ranging markets.</p>
        `,
        codeBlocks: [
          { language: 'python', code: `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List

class DipBuyer(Strategy):
    def __init__(self, lookback=24, dip_pct=2.0):
        self.lookback = lookback
        self.dip = dip_pct / 100

    @property
    def name(self):
        return f"DipBuyer({self.lookback}, {self.dip*100:.0f}%)"

    @classmethod
    def parameters(cls):
        return {
            "lookback": {"type": "int", "low": 12, "high": 72, "default": 24},
            "dip_pct": {"type": "float", "low": 1.0, "high": 5.0, "default": 2.0},
        }

    def on_candle(self, candle, history, ctx=None):
        if len(history) < self.lookback:
            return Signal.HOLD
        avg = sum(c.close for c in history[-self.lookback:]) / self.lookback
        if candle.close < avg * (1 - self.dip):
            return Signal.BUY
        elif candle.close > avg * (1 + self.dip):
            return Signal.SELL
        return Signal.HOLD

    def reset(self):
        pass` },
          { language: 'bash', code: `# Test it
flint backtest strategies/user/my_first.py -m SOL-PERP -p 6m

# Try different markets
flint backtest strategies/user/my_first.py -m ETH-PERP -p 6m
flint backtest strategies/user/my_first.py -m BTC-PERP -p 6m` },
        ],
      },
      {
        id: 'tutorial-step5',
        title: 'Step 5: Optimize Parameters',
        content: `
          <p>Your strategy has <code>lookback</code> and <code>dip_pct</code> parameters. Instead of guessing, let Optuna find the best values:</p>
        `,
        codeBlocks: [
          { language: 'bash', code: `flint optimize strategies/user/my_first.py \\
  --market SOL-PERP \\
  --period 6m \\
  --metric sharpe \\
  --trials 50` },
          { language: 'text', code: `Output:
  Strategy:   DipBuyer
  Parameters: lookback, dip_pct
  Metric:     sharpe_ratio
  Trials:     50
  Candles:    4,380

  Best sharpe_ratio: 1.8432

  Best Parameters
  ┌────────────┬───────┐
  │ lookback   │    18 │
  │ dip_pct    │   1.8 │
  └────────────┴───────┘

  Top 5 Trials (of 50)
  ┌───┬────────┬──────────┬────────┬──────────────────────┐
  │ # │ sharpe │      PnL │ Trades │ Params               │
  │ 1 │ 1.8432 │ +$3,210  │     42 │ lookback=18, dip=1.8 │
  │ 2 │ 1.6891 │ +$2,890  │     38 │ lookback=24, dip=2.0 │
  └───┴────────┴──────────┴────────┴──────────────────────┘` },
        ],
      },
      {
        id: 'tutorial-step6',
        title: 'Step 6: Use the Web UI',
        content: `
          <p>The Strategy Lab gives you a visual editor with live backtesting:</p>
        `,
        codeBlocks: [
          { language: 'bash', code: `flint serve
# Open http://localhost:8000 in your browser` },
          { language: 'text', code: `What you'll see:
1. Strategy Lab — Monaco code editor + config panel
   - Pick a template from the dropdown (20+ to choose from)
   - Select market, timeframe, dates, and venue
   - Green indicator shows data availability
   - Click RUN_BACKTEST — progress bar shows download + execution
   - Results: equity curve, drawdown, trade markers, PnL histogram

2. Data Explorer — browse OHLCV data in your local DB
3. Docs — this documentation
4. MEV Dashboard — arb + liquidation scanning
5. LIVE — monitor and control live/paper trading sessions
6. FILLS — replay and inspect individual trade fills
7. FUNDING — cross-venue funding rate comparison charts

Keyboard shortcuts:
  1-7     Navigate between pages
  Ctrl+S  Save strategy
  Ctrl+Enter  Run backtest` },
        ],
      },
      {
        id: 'tutorial-next',
        title: 'What Next?',
        content: `
          <h4>Explore More Strategies</h4>
          <p>In the Strategy Lab, try each of the 20+ templates. The <strong>Advanced</strong> category shows v2 features: cross-market access, stop-loss/take-profit orders, and cross-venue funding strategies.</p>

          <h4>Try Different Markets</h4>
          <p>Flint supports 48 Drift perp markets. Popular ones: SOL-PERP, BTC-PERP, ETH-PERP, WIF-PERP, DOGE-PERP. Each market has different volatility and character.</p>

          <h4>Paper Trade Your Best Strategy</h4>
          <p>Once you find a profitable strategy, paper trade it:</p>

          <h4>Read the Strategy API Docs</h4>
          <p>The v2 ExecutionContext API unlocks: limit orders, stop-losses, take-profits, DCA (dollar cost averaging), multi-position, and cross-market signals. See the <strong>Strategy API</strong> section.</p>

          <h4>Run Walk-Forward Analysis</h4>
          <p>Before trusting any backtest, validate with walk-forward analysis. This tests if your strategy works on unseen data, not just the data you optimized on.</p>

          <h4>Join the Community</h4>
          <p>Flint is open source. Star the repo, report bugs, contribute strategies.</p>
        `,
        codeBlocks: [
          { language: 'bash', code: `# Paper trade
flint live strategies/user/my_first.py --market SOL-PERP --paper

# Walk-forward validation (Python)
from flint.optimization.walk_forward import run_walk_forward
from flint.strategy.loader import load_user_strategy

# Validate your strategy isn't overfit
result = run_walk_forward(MyStrategy, candles, n_windows=5)
print(f"Out-of-sample Sharpe: {result.avg_oos_sharpe:.2f}")
print(f"Overfitting ratio: {result.overfitting_ratio:.2f}")  # > 2x = suspicious` },
        ],
      },
    ],
  },

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // GETTING STARTED
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {
    id: 'getting-started',
    title: 'GETTING STARTED',
    topics: [
      {
        id: 'installation',
        title: 'Installation',
        content: `
          <p>Flint runs on macOS, Linux, and WSL. No external database or API keys needed. Choose the method that fits your workflow.</p>
          <h4>One-Line Install (recommended)</h4>
          <p>Installs Python, Node, clones the repo, builds the UI, and opens your browser. A setup wizard walks you through venue selection (Drift or Hyperliquid), market selection, and data download.</p>
          <pre><code>curl -fsSL https://raw.githubusercontent.com/sohan-shingade/flint/main/install.sh | bash</code></pre>
          <h4>Docker</h4>
          <p>Clone the repo and run <code>docker compose up</code>. Data persists in a named Docker volume (<code>flint-data</code>). The container includes a healthcheck on <code>/api/v1/health</code>. Open localhost:8000 and the setup wizard handles the rest. Pass API keys via a <code>.env</code> file — the compose file loads it automatically.</p>
          <h4>From Source (developers)</h4>
          <p>Clone, pip install, init, serve. Or use the Makefile: <code>make install</code>, <code>make dev</code>, <code>make test</code>. Run the UI separately in dev mode with <code>cd ui && npm run dev</code> (hot-reload on port 5173).</p>
        `,
        codeBlocks: [
          { language: 'bash', code: `# Option 1: One-line install (handles everything)
curl -fsSL https://raw.githubusercontent.com/sohan-shingade/flint/main/install.sh | bash

# Option 2: pip install from PyPI
pip install flint-trading   # installs as "flint" CLI; import name is "flint"
flint init          # creates config, backfills data, runs sample backtest
flint serve         # starts the API + UI at localhost:8000

# Option 3: Docker
git clone https://github.com/sohan-shingade/flint.git && cd flint
echo "FLINT_BIRDEYE_API_KEY=your-key" > .env   # optional
docker compose up   # open localhost:8000

# Option 4: From source
git clone https://github.com/sohan-shingade/flint.git && cd flint
pip install -e .
flint init          # creates config, backfills data, runs sample backtest
flint serve         # starts the API + UI at localhost:8000

# Option 5: Makefile shortcuts (developers)
make install   # pip install -e . + ui npm install
make dev       # flint serve --dev + npm run dev in parallel
make test      # pytest tests/ -v` },
        ],
      },
      {
        id: 'quickstart',
        title: 'First Backtest in 5 Minutes',
        content: `
          <p>After installation, you can run your first backtest from the terminal or the web UI.</p>
          <h4>From the CLI</h4>
        `,
        codeBlocks: [
          { language: 'bash', code: `# Scaffold a strategy file
flint new my_alpha

# Backtest it on SOL-PERP for the last 90 days
flint backtest strategies/user/my_alpha.py --market SOL-PERP --period 90d

# Optimize parameters
flint optimize strategies/user/my_alpha.py --metric sharpe --trials 50

# Or use the web UI
flint serve   # then open http://localhost:8000` },
          { language: 'text', code: `CLI Output:
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Metric           ┃      Value ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ Total PnL        │ $+6,977.38 │
│ Total Trades     │        166 │
│ Win Rate         │      45.2% │
│ Sharpe Ratio     │       1.16 │
└──────────────────┴────────────┘
  Equity: ▁▁▁▂▃▂▃▄▅▅▄▄▅▆▇▇▆▅▅▄▅` },
        ],
      },
      {
        id: 'project-structure',
        title: 'Project Structure',
        content: `
          <p>Flint is organized into focused modules:</p>
          <table>
            <tr><td>flint/strategy/</td><td>Strategy ABC + 20+ built-in strategies + dynamic loader</td></tr>
            <tr><td>flint/backtest/</td><td>Event-driven backtest engine with v2 ExecutionContext</td></tr>
            <tr><td>flint/execution/</td><td>Fill models, fee models, BacktestContext, PaperBroker, LiveDriftContext, MultiVenueLiveContext</td></tr>
            <tr><td>flint/execution/tx_costs.py</td><td>Transaction cost model (Jito tip, priority fee, slippage)</td></tr>
            <tr><td>flint/execution/vamm.py</td><td>vAMM curve math for realistic Drift fill simulation</td></tr>
            <tr><td>flint/mev/clmm.py</td><td>Concentrated liquidity math for Orca/Raydium fill simulation</td></tr>
            <tr><td>flint/backtest/calibration.py</td><td>Auto-calibrate fill models against real trade history</td></tr>
            <tr><td>flint/risk/</td><td>Risk guards: MaxDrawdown, MaxPositions, DailyLossLimit, EquityMonitor, PerMarketPositionLimit, MaxOrdersPerMinute</td></tr>
            <tr><td>flint/optimization/</td><td>Optuna hyperparameter search + walk-forward analysis</td></tr>
            <tr><td>flint/analytics/</td><td>Metrics, tearsheet, Monte Carlo simulation</td></tr>
            <tr><td>flint/providers/</td><td>Drift Data API, Drift S3, Hyperliquid candles, GeckoTerminal, Jupiter (15 providers)</td></tr>
            <tr><td>flint/providers/hyperliquid_candles.py</td><td>Hyperliquid OHLCV candle provider</td></tr>
            <tr><td>flint/providers/hyperliquid_ws.py</td><td>Hyperliquid WebSocket connector for live data</td></tr>
            <tr><td>flint/execution/multi_venue_live.py</td><td>Multi-venue live execution coordinator</td></tr>
            <tr><td>flint/execution/hyperliquid_live.py</td><td>Live order execution on Hyperliquid (EIP-712 signing)</td></tr>
            <tr><td>flint/collector/</td><td>Automated data collection (oracle, funding, orderbook)</td></tr>
            <tr><td>flint/portfolio/</td><td>Multi-strategy portfolio engine + allocators</td></tr>
            <tr><td>flint/paper/</td><td>Paper trading engine</td></tr>
            <tr><td>flint/notifications/</td><td>Telegram, Discord, webhook alerts</td></tr>
            <tr><td>flint/store.py</td><td>Thread-safe DuckDB store (candles, funding, orderbook, tick_snapshots, live trading tables, etc.)</td></tr>
            <tr><td>flint/config.py</td><td>Pydantic Settings (YAML + env vars)</td></tr>
            <tr><td>flint/cli.py</td><td>Typer CLI: init, backtest, optimize, serve, data, live, calibrate, parity</td></tr>
          </table>
        `,
      },
    ],
  },

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // CLI REFERENCE
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {
    id: 'cli',
    title: 'CLI REFERENCE',
    topics: [
      {
        id: 'cli-overview',
        title: 'Command Overview',
        content: `
          <p>The <code>flint</code> CLI is installed via <code>pip install flint-trading</code> (or <code>pip install -e .</code> from source) and registered as a console script.</p>
          <table>
            <tr><td><strong>flint init</strong></td><td>Scaffold project, create flint.yaml, backfill data, run sample backtest</td></tr>
            <tr><td><strong>flint backtest</strong></td><td>Run a strategy on historical data with rich terminal output + ASCII equity sparkline</td></tr>
            <tr><td><strong>flint optimize</strong></td><td>Hyperparameter search via Optuna with parameter table output</td></tr>
            <tr><td><strong>flint serve</strong></td><td>Start the FastAPI server (API + collector)</td></tr>
            <tr><td><strong>flint new</strong></td><td>Scaffold a new strategy file (v1 or v2 template)</td></tr>
            <tr><td><strong>flint live</strong></td><td>Paper trading or live execution on Drift Protocol</td></tr>
            <tr><td><strong>flint data download</strong></td><td>Download candle data from Drift for a specific market</td></tr>
            <tr><td><strong>flint data status</strong></td><td>Show data coverage table for all markets in local DB</td></tr>
          </table>
          <p>If the API server is running, CLI commands automatically route through the API to avoid DuckDB lock conflicts.</p>
        `,
      },
      {
        id: 'cli-init',
        title: 'flint init',
        content: `
          <p>One-command project setup. Creates config, downloads data, verifies everything works.</p>
          <table>
            <tr><td>--days N</td><td>Days of data to backfill (default: 90)</td></tr>
            <tr><td>--market MARKET</td><td>Primary market to backfill (default: SOL-PERP)</td></tr>
            <tr><td>--skip-demo</td><td>Skip running the demo backtest</td></tr>
          </table>
          <h4>What it does:</h4>
          <ol>
            <li>Creates <code>flint.yaml</code> config file (if not exists)</li>
            <li>Creates <code>data/</code> and <code>strategies/user/</code> directories</li>
            <li>Checks for existing data — skips download if &ge;80% coverage already exists</li>
            <li>Downloads candle data from Drift Data API (with S3 fallback)</li>
            <li>Falls back to 2024 data if current data unavailable</li>
            <li>Runs a sample MA Crossover backtest to verify everything works (unless <code>--skip-demo</code>)</li>
          </ol>
        `,
        codeBlocks: [
          { language: 'bash', code: `flint init
flint init --days 365 --market BTC-PERP
flint init --skip-demo   # skip the sample backtest` },
        ],
      },
      {
        id: 'cli-backtest',
        title: 'flint backtest',
        content: `
          <p>Run a strategy file against historical data. Prints a metrics table and ASCII equity sparkline.</p>
          <table>
            <tr><td><strong>STRATEGY_FILE</strong></td><td>Path to your .py strategy file (required)</td></tr>
            <tr><td>-m, --market</td><td>Market symbol (default: SOL-PERP)</td></tr>
            <tr><td>-r, --resolution</td><td>Candle resolution in seconds (default: 3600 = 1h)</td></tr>
            <tr><td>-s, --start</td><td>Start date YYYY-MM-DD</td></tr>
            <tr><td>-e, --end</td><td>End date YYYY-MM-DD</td></tr>
            <tr><td>-p, --period</td><td>Period shorthand: 30d, 90d, 6m, 1y</td></tr>
            <tr><td>-c, --capital</td><td>Initial capital (default: 10000)</td></tr>
            <tr><td>--fee</td><td>Fee rate, e.g. 0.001 for 10bps (default: 0.0005)</td></tr>
          </table>
          <p>Data is auto-downloaded from Drift Data API if not cached locally. Second run for the same range is instant.</p>
        `,
        codeBlocks: [
          { language: 'bash', code: `# Basic usage
flint backtest strategies/user/my_strat.py

# Full options
flint backtest strategies/user/my_strat.py \\
  --market ETH-PERP \\
  --start 2024-06-01 \\
  --end 2024-12-31 \\
  --capital 50000 \\
  --fee 0.001

# Using period shorthand
flint backtest my_strat.py -m SOL-PERP -p 6m
flint backtest my_strat.py -m BTC-PERP -p 1y` },
        ],
      },
      {
        id: 'cli-optimize',
        title: 'flint optimize',
        content: `
          <p>Find optimal strategy parameters using Optuna bayesian search. Requires <code>parameters()</code> classmethod on your strategy.</p>
          <table>
            <tr><td><strong>STRATEGY_FILE</strong></td><td>Path to strategy .py file (required)</td></tr>
            <tr><td>-m, --market</td><td>Market (default: SOL-PERP)</td></tr>
            <tr><td>--metric</td><td>Metric to optimize: sharpe_ratio, total_pnl, calmar, win_rate (default: sharpe_ratio)</td></tr>
            <tr><td>-n, --trials</td><td>Number of optimization trials (default: 50)</td></tr>
            <tr><td>-p, --period</td><td>Data period (default: 90d)</td></tr>
            <tr><td>-c, --capital</td><td>Initial capital (default: 10000)</td></tr>
          </table>
          <p>Output: best parameters table + top 5 trials ranked by metric.</p>
        `,
        codeBlocks: [
          { language: 'bash', code: `# Optimize for Sharpe ratio
flint optimize strategies/user/my_strat.py --trials 100

# Optimize for total PnL with 1 year of data
flint optimize my_strat.py --metric total_pnl --period 1y

# Optimize on a specific market
flint optimize my_strat.py -m ETH-PERP --metric calmar --trials 200` },
        ],
      },
      {
        id: 'cli-data',
        title: 'flint data',
        content: `
          <p>Manage local data and data providers.</p>

          <h4>flint data download</h4>
          <table>
            <tr><td>-m, --market</td><td>Market to download (default: SOL-PERP)</td></tr>
            <tr><td>-d, --days</td><td>Days of history (default: 365)</td></tr>
            <tr><td>-r, --resolution</td><td>Candle resolution in seconds (default: 3600)</td></tr>
            <tr><td>--end-date</td><td>End date YYYY-MM-DD (default: today)</td></tr>
          </table>

          <h4>flint data status</h4>
          <p>Shows markets in the local database with candle counts and date ranges.</p>

          <h4>flint data provider status</h4>
          <p>Shows all data providers with enabled/available status, API key requirements, and supported data types.</p>

          <h4>flint data provider enable &lt;name&gt;</h4>
          <table>
            <tr><td>name</td><td>Provider name: birdeye, helius, pyth, raydium, orca, ccxt</td></tr>
            <tr><td>--api-key</td><td>API key (saved to .env automatically)</td></tr>
          </table>

          <h4>flint data provider disable &lt;name&gt;</h4>
          <p>Disables a provider in flint.yaml.</p>

          <h4>flint data exchanges</h4>
          <p>Lists all CCXT-supported exchanges (requires <code>pip install flint[ccxt]</code>).</p>

          <h4>flint data markets &lt;exchange&gt;</h4>
          <table>
            <tr><td>exchange</td><td>Exchange name: binance, bybit, okx, coinbase, kraken, etc.</td></tr>
            <tr><td>--type</td><td>Market type: swap (perps), spot, future (default: swap)</td></tr>
            <tr><td>--quote</td><td>Quote currency filter (default: USDT)</td></tr>
          </table>
        `,
        codeBlocks: [
          { language: 'bash', code: `# Download data
flint data download --market SOL-PERP --days 365
flint data status

# Manage providers
flint data provider status
flint data provider enable birdeye --api-key YOUR_KEY
flint data provider enable helius --api-key YOUR_KEY
flint data provider enable pyth
flint data provider disable binance

# CCXT exchanges (pip install flint[ccxt])
flint data exchanges
flint data markets binance
flint data markets bybit --type spot --quote USDC` },
        ],
      },
      {
        id: 'cli-other',
        title: 'flint serve / new / live',
        content: `
          <h4>flint serve</h4>
          <table>
            <tr><td>--host</td><td>Bind address (default: 127.0.0.1)</td></tr>
            <tr><td>--port</td><td>Port number (default: 8000)</td></tr>
          </table>
          <p>Starts the FastAPI server with the built UI at localhost:8000. For dev mode, use <code>flint serve --dev</code> (API only) and run the UI separately with <code>cd ui && npm run dev</code> (port 5173).</p>

          <h4>flint new [NAME]</h4>
          <table>
            <tr><td>NAME</td><td>Strategy name (default: my_strategy)</td></tr>
            <tr><td>--v2</td><td>Generate v2 template with ExecutionContext (stop-loss, limit orders)</td></tr>
          </table>
          <p>Creates <code>strategies/user/NAME.py</code> with a working strategy template.</p>

          <h4>flint live STRATEGY_FILE</h4>
          <table>
            <tr><td>-m, --market</td><td>Market to trade (default: SOL-PERP)</td></tr>
            <tr><td>--venue</td><td>Venue: drift (default) or hyperliquid</td></tr>
            <tr><td>--paper / --real</td><td>Paper trading (default) or real money</td></tr>
            <tr><td>-c, --capital</td><td>Starting capital (default: 10000)</td></tr>
            <tr><td>--key</td><td>Base58 private key for Drift, or hex key for Hyperliquid (or set env var)</td></tr>
            <tr><td>--rpc</td><td>Solana RPC URL (Drift only, or set FLINT_RPC_URL)</td></tr>
          </table>
          <p>Drift live trading requires <code>pip install driftpy</code> (Python 3.10+) and a funded Solana wallet. Hyperliquid live trading requires <code>FLINT_HYPERLIQUID_PRIVATE_KEY</code> (EIP-712 signing via the Hyperliquid API wallet).</p>

          <h4>flint calibrate</h4>
          <p>Calibrate fill models against real trade history from a venue. Adjusts slippage and spread parameters in <code>flint.yaml</code> to match observed fills.</p>
          <table>
            <tr><td>--venue</td><td>drift or hyperliquid</td></tr>
            <tr><td>--market</td><td>Market to calibrate (e.g. SOL-PERP)</td></tr>
            <tr><td>--days</td><td>Days of history to use (default: 30)</td></tr>
          </table>

          <h4>flint parity</h4>
          <p>Run a parity check comparing backtest fills against real fills for the same strategy and time window. Reports fill price error, slippage accuracy, and timing drift. Useful for validating model quality before going live.</p>
        `,
        codeBlocks: [
          { language: 'bash', code: `# Start API server
flint serve --port 8000

# Create strategies
flint new my_alpha           # v1 signal-based
flint new my_alpha_v2 --v2   # v2 with stop-loss, limit orders

# Paper trade
flint live my_strat.py -m SOL-PERP --paper
flint live my_strat.py -m SOL-PERP --venue hyperliquid --paper

# Live trade (REAL MONEY)
export FLINT_PRIVATE_KEY="your_base58_key"
flint live my_strat.py -m SOL-PERP --real

# Hyperliquid live (REAL MONEY)
export FLINT_HYPERLIQUID_PRIVATE_KEY="0xabc..."
flint live my_strat.py -m SOL-PERP --venue hyperliquid --real

# Calibrate fill model against real trade history
flint calibrate --venue drift --market SOL-PERP
flint calibrate --venue hyperliquid --market SOL-PERP --days 14

# Parity check — compare backtest fills to real fills
flint parity my_strat.py --market SOL-PERP --venue drift` },
        ],
      },
    ],
  },

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // STRATEGY API
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {
    id: 'strategies',
    title: 'STRATEGY API',
    topics: [
      {
        id: 'strategy-basics',
        title: 'Writing a Strategy (v1)',
        content: `
          <p>Every strategy subclasses <code>Strategy</code> and implements three methods:</p>
          <ul>
            <li><code>name</code> — property returning the strategy name</li>
            <li><code>on_candle(candle, history, ctx=None)</code> — called each bar, returns Signal.BUY / SELL / HOLD</li>
            <li><code>reset()</code> — clear internal state for a fresh run</li>
          </ul>
          <p>v1 strategies return <strong>Signal</strong> values. The engine automatically converts BUY → open position, SELL → close position. Simple and clean.</p>
        `,
        codeBlocks: [
          { language: 'python', code: `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np

class MyStrategy(Strategy):
    def __init__(self, fast=10, slow=30):
        self.fast = fast
        self.slow = slow
        self._prev_fast = None
        self._prev_slow = None

    @property
    def name(self):
        return f"MyStrat({self.fast}/{self.slow})"

    @classmethod
    def parameters(cls):
        """Define optimizable parameters for Optuna."""
        return {
            "fast": {"type": "int", "low": 5, "high": 50, "default": 10},
            "slow": {"type": "int", "low": 20, "high": 200, "default": 30},
        }

    def on_candle(self, candle, history, ctx=None):
        if len(history) < self.slow:
            return Signal.HOLD
        closes = [c.close for c in history[-self.slow:]]
        fast_ma = np.mean(closes[-self.fast:])
        slow_ma = np.mean(closes)
        signal = Signal.HOLD
        if self._prev_fast is not None:
            if self._prev_fast <= self._prev_slow and fast_ma > slow_ma:
                signal = Signal.BUY
            elif self._prev_fast >= self._prev_slow and fast_ma < slow_ma:
                signal = Signal.SELL
        self._prev_fast = fast_ma
        self._prev_slow = slow_ma
        return signal

    def reset(self):
        self._prev_fast = None
        self._prev_slow = None` },
        ],
      },
      {
        id: 'strategy-v2',
        title: 'v2 Context API (Advanced)',
        content: `
          <p>The v2 API gives strategies direct control over order management via the <code>ExecutionContext</code>. Instead of returning signals, you call methods on <code>ctx</code>:</p>
          <table>
            <tr><td>ctx.market_order(market, side, size, venue="drift")</td><td>Submit a market order on the specified venue</td></tr>
            <tr><td>ctx.limit_order(market, side, size, price, venue="drift")</td><td>Submit a limit order</td></tr>
            <tr><td>ctx.stop_order(market, side, size, trigger, venue="drift")</td><td>Attach a stop-loss</td></tr>
            <tr><td>ctx.take_profit_order(market, side, size, trigger, venue="drift")</td><td>Attach a take-profit</td></tr>
            <tr><td>ctx.close_position(market, venue="drift")</td><td>Close entire position on a venue</td></tr>
            <tr><td>ctx.cancel(order_id)</td><td>Cancel a pending order</td></tr>
            <tr><td>ctx.cancel_all(market)</td><td>Cancel all pending orders</td></tr>
            <tr><td>ctx.account</td><td>AccountState: equity, cash, unrealized PnL</td></tr>
            <tr><td>ctx.positions</td><td>List of open PositionInfo (keyed by (venue, market))</td></tr>
            <tr><td>ctx.pending_orders</td><td>List of unfilled Orders</td></tr>
            <tr><td>ctx.get_candles(market, lookback)</td><td>Cross-market data access</td></tr>
            <tr><td>ctx.get_funding_by_venue(market)</td><td>Dict of {venue: funding_rate} for current bar</td></tr>
            <tr><td>ctx.estimate_cost(market, size, venue="drift")</td><td>Estimate total transaction cost (fee + slippage + Jito tip) before placing order</td></tr>
            <tr><td>ctx.markets</td><td>List of available markets</td></tr>
          </table>
          <p>The <code>venue=</code> parameter defaults to <code>"drift"</code>. Pass <code>venue="hyperliquid"</code> to route orders to Hyperliquid. The same strategy code runs identically in backtest, paper trading, and live trading — Flint swaps the context implementation behind the scenes.</p>
        `,
        codeBlocks: [
          { language: 'python', code: `from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side
from typing import List

class StopLossStrategy(Strategy):
    @property
    def name(self): return "WithStops"

    def on_candle(self, candle, history, ctx=None):
        if ctx is None or len(history) < 20:
            return Signal.HOLD

        # Entry: momentum breakout
        if not ctx.positions:
            old = history[-20].close
            if (candle.close - old) / old > 0.03:  # 3% up
                size = (ctx.account.cash * 0.9) / candle.close
                ctx.market_order(candle.market, Side.LONG, size)
                # Automatic risk management
                ctx.stop_order(candle.market, Side.SHORT, size,
                               candle.close * 0.98)   # 2% stop
                ctx.take_profit_order(candle.market, Side.SHORT, size,
                                      candle.close * 1.06)  # 6% TP
        return Signal.HOLD

    def reset(self): pass` },
        ],
      },
      {
        id: 'cross-market',
        title: 'Cross-Market & Cross-Venue Strategies',
        content: `
          <p>Strategies can access data for other markets using <code>ctx.get_candles()</code>. This enables pairs trading, correlation strategies, and cross-market signals.</p>
          <p>Pass multiple markets to the engine via <code>engine.run({"SOL-PERP": sol_candles, "BTC-PERP": btc_candles})</code> or the engine auto-loads from the DB.</p>

          <h4>Cross-Venue Execution</h4>
          <p>With multi-venue support, a single strategy can simultaneously hold positions on Drift and Hyperliquid. Use <code>venue=</code> on order methods and <code>ctx.get_funding_by_venue()</code> to build funding arbitrage or basis strategies. In live mode, use <code>MultiVenueLiveContext</code> which manages WebSocket connections and capital allocation across venues.</p>
          <h4>Venue Capital Allocation</h4>
          <p>Configure per-venue capital in the backtest request with <code>capital_allocation: {"drift": 5000, "hyperliquid": 3000}</code>. The <code>VenueAllocator</code> manages transfers between venues with configurable delay and cost models.</p>
        `,
        codeBlocks: [
          { language: 'python', code: `def on_candle(self, candle, history, ctx=None):
    if ctx is None:
        return Signal.HOLD

    # Peek at BTC while trading SOL
    btc = ctx.get_candles("BTC-PERP", 12)
    if len(btc) >= 2:
        btc_return = (btc[-1].close - btc[0].close) / btc[0].close
        if btc_return > 0.02:   # BTC up 2% → go long SOL
            return Signal.BUY
        elif btc_return < -0.02:
            return Signal.SELL
    return Signal.HOLD` },
          { language: 'python', code: `# Cross-venue funding arb: long on low-rate venue, short on high-rate venue
def on_candle(self, candle, history, ctx=None):
    if ctx is None:
        return Signal.HOLD

    rates = ctx.get_funding_by_venue(candle.market)
    # rates = {"drift": 0.0001, "hyperliquid": -0.0003, "binance": 0.00015, ...}

    venues = sorted(rates, key=lambda v: rates[v])
    low_venue, high_venue = venues[0], venues[-1]
    spread = rates[high_venue] - rates[low_venue]

    if spread > 0.0002 and not ctx.positions:   # 2bps spread threshold
        size = ctx.account.cash * 0.4 / candle.close
        ctx.market_order(candle.market, Side.LONG,  size, venue=low_venue)
        ctx.market_order(candle.market, Side.SHORT, size, venue=high_venue)
    return Signal.HOLD` },
        ],
      },
      {
        id: 'built-in-strategies',
        title: 'Built-in Strategies (20+)',
        content: `
          <p>Flint ships with 20+ strategy templates across 6 categories. All are available in the Strategy Lab UI and via API.</p>
          <h4>Trend Following</h4>
          <ul>
            <li><strong>MA Crossover</strong> — SMA golden/death cross (fast=10, slow=30)</li>
            <li><strong>EMA Crossover</strong> — EMA crossover, more responsive to recent price (fast=12, slow=26)</li>
            <li><strong>Breakout Momentum</strong> — N-period high breakout + volume spike confirmation</li>
            <li><strong>MomentumBreakout</strong> — Combines ADX trend strength with a price-channel breakout. Only trades when ADX &gt; threshold, reducing whipsaws.</li>
            <li><strong>ATR Breakout</strong> — Price breaks SMA ± N×ATR channel. Adapts to volatility.</li>
            <li><strong>Dual Timeframe</strong> — Long SMA trend filter + short momentum entry</li>
            <li><strong>Momentum</strong> — Rate-of-change: buy when up X% over lookback</li>
          </ul>
          <h4>Mean Reversion</h4>
          <ul>
            <li><strong>RSI</strong> — Buy oversold (RSI &lt; 30), sell overbought (RSI &gt; 70)</li>
            <li><strong>Bollinger Bands</strong> — Buy lower band, sell upper band</li>
            <li><strong>Z-Score Reversion</strong> — Statistical deviation from rolling mean with stop-loss</li>
            <li><strong>VWAP Reversion</strong> — Buy X% below VWAP, sell at reversion. Great for range-bound markets.</li>
            <li><strong>FundingMeanReversion</strong> — Fades extreme funding rates: when funding is highly positive, go short; when highly negative, go long. Captures the mean-reversion in rates plus the funding payment itself.</li>
          </ul>
          <h4>Multi-Signal</h4>
          <ul>
            <li><strong>MACD Divergence</strong> — Classic MACD/signal line crossover</li>
            <li><strong>RSI + MACD Combo</strong> — Only trades when both RSI and MACD agree. Fewer but higher-quality signals.</li>
          </ul>
          <h4>Solana / DeFi Native</h4>
          <ul>
            <li><strong>Funding Harvest</strong> — Trade against funding rate direction (get paid to hold)</li>
            <li><strong>Multi-Venue Funding</strong> — Cross-venue funding arbitrage using 10 venues. Unique to Flint.</li>
            <li><strong>FundingArb</strong> — Opens long on the lowest-rate venue and short on the highest-rate venue simultaneously using <code>venue=</code> parameter. Requires multi-venue capital allocation.</li>
            <li><strong>BasisTrade</strong> — Exploits the spread between a perp's mark price and the spot oracle. Goes long spot / short perp when the basis is positive, capturing funding and convergence.</li>
            <li><strong>Grid Trader</strong> — Limit order grid for ranging markets (v2 context)</li>
          </ul>
          <h4>MEV / Research</h4>
          <ul>
            <li><strong>MevArbMonitor</strong> — Monitors AMM pools and Drift vAMM for profitable arbitrage routes. Does not execute — produces signals for research and sizing calculations. Integrates with MEV Dashboard.</li>
          </ul>
          <p>Plus a <strong>Blank</strong> template in the Strategy Lab to start from scratch.</p>
        `,
      },
    ],
  },

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // BACKTEST ENGINE
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {
    id: 'backtesting',
    title: 'BACKTEST ENGINE',
    topics: [
      {
        id: 'engine-overview',
        title: 'How It Works',
        content: `
          <p>The engine is <strong>event-driven</strong>: it iterates candles chronologically, calling your strategy's <code>on_candle()</code> at each step.</p>
          <h4>Execution Flow (per candle)</h4>
          <ol>
            <li>Set current candle on context</li>
            <li>Process pending stop-loss/take-profit/limit orders against this candle's high/low</li>
            <li>Apply funding rate payment (if available for this timestamp)</li>
            <li>Call <code>strategy.on_candle(candle, history, ctx)</code></li>
            <li>Convert v1 Signal returns to orders (BUY → market_order, SELL → close_position)</li>
            <li>Process market orders placed during this bar</li>
            <li>Record equity</li>
          </ol>
          <p>After all candles: force-close open positions, compute metrics, generate tearsheet, run Monte Carlo.</p>
        `,
      },
      {
        id: 'fill-models',
        title: 'Fill Models',
        content: `
          <p>Fill models determine <strong>at what price</strong> orders execute. Pluggable via <code>BacktestEngine(fill_model=...)</code>. Flint offers a 4-tier fill model hierarchy from simple to highest-fidelity:</p>
          <p>Legacy standalone models include <code>ClosePriceFill</code>, <code>NextBarOpenFill</code>, and <code>SlippageFill</code>. The <code>FillPipeline</code> applies tiers in priority order:</p>
          <table>
            <tr><td><strong>Tier 0 — vAMM</strong></td><td><code>VammCurveFill</code> — simulates Drift's virtual AMM curve using <code>VammCurve</code> math. Accounts for market depth and quote asset reserve ratios. Used when vAMM configs are provided.</td></tr>
            <tr><td><strong>Tier 1 — Orderbook walk</strong></td><td><code>OrderbookFillModel</code> — walks the L2 order book for volume-weighted fill prices. Requires stored orderbook snapshots.</td></tr>
            <tr><td><strong>Tier 2 — Sqrt participation</strong></td><td>Square-root participation model — estimates price impact from bar volume. Used when volume data exists but no orderbook.</td></tr>
            <tr><td><strong>Tier 3 — Flat bps fallback</strong></td><td><code>ClosePriceFill</code> / <code>SlippageFill(bps=N)</code> — fill at close ± fixed slippage. Last resort when no other data is available.</td></tr>
          </table>
          <p>The <code>tx_cost_model</code> parameter adds Jito tip + Solana priority fee to every fill, regardless of which fill tier is used. Calibrate fill model parameters against real trade history with <code>flint calibrate</code>.</p>
          <h4>How limit orders fill</h4>
          <p>Limit buy fills when <code>candle.low &lt;= limit_price</code>. Limit sell fills when <code>candle.high &gt;= limit_price</code>. Fills at the limit price (no slippage on limits).</p>
          <h4>How stops trigger</h4>
          <p>Stop-loss checks <code>candle.low</code> (for long stops) or <code>candle.high</code> (for short stops) against the trigger price. Fills at the trigger price.</p>
        `,
      },
      {
        id: 'fee-models',
        title: 'Fee Models',
        content: `
          <p>Fee models compute trading costs. Applied to every fill.</p>
          <table>
            <tr><td>FlatFeeModel(fee_bps=5)</td><td>Flat basis points on notional (default: 5bps = 0.05%)</td></tr>
            <tr><td>DriftFeeModel(maker=-2bps, taker=10bps)</td><td>Drift Protocol tiered fees. Makers get rebates.</td></tr>
            <tr><td>ZeroFeeModel()</td><td>No fees — for testing only</td></tr>
          </table>
          <p>In the UI, select from presets: Drift Taker (10bps), Drift Maker (-2bps), Flat 5bps, Flat 1bps, Zero.</p>
          <h4>Drift Fee Tiers (v2 model)</h4>
          <table>
            <tr><td>&lt; $1M 30d volume</td><td>Taker: 10bps, Maker: -2bps</td></tr>
            <tr><td>$1M–$5M</td><td>Taker: 8bps, Maker: -2bps</td></tr>
            <tr><td>$5M–$25M</td><td>Taker: 6bps, Maker: -3bps</td></tr>
            <tr><td>&gt; $25M</td><td>Taker: 4bps, Maker: -4bps</td></tr>
          </table>
        `,
      },
      {
        id: 'risk-management',
        title: 'Risk Management',
        content: `
          <p>Risk guards run between strategy signal and order execution. Any guard can reject an order.</p>
          <table>
            <tr><td>MaxPositionSize(max_notional)</td><td>Reject orders exceeding notional limit</td></tr>
            <tr><td>MaxOpenPositions(n)</td><td>Limit concurrent open positions</td></tr>
            <tr><td>MaxDrawdownCircuitBreaker(pct, capital)</td><td>Stop all trading if portfolio drawdown exceeds threshold</td></tr>
            <tr><td>DailyLossLimit(max_loss, capital)</td><td>Stop trading for the day after N loss</td></tr>
            <tr><td>EquityMonitor(floor_pct)</td><td>Kill switch: halt all activity and close all positions if equity falls below floor. Sends alert via configured notification channel.</td></tr>
            <tr><td>PerMarketPositionLimit(market, max_notional)</td><td>Per-market notional cap — useful in multi-venue strategies where aggregate exposure can exceed intent.</td></tr>
            <tr><td>MaxOrdersPerMinute(n)</td><td>Rate-limiter: rejects orders above N per minute. Guards against runaway loops in live trading.</td></tr>
          </table>
        `,
        codeBlocks: [
          { language: 'python', code: `from flint.risk import RiskManager, MaxDrawdownCircuitBreaker, MaxOpenPositions
from flint.backtest.engine import BacktestEngine

rm = RiskManager([
    MaxDrawdownCircuitBreaker(max_drawdown_pct=0.15, initial_capital=10000),
    MaxOpenPositions(max_positions=3),
])

engine = BacktestEngine(strategy, risk_manager=rm)
result = engine.run(candles)` },
        ],
      },
      {
        id: 'monte-carlo',
        title: 'Monte Carlo & Statistical Confidence',
        content: `
          <p>Every backtest with 5+ trades automatically runs a <strong>Monte Carlo simulation</strong> (500 iterations). This shuffles the trade sequence to measure luck vs. skill.</p>
          <h4>What You Get</h4>
          <table>
            <tr><td>Sharpe CI</td><td>90% confidence interval on Sharpe ratio</td></tr>
            <tr><td>Sharpe p-value</td><td>Probability that Sharpe &le; 0 (is it real?)</td></tr>
            <tr><td>Max Drawdown CI</td><td>5th–95th percentile of worst-case drawdown</td></tr>
            <tr><td>PnL CI</td><td>Confidence interval on total profit</td></tr>
            <tr><td>Probability of Ruin</td><td>% of simulations with &gt;50% drawdown</td></tr>
            <tr><td>Equity bands</td><td>p5/p25/p50/p75/p95 equity curves for charting</td></tr>
          </table>
          <p>Results appear in the tearsheet JSON under <code>monte_carlo</code>.</p>
        `,
      },
    ],
  },

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // DATA
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {
    id: 'data',
    title: 'DATA & STORAGE',
    topics: [
      {
        id: 'data-sources',
        title: 'Data Sources',
        content: `
          <p>Flint aggregates data from multiple sources into a local DuckDB database. All core data is free — no API keys needed for backtesting.</p>

          <h4>Price Data — Free, No API Keys</h4>
          <table>
            <tr><td><strong>Drift Data API</strong></td><td>OHLCV candles (1m→monthly), orderbook L2/L3. 48 perp + 17 spot markets. Primary source.</td></tr>
            <tr><td><strong>Drift S3</strong></td><td>Historical trade records. Archival backfill. Auto-fallback.</td></tr>
            <tr><td><strong>Hyperliquid Candles</strong></td><td>OHLCV candles for all Hyperliquid perp markets via <code>HyperliquidCandleProvider</code>. ~1 year history. Stored in DuckDB with a <code>venue</code> column alongside Drift candles, enabling direct price comparison.</td></tr>
            <tr><td><strong>CoinGecko</strong></td><td>BTC/ETH spot hourly OHLCV (fills gaps where Drift has no spot candles).</td></tr>
            <tr><td><strong>Drift Open Interest</strong></td><td>Long/short OI per market. Crowding detection.</td></tr>
            <tr><td><strong>Pyth Network</strong></td><td>Real-time oracle feeds for 20 pairs. Sub-second updates.</td></tr>
            <tr><td><strong>Raydium</strong></td><td>AMM/CLMM pool data, reserves, fees, TVL, volume.</td></tr>
            <tr><td><strong>Orca</strong></td><td>Whirlpool concentrated liquidity pool stats + tick array data for CLMM fill simulation.</td></tr>
          </table>

          <h4>Funding Rate Venues — 10 Venues, All Free</h4>
          <p>When you download a perp market, Flint fetches funding rates from up to <strong>10 venues</strong> simultaneously. You choose which venues to include before downloading. All data is forward-filled to hourly resolution.</p>
          <table>
            <tr><td><strong>Drift</strong></td><td>1h native, ~30 day history. Solana-native perp exchange.</td></tr>
            <tr><td><strong>Hyperliquid</strong></td><td>1h native, ~1 year history. Best long-term coverage.</td></tr>
            <tr><td><strong>dYdX v4</strong></td><td>1h native. Decentralized perp exchange.</td></tr>
            <tr><td><strong>OKX</strong></td><td>8h → 1h filled. Major CEX, no geo-block.</td></tr>
            <tr><td><strong>Bybit</strong></td><td>8h → 1h filled. Geo-blocked in US.</td></tr>
            <tr><td><strong>Gate.io</strong></td><td>8h → 1h filled. No geo-block.</td></tr>
            <tr><td><strong>Bitget</strong></td><td>8h → 1h filled. No geo-block.</td></tr>
            <tr><td><strong>MEXC</strong> (CCXT)</td><td>8h → 1h filled. Via CCXT library.</td></tr>
            <tr><td><strong>Phemex</strong> (CCXT)</td><td>8h → 1h filled. Via CCXT library.</td></tr>
            <tr><td><strong>BitMEX</strong> (CCXT)</td><td>8h → 1h filled. Via CCXT library.</td></tr>
          </table>

          <h4>Optional API Keys (free signup, no credit card)</h4>
          <table>
            <tr><td><strong>Birdeye</strong></td><td>OHLCV for <strong>any</strong> Solana token. Sign up at birdeye.so/developers.</td></tr>
            <tr><td><strong>Helius</strong></td><td>Liquidation detection, whale tracking. Sign up at helius.dev.</td></tr>
          </table>

          <h4>CCXT — 100+ Exchanges</h4>
          <p>Install <code>pip install ccxt</code> to unlock MEXC, Phemex, BitMEX funding venues. CCXT also supports candle data from 100+ exchanges (Binance, Kraken, Coinbase, etc.).</p>

          <h4>How Data Loading Works</h4>
          <ol>
            <li>Select markets in Data Explorer → ADD MARKETS panel</li>
            <li>Choose time range and funding venues</li>
            <li>Click DOWNLOAD — fetches candles from Drift + funding from selected venues</li>
            <li>All data cached in local DuckDB — instant on next load</li>
            <li>8h funding rates are forward-filled to hourly for uniform resolution</li>
          </ol>
          <h4>Available Markets (48 perp + 17 spot)</h4>
          <p>SOL-PERP, BTC-PERP, ETH-PERP, and 45 more perp markets. Spot: SOL, BTC, ETH, JUP, DRIFT, PYTH, WIF, JTO, BONK, POPCAT, and more. With Birdeye enabled, backtest <strong>any</strong> Solana token.</p>
        `,
      },
      {
        id: 'duckdb-store',
        title: 'DuckDB Store',
        content: `
          <p>All data is stored in a single <code>data/flint.duckdb</code> file. Thread-safe (internal locking). No server needed.</p>
          <h4>Tables</h4>
          <table>
            <tr><td>candles</td><td>OHLCV data. PK: (market, resolution_s, ts). Includes a <code>venue</code> column to store Drift and Hyperliquid candles side-by-side.</td></tr>
            <tr><td>venue_funding_rates</td><td>Funding rates from 10 venues, hourly. PK: (venue, market, ts)</td></tr>
            <tr><td>oracle_prices</td><td>Oracle price snapshots. PK: (market, ts)</td></tr>
            <tr><td>orderbook_snapshots</td><td>L2 bid/ask arrays. PK: (market, ts)</td></tr>
            <tr><td>tick_snapshots</td><td>Orca/Raydium CLMM tick array snapshots for concentrated liquidity fill simulation. PK: (pool_address, ts)</td></tr>
            <tr><td>pool_snapshots</td><td>AMM pool reserves. PK: (pool_address, ts)</td></tr>
            <tr><td>open_interest</td><td>Long/short OI from Drift. PK: (market, ts)</td></tr>
            <tr><td>liquidations</td><td>Liquidation events from Helius. PK: (market, ts, tx_sig)</td></tr>
            <tr><td>whale_transfers</td><td>Large token movements. PK: (token_mint, ts, tx_sig)</td></tr>
            <tr><td>dex_volume</td><td>DEX volume per market. PK: (market, dex, ts)</td></tr>
            <tr><td>sync_metadata</td><td>Freshness tracking per provider. PK: (provider, market, data_type)</td></tr>
            <tr><td>backtest_runs</td><td>Saved backtest results (journal)</td></tr>
            <tr><td>journal_trades</td><td>Per-trade detail for saved runs</td></tr>
            <tr><td>live_sessions</td><td>Active and historical live/paper trading sessions</td></tr>
            <tr><td>live_fills</td><td>Real fills from live trading sessions, normalized across venues</td></tr>
          </table>
          <h4>Parquet Export</h4>
        `,
        codeBlocks: [
          { language: 'python', code: `from flint.data.export import export_table_parquet, import_parquet
from flint.store import FlintStore

store = FlintStore("./data/flint.duckdb")
export_table_parquet(store, "candles", "backup/candles.parquet")
import_parquet(store, "backup/candles.parquet", "candles")` },
        ],
      },
      {
        id: 'data-quality',
        title: 'Data Quality Checks',
        content: `
          <p>Every backtest automatically runs quality checks before execution:</p>
          <ul>
            <li><strong>Gap detection</strong> — finds missing candle periods</li>
            <li><strong>Outlier detection</strong> — flags price moves &gt; 5 standard deviations</li>
            <li><strong>Duplicate detection</strong> — finds repeated timestamps</li>
            <li><strong>Completeness %</strong> — actual vs. expected candle count</li>
          </ul>
          <p>Warnings appear in the tearsheet under <code>data_quality</code> and in the progress bar.</p>
        `,
      },
      {
        id: 'data-providers',
        title: 'Configuring Providers',
        content: `
          <p>All data providers are configurable via <code>flint.yaml</code>, <code>.env</code>, or CLI. Enable only what you need.</p>

          <h4>Quick Setup</h4>
          <table>
            <tr><td><strong>Birdeye</strong> (any Solana token)</td><td><code>flint data provider enable birdeye --api-key YOUR_KEY</code></td></tr>
            <tr><td><strong>Helius</strong> (liquidations, whales)</td><td><code>flint data provider enable helius --api-key YOUR_KEY</code></td></tr>
            <tr><td><strong>Pyth</strong> (real-time oracles)</td><td><code>flint data provider enable pyth</code></td></tr>
            <tr><td><strong>Raydium</strong> (AMM pool data)</td><td><code>flint data provider enable raydium</code></td></tr>
            <tr><td><strong>Orca</strong> (Whirlpool data)</td><td><code>flint data provider enable orca</code></td></tr>
            <tr><td><strong>CCXT</strong> (100+ exchanges)</td><td><code>pip install flint[ccxt]</code> then <code>flint data provider enable ccxt</code></td></tr>
          </table>

          <h4>Where API Keys Go</h4>
          <p>API keys are stored in <code>.env</code> (never in flint.yaml). The CLI <code>--api-key</code> flag saves them automatically.</p>
          <table>
            <tr><td>FLINT_BIRDEYE_API_KEY</td><td>Free at birdeye.so/developers (no credit card)</td></tr>
            <tr><td>FLINT_HELIUS_API_KEY</td><td>Free at helius.dev (100k credits/day, no credit card)</td></tr>
            <tr><td>FLINT_CCXT_API_KEY</td><td>Optional — only for private exchange data (balances, orders)</td></tr>
            <tr><td>FLINT_CCXT_SECRET</td><td>Optional — exchange API secret</td></tr>
          </table>

          <h4>CCXT — Any Exchange</h4>
          <p>With CCXT installed, pull candles, funding rates, orderbooks, and tickers from Binance, Bybit, OKX, Coinbase, Kraken, and 100+ more. Symbol mapping between Flint format (<code>SOL-PERP</code>) and exchange format (<code>SOL/USDT:USDT</code>) is automatic.</p>
        `,
        codeBlocks: [
          { language: 'yaml', code: `# flint.yaml — provider configuration
providers:
  drift:
    enabled: true
  birdeye:
    enabled: true       # needs FLINT_BIRDEYE_API_KEY in .env
  helius:
    enabled: true       # needs FLINT_HELIUS_API_KEY in .env
  pyth:
    enabled: true       # no key needed
  raydium:
    enabled: true       # no key needed
  orca:
    enabled: false
  ccxt:
    enabled: true
    exchange: binance    # any CCXT-supported exchange
  funding:
    drift: true
    hyperliquid: true
    okx: true
    binance: false       # geo-blocked from US (funding data works without API key)
    bybit: false` },
          { language: 'python', code: `# Use providers directly in scripts
from flint.providers import BirdeyeProvider, CCXTProvider, PythProvider

# Birdeye — any Solana token
birdeye = BirdeyeProvider(api_key="YOUR_KEY")
candles = birdeye.fetch_candles("So111...112", 3600, start_ts, end_ts)
meta = birdeye.fetch_token_metadata("So111...112")

# CCXT — any exchange
provider = CCXTProvider(exchange="bybit")
candles = provider.fetch_candles("SOL/USDT", 3600, start_ts, end_ts)
markets = provider.list_markets(quote="USDT")

# Pyth — real-time oracle
pyth = PythProvider()
price = pyth.fetch_price("SOL/USD")  # {price: 150.25, confidence: 0.05}
prices = pyth.fetch_prices(["SOL/USD", "BTC/USD", "ETH/USD"])` },
        ],
      },
    ],
  },

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // OPTIMIZATION
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {
    id: 'optimization',
    title: 'OPTIMIZATION',
    topics: [
      {
        id: 'hyperopt',
        title: 'Hyperparameter Optimization',
        content: `
          <p>Flint uses <strong>Optuna</strong> (Bayesian TPE sampler) to find optimal strategy parameters.</p>
          <h4>How It Works</h4>
          <ol>
            <li>Define <code>parameters()</code> classmethod on your strategy</li>
            <li>Run <code>flint optimize strategy.py --trials 100</code></li>
            <li>Optuna samples parameter combinations intelligently</li>
            <li>Each trial runs a full backtest</li>
            <li>Best parameters printed with metrics table</li>
          </ol>
          <h4>Supported Metrics</h4>
          <table>
            <tr><td>sharpe_ratio</td><td>Risk-adjusted return (default)</td></tr>
            <tr><td>total_pnl</td><td>Raw profit</td></tr>
            <tr><td>win_rate</td><td>Percentage of winning trades</td></tr>
            <tr><td>max_drawdown</td><td>Minimize worst peak-to-trough (negated)</td></tr>
            <tr><td>calmar</td><td>Return / max drawdown ratio</td></tr>
          </table>
        `,
        codeBlocks: [
          { language: 'python', code: `# In your strategy class:
@classmethod
def parameters(cls):
    return {
        "fast_period": {"type": "int", "low": 5, "high": 50},
        "slow_period": {"type": "int", "low": 20, "high": 200},
        "threshold": {"type": "float", "low": 0.01, "high": 0.10},
    }` },
        ],
      },
      {
        id: 'walk-forward',
        title: 'Walk-Forward Analysis',
        content: `
          <p>Walk-forward analysis splits data into rolling train/test windows to detect overfitting.</p>
          <h4>Output</h4>
          <ul>
            <li><strong>In-sample vs out-of-sample Sharpe</strong> per window</li>
            <li><strong>Overfitting ratio</strong> — IS/OOS Sharpe. &gt;2x is suspicious</li>
            <li><strong>Parameter stability</strong> — coefficient of variation per param across windows</li>
          </ul>
        `,
        codeBlocks: [
          { language: 'python', code: `from flint.optimization.walk_forward import run_walk_forward

result = run_walk_forward(
    MACrossoverStrategy, candles,
    n_windows=5, train_pct=0.7,
    metric="sharpe_ratio", trials_per_window=30,
)
print(f"OOS Sharpe: {result.avg_oos_sharpe:.2f}")
print(f"Overfitting ratio: {result.overfitting_ratio:.2f}")` },
        ],
      },
      {
        id: 'portfolio',
        title: 'Multi-Strategy Portfolio',
        content: `
          <p>Run multiple strategies simultaneously with capital allocation and correlation tracking.</p>
          <table>
            <tr><td>EqualWeightAllocator</td><td>Split capital equally</td></tr>
            <tr><td>InverseVolAllocator</td><td>More capital to lower-volatility strategies</td></tr>
          </table>
        `,
        codeBlocks: [
          { language: 'python', code: `from flint.portfolio import PortfolioEngine, InverseVolAllocator

engine = PortfolioEngine(
    strategies=[
        ("MA", MACrossoverStrategy()),
        ("RSI", RSIStrategy()),
        ("Momentum", MomentumStrategy()),
    ],
    initial_capital=30_000,
    allocator=InverseVolAllocator(),
)
result = engine.run(candles)
print(result.allocations)        # {"MA": 0.45, "RSI": 0.30, "Momentum": 0.25}
print(result.correlation_matrix) # pairwise return correlations` },
        ],
      },
    ],
  },

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // API REFERENCE
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {
    id: 'api',
    title: 'API REFERENCE',
    topics: [
      {
        id: 'api-backtest',
        title: 'Backtest Endpoints',
        content: `
          <p>Base URL: <code>http://localhost:8000/api/v1</code></p>
          <table>
            <tr><td><strong>POST /backtest/run</strong></td><td>Submit a backtest job</td></tr>
          </table>
          <p>Request body:</p>
          <ul>
            <li><code>strategy</code> (string) — strategy name or "custom" for inline code</li>
            <li><code>code</code> (string, optional) — Python strategy source code</li>
            <li><code>market</code> (string) — e.g. "SOL-PERP" (default: SOL-PERP)</li>
            <li><code>resolution_s</code> (int) — candle width in seconds (default: 3600)</li>
            <li><code>start_ts</code> (int) — start unix timestamp (required)</li>
            <li><code>end_ts</code> (int) — end unix timestamp (required)</li>
            <li><code>initial_capital</code> (float) — starting capital (default: 10000)</li>
            <li><code>fee_rate</code> (float) — fee rate (default: 0.0005)</li>
            <li><code>params</code> (dict, optional) — strategy constructor params</li>
          </ul>
          <p>Returns: <code>{"id": "abc123", "status": "running"}</code></p>
          <p>Data is auto-downloaded from Drift Data API if not cached locally.</p>

          <table>
            <tr><td><strong>GET /backtest/{id}/results</strong></td><td>Poll for results + progress</td></tr>
          </table>
          <p>Returns: <code>{"id", "status": "running|complete|failed", "progress": {"phase", "pct", "detail", "elapsed_s", "candles"}, "results": tearsheet_dict}</code></p>

          <table>
            <tr><td><strong>GET /backtest/{id}/status</strong></td><td>Quick status check</td></tr>
            <tr><td><strong>GET /backtest/compare?ids=a,b,c</strong></td><td>Compare multiple completed runs</td></tr>
          </table>
          <p>Results include: metrics, equity curve, drawdown, monthly returns, trades, data quality warnings, Monte Carlo CI (if 5+ trades).</p>
        `,
        codeBlocks: [
          { language: 'bash', code: `# Submit a backtest
curl -X POST http://localhost:8000/api/v1/backtest/run \\
  -H "Content-Type: application/json" \\
  -d '{"strategy":"ma_crossover","market":"SOL-PERP","start_ts":1704067200,"end_ts":1735689600}'

# Poll for results
curl http://localhost:8000/api/v1/backtest/abc123/results` },
        ],
      },
      {
        id: 'api-optimize',
        title: 'Optimization Endpoints',
        content: `
          <table>
            <tr><td><strong>POST /optimize/run</strong></td><td>Submit optimization job</td></tr>
          </table>
          <p>Request body:</p>
          <ul>
            <li><code>code</code> (string) — strategy source code (required)</li>
            <li><code>market</code>, <code>resolution_s</code>, <code>start_ts</code>, <code>end_ts</code>, <code>initial_capital</code>, <code>fee_rate</code> — same as backtest</li>
            <li><code>metric</code> (string) — "sharpe_ratio", "total_pnl", "calmar", "win_rate"</li>
            <li><code>trials</code> (int) — number of Optuna trials (default: 50)</li>
          </ul>
          <p>Returns: <code>{"id": "...", "status": "running"}</code></p>

          <table>
            <tr><td><strong>GET /optimize/{id}/results</strong></td><td>Poll for optimization results</td></tr>
          </table>
          <p>Returns: best_params, best_value, metric, n_trials, top 20 trials with params/metrics.</p>
        `,
      },
      {
        id: 'api-data',
        title: 'Data Endpoints',
        content: `
          <h4>Core Data</h4>
          <table>
            <tr><td><strong>GET /data/ohlcv</strong></td><td>Query OHLCV candles. Params: market, resolution_s, start_ts, end_ts, limit, venue (drift | hyperliquid)</td></tr>
            <tr><td><strong>GET /data/funding</strong></td><td>Query funding rates. Params: market, start_ts, end_ts, limit, venue (omit for all venues)</td></tr>
            <tr><td><strong>GET /data/markets</strong></td><td>List all markets with data coverage (candle count, date range, venues)</td></tr>
            <tr><td><strong>GET /data/check</strong></td><td>Check data availability. Returns: has_data, covers_range, will_download</td></tr>
            <tr><td><strong>GET /data/venues</strong></td><td>List venues with cross-venue funding data</td></tr>
            <tr><td><strong>POST /data/download</strong></td><td>Download market data. Body: market, days, resolution_s, venues[] (which funding venues to fetch), candle_venues[] (drift | hyperliquid)</td></tr>
          </table>

          <h4>Provider Management</h4>
          <table>
            <tr><td><strong>GET /data/providers</strong></td><td>List all data providers with enabled/available status and data types</td></tr>
            <tr><td><strong>GET /data/freshness</strong></td><td>Data freshness report — staleness per provider/market/data type</td></tr>
          </table>

          <h4>Data Types</h4>
          <table>
            <tr><td><strong>GET /data/open-interest/{market}</strong></td><td>Open interest (long/short OI). Params: start, end, limit</td></tr>
            <tr><td><strong>GET /data/liquidations/{market}</strong></td><td>Liquidation events. Params: start, end</td></tr>
            <tr><td><strong>GET /data/whale-transfers</strong></td><td>Large token movements. Params: token, wallet, start, end</td></tr>
            <tr><td><strong>GET /data/dex-volume/{market}</strong></td><td>DEX volume by venue. Params: dex, start, end</td></tr>
            <tr><td><strong>GET /data/correlation</strong></td><td>Cross-market correlation matrix. Params: markets (comma-separated), resolution</td></tr>
          </table>

          <h4>Live Trading</h4>
          <table>
            <tr><td><strong>POST /live/start</strong></td><td>Start a live trading session. Body: strategy, code, market, venue, capital, real (bool)</td></tr>
            <tr><td><strong>POST /live/stop</strong></td><td>Gracefully stop a live session. Body: session_id</td></tr>
            <tr><td><strong>POST /live/kill</strong></td><td>Emergency stop: close all positions immediately. Body: session_id</td></tr>
            <tr><td><strong>GET /live/status/{session_id}</strong></td><td>Current equity, positions, fills, P&amp;L, venue connection status</td></tr>
            <tr><td><strong>GET /live/sessions</strong></td><td>List all live/paper sessions with status and performance summary</td></tr>
            <tr><td><strong>GET /live/fills/{session_id}</strong></td><td>Fill history for a live session (normalized across venues)</td></tr>
          </table>
        `,
      },
      {
        id: 'api-strategies',
        title: 'Strategy Endpoints',
        content: `
          <table>
            <tr><td><strong>GET /strategies</strong></td><td>List all built-in strategies with parameter schemas</td></tr>
            <tr><td><strong>GET /strategies/{name}</strong></td><td>Get details for one strategy</td></tr>
          </table>
          <h4>User Strategies (CRUD)</h4>
          <table>
            <tr><td><strong>POST /user-strategies</strong></td><td>Save strategy code. Body: <code>{"name": "my_strat", "code": "..."}</code></td></tr>
            <tr><td><strong>GET /user-strategies</strong></td><td>List all saved user strategies</td></tr>
            <tr><td><strong>GET /user-strategies/{name}</strong></td><td>Load strategy code by name</td></tr>
            <tr><td><strong>DELETE /user-strategies/{name}</strong></td><td>Delete a saved strategy</td></tr>
            <tr><td><strong>POST /user-strategies/validate</strong></td><td>Validate Python code (AST check). Body: <code>{"code": "..."}</code></td></tr>
          </table>
        `,
      },
      {
        id: 'api-paper',
        title: 'Paper Trading Endpoints',
        content: `
          <table>
            <tr><td><strong>POST /paper/start</strong></td><td>Start a paper trading session</td></tr>
          </table>
          <p>Body: <code>strategy</code>, <code>code</code> (optional), <code>market</code>, <code>resolution_s</code>, <code>initial_capital</code>, <code>venue</code> ("drift" or "hyperliquid", default: "drift")</p>

          <table>
            <tr><td><strong>POST /paper/stop</strong></td><td>Stop a session. Body: <code>{"session_id": "..."}</code></td></tr>
            <tr><td><strong>POST /paper/kill</strong></td><td>Emergency: close all positions + stop. Body: <code>{"session_id": "..."}</code></td></tr>
            <tr><td><strong>GET /paper/status/{session_id}</strong></td><td>Current equity, positions, pending orders, PnL</td></tr>
            <tr><td><strong>GET /paper/sessions</strong></td><td>List all paper sessions with status</td></tr>
            <tr><td><strong>GET /paper/trades/{session_id}</strong></td><td>Trade history for a session</td></tr>
          </table>
        `,
      },
      {
        id: 'api-calibration',
        title: 'Calibration Endpoints',
        content: `
          <p>Calibration and parity endpoints live under the backtest router. They fit fill model parameters against real trade history to improve backtest accuracy.</p>
          <table>
            <tr><td><strong>POST /backtest/calibrate</strong></td><td>Run slippage calibration for a venue/market. Body: <code>venue</code>, <code>market</code>, <code>lookback_days</code> (default: 30). Returns fitted parameters and error metrics.</td></tr>
            <tr><td><strong>POST /backtest/parity</strong></td><td>Run a parity test comparing backtest fills against real fills. Body: <code>venue</code>, <code>market</code>, <code>strategy</code>, <code>code</code>. Reports fill price error, slippage accuracy, and timing drift.</td></tr>
          </table>
          <p>Calibrated parameters are stored in <code>flint.yaml</code> under <code>fill_models</code> and automatically used by the backtest engine for the matching venue.</p>
        `,
        codeBlocks: [
          { language: 'bash', code: `# Run calibration via API
curl -X POST http://localhost:8000/api/v1/backtest/calibrate \\
  -H "Content-Type: application/json" \\
  -d '{"venue": "drift", "market": "SOL-PERP", "lookback_days": 30}'

# Run parity test via API
curl -X POST http://localhost:8000/api/v1/backtest/parity \\
  -H "Content-Type: application/json" \\
  -d '{"venue": "drift", "market": "SOL-PERP"}'

# Or use the CLI
flint calibrate --venue drift --market SOL-PERP` },
        ],
      },
      {
        id: 'api-journal',
        title: 'Journal Endpoints',
        content: `
          <p>All backtests are auto-saved to the journal (persistent in DuckDB).</p>
          <table>
            <tr><td><strong>GET /journal/runs</strong></td><td>List all saved backtest runs (newest first). Param: <code>limit</code></td></tr>
            <tr><td><strong>GET /journal/runs/{run_id}</strong></td><td>Get full details for one run including all trades</td></tr>
            <tr><td><strong>DELETE /journal/runs/{run_id}</strong></td><td>Delete a saved run</td></tr>
            <tr><td><strong>GET /journal/compare?ids=a,b,c</strong></td><td>Compare metrics across multiple saved runs</td></tr>
          </table>
        `,
      },
      {
        id: 'api-other',
        title: 'Other Endpoints',
        content: `
          <h4>Health & Collector</h4>
          <table>
            <tr><td><strong>GET /health</strong></td><td>API health check. Returns <code>{"status": "ok"}</code></td></tr>
            <tr><td><strong>GET /collector/status</strong></td><td>Data collector status (oracle, funding, orderbook collection)</td></tr>
            <tr><td><strong>GET /collector/config</strong></td><td>Collector configuration (markets, intervals)</td></tr>
            <tr><td><strong>POST /collector/trigger</strong></td><td>Manually trigger data collection</td></tr>
          </table>

          <h4>MEV</h4>
          <table>
            <tr><td><strong>POST /mev/scan/arb</strong></td><td>Detect arbitrage routes across AMM pools. Body: pool data</td></tr>
            <tr><td><strong>POST /mev/scan/liquidations</strong></td><td>Scan positions for liquidation opportunities. Body: positions + oracle prices</td></tr>
          </table>

          <h4>WebSocket</h4>
          <table>
            <tr><td><strong>WS /ws/{channel}</strong></td><td>Real-time streaming. Channels: portfolio, trades, candles, alerts, all</td></tr>
          </table>
          <p>Subscribe by connecting to <code>ws://localhost:8000/ws/portfolio</code>. Messages are JSON with a <code>channel</code> field.</p>
        `,
      },
    ],
  },

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // CONFIGURATION
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {
    id: 'config',
    title: 'CONFIGURATION',
    topics: [
      {
        id: 'config-file',
        title: 'flint.yaml',
        content: `
          <p>Flint loads config from <code>flint.yaml</code> in the project root, overridden by environment variables with the <code>FLINT_</code> prefix.</p>
        `,
        codeBlocks: [
          { language: 'yaml', code: `db:
  path: ./data/flint.duckdb

trading:
  default_markets: [SOL-PERP, BTC-PERP, ETH-PERP]
  default_fee_rate: 0.0005
  default_capital: 10000.0
  primary_venue: drift          # drift | hyperliquid

collector:
  enabled: true
  candle_backfill_days: 90
  oracle_interval_s: 60
  funding_interval_s: 3600
  orderbook_interval_s: 300

risk:
  max_drawdown_pct: 0.20
  default_stop_loss_pct: 0.05
  max_open_trades: 5
  equity_floor_pct: 0.50        # EquityMonitor kill switch (50% of starting capital)
  max_orders_per_minute: 60     # MaxOrdersPerMinute rate limiter

# Hyperliquid live trading
# hyperliquid:
#   private_key: ""              # or set FLINT_HYPERLIQUID_PRIVATE_KEY
#   api_wallet: ""               # EIP-712 API wallet address
#   testnet: false               # use Hyperliquid testnet

# Multi-venue capital allocation defaults
# venues:
#   drift:
#     capital: 5000
#   hyperliquid:
#     capital: 3000
#     transfer_delay_s: 300      # simulated transfer delay for backtests
#     transfer_cost_bps: 5       # bridge cost in basis points

# Fill model calibration (auto-updated by flint calibrate)
# fill_models:
#   drift:
#     SOL-PERP:
#       slippage_bps: 3.2
#       spread_bps: 1.1
#   hyperliquid:
#     SOL-PERP:
#       slippage_bps: 2.8

# Transaction cost model
# tx_costs:
#   drift:
#     jito_tip_lamports: 10000   # Jito bundle tip
#     priority_fee_micro_lamports: 5000
#   hyperliquid:
#     gas_fee_usd: 0.002          # estimated per-tx gas cost

# vAMM curve parameters (Drift fill simulation)
# vamm:
#   base_asset_reserve: 1000000
#   quote_asset_reserve: 150000000
#   peg_multiplier: 1.0

# CLMM parameters (Orca/Raydium fill simulation)
# clmm:
#   tick_spacing: 64
#   fee_tier_bps: 4

# notifications:
#   telegram_bot_token: "your-token"
#   telegram_chat_id: "your-chat-id"
#   discord_webhook_url: ""

# Override any setting via env var:
# FLINT_DB_PATH=./custom.duckdb
# FLINT_DEFAULT_CAPITAL=50000
# FLINT_HYPERLIQUID_PRIVATE_KEY=0xabc...` },
        ],
      },
      {
        id: 'notifications',
        title: 'Notifications',
        content: `
          <p>Configure alerts for trade events, risk warnings, and system errors.</p>
          <table>
            <tr><td>Telegram</td><td>Set telegram_bot_token + telegram_chat_id in config</td></tr>
            <tr><td>Discord</td><td>Set discord_webhook_url</td></tr>
            <tr><td>Generic Webhook</td><td>Set webhook_url — receives JSON POST for all events</td></tr>
          </table>
          <h4>Event Types</h4>
          <p>fill, stop_triggered, drawdown_warning, strategy_error, collector_error, daily_summary</p>
        `,
      },
    ],
  },

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // MEV
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {
    id: 'mev',
    title: 'MEV FRAMEWORK',
    topics: [
      {
        id: 'mev-overview',
        title: 'MEV Detection & Analysis',
        content: `
          <p>Flint includes a full MEV research framework for Solana:</p>
          <table>
            <tr><td>ArbDetector</td><td>Builds AMM pool graph, DFS search for profitable circular routes. Constant-product math with per-pool fees.</td></tr>
            <tr><td>LiquidationScanner</td><td>Monitors positions vs oracle prices. Computes liquidation prices + estimated profit.</td></tr>
            <tr><td>JitDetector</td><td>Compares DLOB price levels vs vAMM. Finds JIT fill opportunities with maker rebate.</td></tr>
            <tr><td>BundleBuilder</td><td>Constructs Jito bundles. Validates instruction count, tx size, tip bounds.</td></tr>
            <tr><td>MevSimulator</td><td>Replays historical snapshots. Runs arb + liquidation at each timestamp.</td></tr>
          </table>
          <h4>MEV Cost Model</h4>
          <p>The <code>MevCostModel</code> estimates per-trade MEV costs: Jito tip, base tx fee, and front-running cost. Can be added to backtests for realistic Solana execution costs.</p>

          <h4>CLMM / Concentrated Liquidity</h4>
          <p>Flint models concentrated liquidity pools (Orca Whirlpools, Raydium CLMM) for accurate MEV research and fill simulation:</p>
          <table>
            <tr><td>CLMMPool</td><td>Represents a concentrated liquidity position. Computes exact output for a given swap input by walking through active tick ranges.</td></tr>
            <tr><td>OrcaTickFetcher</td><td>Fetches live tick arrays from the Orca Whirlpool program on-chain. Stores in <code>tick_snapshots</code> table for offline replay.</td></tr>
            <tr><td>ClmmArbDetector</td><td>Finds arb routes between CLMM pools and constant-product AMMs. Accounts for the non-uniform liquidity distribution in tick ranges.</td></tr>
          </table>
          <p>Use <code>ClmmFill</code> fill model to replay CLMM tick snapshots during backtesting for the highest-fidelity fill price simulation on Orca/Raydium pools.</p>
        `,
        codeBlocks: [
          { language: 'python', code: `from flint.mev.clmm import CLMMPool, OrcaTickFetcher

# Fetch and store tick arrays
fetcher = OrcaTickFetcher(rpc_url="https://api.mainnet-beta.solana.com")
ticks = fetcher.fetch_pool("8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj")
store.upsert_tick_snapshot(pool_address, ticks, ts=int(time.time()))

# Compute fill price through CLMM
pool = CLMMPool.from_snapshot(ticks)
output, price_impact = pool.compute_swap(amount_in=1000, a_to_b=True)
print(f"Fill price: {output/1000:.4f}, impact: {price_impact*100:.2f}%")` },
        ],
      },
    ],
  },

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // DEPLOYMENT
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {
    id: 'deployment',
    title: 'DEPLOYMENT',
    topics: [
      {
        id: 'docker',
        title: 'Docker',
        content: `
          <p>The Docker setup uses a multi-stage build (Python backend + Node frontend) with a smart entrypoint that auto-generates config on first run. Data persists in a named Docker volume.</p>
          <h4>Quick Start</h4>
          <p>Clone the repo and run <code>docker compose up</code>. Open localhost:8000 — the setup wizard handles market selection and data download.</p>
          <h4>Healthcheck</h4>
          <p>The container includes a healthcheck that pings <code>/api/v1/health</code> every 30 seconds. Docker will restart it automatically if the server goes down.</p>
          <h4>API Keys</h4>
          <p>Create a <code>.env</code> file in the project root with optional keys (Birdeye, Helius). The compose file loads it automatically.</p>
        `,
        codeBlocks: [
          { language: 'bash', code: `# Start with docker compose (recommended)
docker compose up

# Or build and run manually
docker build -t flint .
docker run -p 8000:8000 -v flint-data:/app/data flint

# Pass API keys via .env
echo "FLINT_BIRDEYE_API_KEY=your-key" > .env
docker compose up` },
        ],
      },
      {
        id: 'live-trading',
        title: 'Live Trading (Drift + Hyperliquid)',
        content: `
          <p>Flint supports live order execution on <strong>Drift Protocol</strong> (Solana) and <strong>Hyperliquid</strong> (EVM-compatible perp DEX). The same strategy code runs identically in backtest, paper, and live — only the context implementation changes.</p>

          <h4>Drift Setup</h4>
          <ul>
            <li><code>pip install driftpy</code> (Python 3.10+)</li>
            <li>Solana private key (base58 encoded) — set <code>FLINT_PRIVATE_KEY</code> or pass <code>--key</code></li>
            <li>Solana RPC URL — set <code>FLINT_RPC_URL</code> (default: public mainnet)</li>
            <li>SOL for transaction fees + margin (USDC)</li>
          </ul>
          <p>Drift live trading uses <code>LiveDriftContext</code> which subscribes to Drift's WebSocket feed for real-time mark prices, funding, and order fills.</p>

          <h4>Hyperliquid Setup</h4>
          <ul>
            <li>No extra pip install — Hyperliquid uses a REST + WebSocket API</li>
            <li>EVM private key (hex) — set <code>FLINT_HYPERLIQUID_PRIVATE_KEY</code></li>
            <li>API wallet address — set <code>FLINT_HYPERLIQUID_API_WALLET</code> (Hyperliquid's EIP-712 sub-account wallet)</li>
            <li>USDC deposited to Hyperliquid for margin</li>
          </ul>
          <p>Hyperliquid live trading uses <code>LiveHyperliquidContext</code> which connects via <code>hyperliquid_ws.py</code> for order updates and <code>HyperliquidCandleProvider</code> for bar data. EIP-712 signing is handled automatically.</p>

          <h4>Multi-Venue Live Trading</h4>
          <p>Run a single strategy across both venues simultaneously using <code>MultiVenueLiveContext</code>. Capital is split per <code>capital_allocation</code> config. Both venue connections are managed concurrently with independent heartbeat monitoring.</p>

          <h4>Network Defaults</h4>
          <p>The default network is <strong>devnet</strong> for Drift and <strong>testnet</strong> for Hyperliquid. Mainnet requires explicit <code>--network mainnet</code> or <code>live_network: mainnet</code> in config. Mainnet support is experimental.</p>
          <p>Use <code>--dry-run</code> flag (or <code>live_dry_run: true</code> in config) to simulate live execution without submitting real orders. Dry-run mode logs all order intents with timestamps for review.</p>

          <h4>Safety Rails</h4>
          <ul>
            <li><code>EquityMonitor</code> — kills all positions if equity falls below the floor percentage</li>
            <li><code>MaxOrdersPerMinute</code> — rate-limits order submission to prevent runaway loops</li>
            <li><code>PerMarketPositionLimit</code> — caps notional exposure per market across venues</li>
            <li>Emergency stop: <code>flint live --kill SESSION_ID</code> or <code>POST /live/kill</code> closes all positions immediately</li>
            <li>Always paper trade first with <code>--paper</code> to verify strategy behavior before going live</li>
          </ul>
        `,
        codeBlocks: [
          { language: 'bash', code: `# Paper trading — Drift (simulated, no real money)
flint live my_strategy.py --market SOL-PERP --paper

# Paper trading — Hyperliquid
flint live my_strategy.py --market SOL-PERP --venue hyperliquid --paper

# Live trading — Drift (REAL MONEY)
export FLINT_PRIVATE_KEY="your_base58_solana_key"
export FLINT_RPC_URL="https://your-rpc.com"
flint live my_strategy.py --market SOL-PERP --real

# Live trading — Hyperliquid (REAL MONEY)
export FLINT_HYPERLIQUID_PRIVATE_KEY="0xabc..."
export FLINT_HYPERLIQUID_API_WALLET="0xdef..."
flint live my_strategy.py --market SOL-PERP --venue hyperliquid --real

# Multi-venue live trading (REAL MONEY on both)
flint live multi_venue_strat.py --venue multi --real` },
          { language: 'python', code: `# Programmatic multi-venue live setup
from flint.execution.multi_venue_live import MultiVenueLiveContext
from flint.risk import RiskManager, EquityMonitor, MaxOrdersPerMinute

ctx = MultiVenueLiveContext(
    venues=["drift", "hyperliquid"],
    capital_allocation={"drift": 5000, "hyperliquid": 3000},
    risk_manager=RiskManager([
        EquityMonitor(floor_pct=0.50),
        MaxOrdersPerMinute(60),
    ]),
)
ctx.run(MyFundingArbStrategy())` },
        ],
      },
    ],
  },
]
