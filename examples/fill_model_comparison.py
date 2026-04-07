"""
Fill Model Comparison
=====================
Runs the same strategy through 4 fill models to show how fill assumptions
affect backtest results.

    python examples/fill_model_comparison.py

Requires SOL-PERP data in the local store. Run canonical_backtest.py first
if you haven't downloaded data yet.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone, timedelta
from typing import List, Tuple

from flint.store import FlintStore
from flint.models import Candle
from flint.strategy.momentum_breakout import MomentumBreakoutStrategy
from flint.backtest.engine import BacktestEngine
from flint.execution.fill_models import (
    ClosePriceFill,
    FillPipeline,
    NextBarOpenFill,
    SlippageFill,
)
from flint.analytics.metrics import compute_metrics


# ---------------------------------------------------------------------------
# Configuration (same as canonical_backtest.py)
# ---------------------------------------------------------------------------

MARKET = "SOL-PERP"
RESOLUTION_S = 3600
LOOKBACK_DAYS = 90
INITIAL_CAPITAL = 10_000.0
FEE_RATE = 0.0005


def load_candles(store: FlintStore, start_ts: int, end_ts: int) -> List[Candle]:
    """Load candles from the local store."""
    candles = store.query_candles(MARKET, RESOLUTION_S, start_ts, end_ts)
    if not candles:
        print(f"No {MARKET} data found. Run canonical_backtest.py first to download data.")
        sys.exit(1)
    return candles


def make_strategy():
    """Create a fresh strategy instance (reset between runs)."""
    return MomentumBreakoutStrategy(
        breakout_lookback=20,
        trailing_stop_pct=0.02,
        oracle_confirmation=0,
    )


def run_with_fill_model(candles, fill_model, label: str):
    """Run backtest with a given fill model and return (label, result, metrics, elapsed)."""
    strategy = make_strategy()
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=INITIAL_CAPITAL,
        fee_rate=FEE_RATE,
        fill_model=fill_model,
    )
    t0 = time.perf_counter()
    result = engine.run(candles)
    elapsed = time.perf_counter() - t0
    metrics = compute_metrics(result, INITIAL_CAPITAL)
    return label, result, metrics, elapsed


def compute_avg_fill_delta(result, candles) -> float:
    """Average fill price delta vs candle close, in basis points.

    Measures how much each fill deviates from the candle close price at the
    same timestamp. Positive means fills are worse (buy higher / sell lower).
    """
    close_by_ts = {c.ts: c.close for c in candles}
    deltas = []
    for fill in result.fills:
        close = close_by_ts.get(fill.ts)
        if close and close > 0:
            # For longs, worse = higher price. For shorts, worse = lower price.
            if fill.side.value == "long":
                delta_bps = (fill.price - close) / close * 10_000
            else:
                delta_bps = (close - fill.price) / close * 10_000
            deltas.append(delta_bps)
    return sum(deltas) / len(deltas) if deltas else 0.0


def print_comparison(rows: List[Tuple], candles):
    """Print a formatted comparison table."""
    start_dt = datetime.fromtimestamp(candles[0].ts, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(candles[-1].ts, tz=timezone.utc)

    print()
    print("=" * 80)
    print("  FILL MODEL COMPARISON")
    print("=" * 80)
    print(f"  Strategy:  Momentum Breakout (lookback=20, stop=2%)")
    print(f"  Market:    {MARKET}  |  Period: {start_dt:%Y-%m-%d} to {end_dt:%Y-%m-%d}")
    print(f"  Capital:   ${INITIAL_CAPITAL:,.0f}  |  Fee: {FEE_RATE * 10_000:.0f} bps")
    print(f"  Candles:   {len(candles):,} ({RESOLUTION_S // 3600}h bars)")
    print("-" * 80)
    print(f"  {'Model':<24} {'PnL':>10} {'Return':>9} {'Sharpe':>8} {'Max DD':>9} {'Fill Delta':>12}")
    print("-" * 80)

    for label, result, metrics, _ in rows:
        avg_delta = compute_avg_fill_delta(result, candles)
        print(
            f"  {label:<24} "
            f"${metrics.total_pnl:>+9,.2f} "
            f"{metrics.total_return_pct:>+8.2f}% "
            f"{metrics.sharpe_ratio:>7.2f} "
            f"{metrics.max_drawdown * 100:>8.2f}% "
            f"{avg_delta:>+10.1f} bps"
        )

    print("=" * 80)
    print()
    print("What the differences mean:")
    print()
    print("  ClosePriceFill    Fills at the candle close. Optimistic baseline -- you rarely")
    print("                    get the exact close price in live trading.")
    print()
    print("  NextBarOpenFill   Fills at the next bar's open. Accounts for the fact that you")
    print("                    decide to trade at close but execute at the next bar.")
    print()
    print("  SlippageFill      Adds 10 bps of slippage to every market order. Simple model")
    print("                    for average execution cost.")
    print()
    print("  FillPipeline      The full 4-tier model: latency simulation, sqrt market impact,")
    print("                    partial fills, and orderbook walk. Most realistic.")
    print()
    print("  If your strategy only looks good with ClosePriceFill, it probably won't survive")
    print("  live trading. Look for strategies that hold up across all four models.")
    print()


def main():
    now = datetime.now(timezone.utc)
    end_ts = int(now.timestamp())
    start_ts = int((now - timedelta(days=LOOKBACK_DAYS)).timestamp())

    store = FlintStore()
    candles = load_candles(store, start_ts, end_ts)
    print(f"Loaded {len(candles)} candles for {MARKET}.")
    print()

    # Define the fill models to compare
    models = [
        (ClosePriceFill(), "ClosePriceFill"),
        (NextBarOpenFill(), "NextBarOpenFill"),
        (SlippageFill(slippage_bps=10.0), "SlippageFill (10 bps)"),
        (FillPipeline(), "FillPipeline (default)"),
    ]

    rows = []
    for fill_model, label in models:
        print(f"  Running {label}...", end=" ", flush=True)
        row = run_with_fill_model(candles, fill_model, label)
        print(f"done ({row[3]:.1f}s)")
        rows.append(row)

    print_comparison(rows, candles)


if __name__ == "__main__":
    main()
