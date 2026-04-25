# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
# ---

# %% [markdown]
# # Funding Arb — proof notebook
#
# Phase 1 T1.5. Proves `FundingArbStrategy` backtest ↔ paper parity over
# 30 days on SOL-PERP, with a fixed seed and pinned data checksums.
#
# **Acceptance:** PnL divergence < 2% over 30 days; equity correlation ≥ 0.95;
# matched-fill price p95 ≤ 10bps.

# %%
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flint.backtest.engine import BacktestEngine
from flint.backtest.parity import ParityTest
from flint.execution.fill_models import ClosePriceFill
from flint.store import FlintStore
from flint.strategy.funding_arb import FundingArbStrategy


# %% [markdown]
# ## Config

# %%
MARKET = "SOL-PERP"
RESOLUTION_S = 3600
LOOKBACK_DAYS = 30
INITIAL_CAPITAL = 10_000.0
FEE_RATE = 0.0005
SEED = 42

# Pinned checksums — update whenever input data is regenerated. Replace
# "PIN-ME" with the sha256 of the parquet/csv once a canonical snapshot
# is committed to `artifacts/proof-data/`.
EXPECTED_CANDLE_HASH = "PIN-ME"

# %% [markdown]
# ## Load data

# %%
def _hash_candles(candles) -> str:
    """Hash a candle list — used to pin data snapshots."""
    h = hashlib.sha256()
    for c in candles:
        h.update(f"{c.ts},{c.open},{c.high},{c.low},{c.close},{c.volume},{c.market}\n"
                 .encode("utf-8"))
    return h.hexdigest()


now = datetime.now(timezone.utc)
end_ts = int(now.timestamp())
start_ts = int((now - timedelta(days=LOOKBACK_DAYS)).timestamp())

store = FlintStore()
candles = store.query_candles(MARKET, RESOLUTION_S, start_ts, end_ts)
if not candles:
    print(f"FAIL: no candles for {MARKET} at {RESOLUTION_S}s.")
    print(f"  run: flint data download --market {MARKET} --days {LOOKBACK_DAYS}")
    sys.exit(1)

candle_hash = _hash_candles(candles)
if EXPECTED_CANDLE_HASH != "PIN-ME" and candle_hash != EXPECTED_CANDLE_HASH:
    print(f"FAIL: candle hash drift — expected {EXPECTED_CANDLE_HASH}, got {candle_hash}")
    sys.exit(1)

print(f"Loaded {len(candles):,} {MARKET} candles")
print(f"  range: {datetime.fromtimestamp(candles[0].ts, timezone.utc):%Y-%m-%d}"
      f" → {datetime.fromtimestamp(candles[-1].ts, timezone.utc):%Y-%m-%d}")
print(f"  sha256: {candle_hash}")

# %% [markdown]
# ## Strategy

# %%
strategy = FundingArbStrategy()
print(f"Strategy: {strategy.name}")

# %% [markdown]
# ## Backtest

# %%
engine = BacktestEngine(
    strategy=strategy,
    initial_capital=INITIAL_CAPITAL,
    fee_rate=FEE_RATE,
    fill_model=ClosePriceFill(),
    seed=SEED,
)
bt_result = engine.run(candles)

print()
print(f"Backtest PnL:    ${bt_result.total_pnl:+,.2f}")
print(f"Sharpe:          {bt_result.sharpe_ratio:.2f}")
print(f"Trades:          {bt_result.total_trades}")
print(f"Max drawdown:    {bt_result.max_drawdown:.2%}")

# %% [markdown]
# ## Parity (backtest vs paper replay)

# %%
paper = ParityTest(
    strategy=FundingArbStrategy(),  # fresh instance
    market=MARKET,
    candles=candles,
    initial_capital=INITIAL_CAPITAL,
    fee_rate=FEE_RATE,
)
report = paper.run()

print()
print(report.summary())

# %% [markdown]
# ## Verdict

# %%
out_dir = Path("artifacts/parity")
out_dir.mkdir(parents=True, exist_ok=True)
date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
out_path = out_dir / f"funding_arb-{MARKET}-{date_str}.md"

out_path.write_text(
    f"# Funding Arb parity — {date_str}\n\n"
    f"- candle sha256: `{candle_hash}`\n"
    f"- seed: `{SEED}`\n"
    f"- candles: {len(candles):,}\n\n"
    "## Summary\n\n"
    "```\n"
    f"{report.summary()}\n"
    "```\n\n"
    "## Raw metrics\n\n"
    "```json\n"
    f"{json.dumps(report.to_dict(), indent=2)}\n"
    "```\n",
    encoding="utf-8",
)

print(f"\nWrote {out_path}")
sys.exit(0 if report.passed else 1)
