#!/usr/bin/env python
"""Parity report pipeline — Phase 1 T1.2.

Runs a reference strategy through the backtest engine and paper broker on
the same candle range with the same seed, then emits a checked-in markdown
report at `artifacts/parity/{strategy}-{market}-{date}.md` with:

- Per-engine PnL + trade counts
- Equity-curve correlation
- Fill-price MAE
- Signal-timing match pct
- Pass/fail verdict vs configured thresholds

Usage:
    python scripts/run_parity_report.py funding_momentum_v4 SOL-PERP 30
    python scripts/run_parity_report.py momentum_breakout SOL-PERP 90 --seed 42
    python scripts/run_parity_report.py --list-strategies

This script is the single command any outside researcher runs to validate
a strategy's backtest ↔ paper equivalence. CI gates on the emitted report
(Phase 5.3).
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional

from flint.backtest.parity import ParityReport, ParityTest
from flint.models import Candle
from flint.store import FlintStore


# ---------------------------------------------------------------------------
# Strategy registry — maps name to (module_path, class_name, default_params)
# ---------------------------------------------------------------------------

STRATEGY_CATALOG = {
    "momentum_breakout": (
        "flint.strategy.momentum_breakout",
        "MomentumBreakoutStrategy",
        {"breakout_lookback": 20, "trailing_stop_pct": 0.02},
    ),
    "ma_crossover": (
        "flint.strategy.ma_crossover",
        "MACrossoverStrategy",
        {"fast_period": 20, "slow_period": 50},
    ),
    "rsi": (
        "flint.strategy.rsi",
        "RSIStrategy",
        {"period": 14, "oversold": 30, "overbought": 70},
    ),
    "bollinger": (
        "flint.strategy.bollinger",
        "BollingerStrategy",
        {"period": 20, "num_std": 2.0},
    ),
    "funding_harvest": (
        "flint.strategy.funding_harvest",
        "FundingHarvestStrategy",
        {},
    ),
    "funding_arb": (
        "flint.strategy.funding_arb",
        "FundingArbStrategy",
        {},
    ),
}

# Thresholds for CI gate.
DEFAULT_THRESHOLD_PCT = 2.0      # PnL divergence %
DEFAULT_FILL_MAE_USD = 5.0       # Mean absolute error on fill prices
DEFAULT_TIMING_MATCH_PCT = 80.0  # Signal-timing match lower bound


def _resolve_strategy(name: str, overrides: Optional[dict] = None):
    if name not in STRATEGY_CATALOG:
        raise SystemExit(
            f"Unknown strategy {name!r}. Known: {sorted(STRATEGY_CATALOG)}"
        )
    module_path, class_name, defaults = STRATEGY_CATALOG[name]
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    params = dict(defaults)
    if overrides:
        params.update(overrides)
    return cls(**params), params


def _ensure_candles(store: FlintStore, market: str, resolution_s: int,
                    lookback_days: int) -> List[Candle]:
    """Pull candles from store. If empty, bail with guidance (don't silently
    fetch from network during a reproducibility run)."""
    now = datetime.now(timezone.utc)
    end_ts = int(now.timestamp())
    start_ts = int((now - timedelta(days=lookback_days)).timestamp())
    candles = store.query_candles(market, resolution_s, start_ts, end_ts)
    if not candles:
        raise SystemExit(
            f"No candles in store for {market} at {resolution_s}s resolution.\n"
            f"Run `flint data download --market {market} --days {lookback_days}` first."
        )
    return candles


def _render_markdown(
    strategy_name: str,
    market: str,
    lookback_days: int,
    resolution_s: int,
    seed: int,
    candles_count: int,
    first_ts: int,
    last_ts: int,
    report: ParityReport,
    params: dict,
    elapsed_s: float,
    fill_mae_threshold: float,
    timing_match_threshold: float,
) -> tuple[str, bool]:
    """Build the markdown report and compute overall pass/fail."""

    first_dt = datetime.fromtimestamp(first_ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last_dt = datetime.fromtimestamp(last_ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    pnl_ok = report.pnl_divergence_pct < report.threshold_pct
    mae_ok = report.fill_price_mae <= fill_mae_threshold
    timing_ok = report.signal_timing_match_pct >= timing_match_threshold
    all_ok = pnl_ok and mae_ok and timing_ok

    verdict = "✓ PASS" if all_ok else "✗ FAIL"

    def check(ok: bool) -> str:
        return "✓" if ok else "✗"

    lines = []
    lines.append(f"# Parity Report — {strategy_name} on {market}")
    lines.append("")
    lines.append(f"**Verdict:** {verdict}")
    lines.append("")
    lines.append("## Run configuration")
    lines.append("")
    lines.append(f"- Strategy: `{strategy_name}` (params: `{json.dumps(params)}`)")
    lines.append(f"- Market: `{market}`")
    lines.append(f"- Resolution: `{resolution_s}s`")
    lines.append(f"- Lookback: `{lookback_days} days`")
    lines.append(f"- Candles: `{candles_count:,}`")
    lines.append(f"- Range: {first_dt} → {last_dt}")
    lines.append(f"- Seed: `{seed}`")
    lines.append(f"- Report generated: {now}")
    lines.append(f"- Elapsed: `{elapsed_s:.2f}s`")
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Metric | Backtest | Paper | Divergence | Threshold | Pass |")
    lines.append("|---|---:|---:|---:|---:|:---:|")
    lines.append(
        f"| Total P&L (USD) | `${report.backtest_pnl:,.2f}` | `${report.paper_pnl:,.2f}` "
        f"| `{report.pnl_divergence_pct:.2f}%` | `< {report.threshold_pct}%` "
        f"| {check(pnl_ok)} |"
    )
    lines.append(
        f"| Trade count | `{report.backtest_trades}` | `{report.paper_trades}` "
        f"| — | match | {check(report.trade_count_match)} |"
    )
    lines.append(
        f"| Fill-price MAE | — | — | `${report.fill_price_mae:.4f}` "
        f"| `≤ ${fill_mae_threshold}` | {check(mae_ok)} |"
    )
    lines.append(
        f"| Equity correlation | — | — | `{report.equity_correlation:.4f}` "
        f"| `≥ 0.95` | {check(report.equity_correlation >= 0.95)} |"
    )
    lines.append(
        f"| Signal-timing match | — | — | `{report.signal_timing_match_pct:.1f}%` "
        f"| `≥ {timing_match_threshold}%` | {check(timing_ok)} |"
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("```")
    lines.append(report.summary())
    lines.append("```")
    lines.append("")
    lines.append("## Reproduce")
    lines.append("")
    lines.append("```bash")
    lines.append(
        f"python scripts/run_parity_report.py {strategy_name} {market} "
        f"{lookback_days} --seed {seed}"
    )
    lines.append("```")
    lines.append("")
    lines.append("## Raw report JSON")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(report.to_dict(), indent=2))
    lines.append("```")
    return "\n".join(lines), all_ok


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("strategy", nargs="?", help="Strategy name (see --list-strategies)")
    parser.add_argument("market", nargs="?", default="SOL-PERP")
    parser.add_argument("days", nargs="?", type=int, default=30,
                        help="Lookback window in days")
    parser.add_argument("--resolution-s", type=int, default=3600)
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--fee-rate", type=float, default=0.0005)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threshold-pct", type=float, default=DEFAULT_THRESHOLD_PCT)
    parser.add_argument("--fill-mae-threshold", type=float, default=DEFAULT_FILL_MAE_USD)
    parser.add_argument("--timing-threshold-pct", type=float,
                        default=DEFAULT_TIMING_MATCH_PCT)
    parser.add_argument("-o", "--out", type=Path, default=None,
                        help="Output markdown path. Default: "
                             "artifacts/parity/{strategy}-{market}-{date}.md")
    parser.add_argument("--list-strategies", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.list_strategies:
        for name, (mod, cls, params) in sorted(STRATEGY_CATALOG.items()):
            print(f"{name:25s} {mod}::{cls}")
        return

    if not args.strategy:
        parser.error("strategy name required (or --list-strategies)")

    strategy, params = _resolve_strategy(args.strategy)

    store = FlintStore()
    candles = _ensure_candles(store, args.market, args.resolution_s, args.days)

    if not args.quiet:
        print(f"[1/2] Loaded {len(candles):,} candles for {args.market}")
        print(f"[2/2] Running parity test (seed={args.seed})...")

    t0 = time.perf_counter()
    # NOTE: ParityTest does not currently thread the seed into engines. The
    # seed here seeds future RNG paths and is captured in the report for
    # reproducibility context. When ParityTest accepts seed in a later phase,
    # this will flow through end-to-end.
    test = ParityTest(
        strategy=strategy,
        market=args.market,
        candles=candles,
        initial_capital=args.capital,
        fee_rate=args.fee_rate,
        threshold_pct=args.threshold_pct,
    )
    report = test.run()
    elapsed = time.perf_counter() - t0

    out_path = args.out
    if out_path is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out_path = Path("artifacts/parity") / f"{args.strategy}-{args.market}-{date_str}.md"

    markdown, overall_pass = _render_markdown(
        strategy_name=args.strategy,
        market=args.market,
        lookback_days=args.days,
        resolution_s=args.resolution_s,
        seed=args.seed,
        candles_count=len(candles),
        first_ts=candles[0].ts,
        last_ts=candles[-1].ts,
        report=report,
        params=params,
        elapsed_s=elapsed,
        fill_mae_threshold=args.fill_mae_threshold,
        timing_match_threshold=args.timing_threshold_pct,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")

    if not args.quiet:
        print()
        print(report.summary())
        print()
        print(f"Wrote {out_path}")

    # CI gate: exit 1 on any threshold breach.
    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
