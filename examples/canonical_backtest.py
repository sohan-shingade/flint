"""
Flint Canonical Backtest Example
================================
Run this to verify your Flint installation works end-to-end.

    python examples/canonical_backtest.py

Expected runtime: ~5 seconds (after data download).
Expected result: Full metrics on SOL-PERP momentum breakout, 90 days of hourly data.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone, timedelta

from flint.store import FlintStore
from flint.providers.drift_candles import DriftCandleProvider
from flint.strategy.momentum_breakout import MomentumBreakoutStrategy
from flint.backtest.engine import BacktestEngine
from flint.execution.fill_models import FillPipeline
from flint.analytics.metrics import compute_metrics


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MARKET = "SOL-PERP"
RESOLUTION_S = 3600          # 1-hour candles
LOOKBACK_DAYS = 90
INITIAL_CAPITAL = 10_000.0
FEE_RATE = 0.0005            # 5 bps (Drift taker fee)


def ensure_data(store: FlintStore, start_ts: int, end_ts: int) -> int:
    """Check local store for SOL-PERP data; download from Drift if missing."""
    candles = store.query_candles(MARKET, RESOLUTION_S, start_ts, end_ts)
    if len(candles) >= LOOKBACK_DAYS * 20:  # expect ~2160 hourly candles, allow gaps
        return len(candles)

    print(f"Downloading {MARKET} hourly candles from Drift ({LOOKBACK_DAYS} days)...")
    provider = DriftCandleProvider()
    try:
        fetched = provider.fetch_candles(MARKET, RESOLUTION_S, start_ts, end_ts)
        if fetched:
            store.upsert_candles(fetched)
            print(f"  Stored {len(fetched)} candles.")
            return len(fetched)
        else:
            print("  Warning: Drift API returned no candles.")
            return 0
    finally:
        provider.close()


def run_backtest(candles):
    """Run momentum breakout strategy and return the result."""
    strategy = MomentumBreakoutStrategy(
        breakout_lookback=20,
        trailing_stop_pct=0.02,
        oracle_confirmation=0,   # disable oracle check for standalone backtest
    )
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=INITIAL_CAPITAL,
        fee_rate=FEE_RATE,
        fill_model=FillPipeline(),
    )
    return engine.run(candles)


def print_results(result, candles, elapsed_s: float):
    """Print a clean summary table."""
    metrics = compute_metrics(result, INITIAL_CAPITAL)
    start_dt = datetime.fromtimestamp(candles[0].ts, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(candles[-1].ts, tz=timezone.utc)

    print()
    print("=" * 60)
    print("  FLINT BACKTEST RESULTS")
    print("=" * 60)
    print(f"  Strategy:        Momentum Breakout (lookback=20, stop=2%)")
    print(f"  Market:          {MARKET}")
    print(f"  Period:          {start_dt:%Y-%m-%d} to {end_dt:%Y-%m-%d}")
    print(f"  Candles:         {len(candles):,} ({RESOLUTION_S // 3600}h bars)")
    print(f"  Initial capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"  Fill model:      FillPipeline (latency + impact + partial)")
    print(f"  Fee rate:        {FEE_RATE * 10_000:.0f} bps")
    print("-" * 60)
    print(f"  Total PnL:       ${metrics.total_pnl:+,.2f}")
    print(f"  Return:          {metrics.total_return_pct:+.2f}%")
    print(f"  Sharpe ratio:    {metrics.sharpe_ratio:.2f}")
    print(f"  Sortino ratio:   {metrics.sortino_ratio:.2f}")
    print(f"  Max drawdown:    {metrics.max_drawdown * 100:.2f}%")
    print(f"  Win rate:        {metrics.win_rate * 100:.1f}%")
    print(f"  Profit factor:   {metrics.profit_factor:.2f}")
    print(f"  Total trades:    {metrics.total_trades}")
    print(f"  Avg win:         ${metrics.avg_win:+,.2f}")
    print(f"  Avg loss:        ${metrics.avg_loss:+,.2f}")
    print(f"  Best trade:      ${metrics.best_trade:+,.2f}")
    print(f"  Worst trade:     ${metrics.worst_trade:+,.2f}")
    print(f"  Fees paid:       ${result.total_fees:.2f}")
    print(f"  Runtime:         {elapsed_s:.1f}s")
    print("=" * 60)
    print()
    print("Try next:")
    print("  flint serve                  # open the web UI and run this visually")
    print("  flint optimize momentum      # optimize strategy parameters with Optuna")
    print("  flint live --paper           # paper trade with live Drift prices")
    print()


def main():
    now = datetime.now(timezone.utc)
    end_ts = int(now.timestamp())
    start_ts = int((now - timedelta(days=LOOKBACK_DAYS)).timestamp())

    # 1. Connect to local DuckDB store
    store = FlintStore()

    # 2. Ensure we have data
    n_candles = ensure_data(store, start_ts, end_ts)
    if n_candles == 0:
        print("No candle data available. Check your network connection and try again.")
        sys.exit(1)

    # 3. Load candles
    candles = store.query_candles(MARKET, RESOLUTION_S, start_ts, end_ts)
    print(f"Loaded {len(candles)} candles for {MARKET}.")

    # 4. Run backtest
    t0 = time.perf_counter()
    result = run_backtest(candles)
    elapsed = time.perf_counter() - t0

    # 5. Print results
    print_results(result, candles, elapsed)


if __name__ == "__main__":
    main()
