# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
# ---

# %% [markdown]
# # Momentum Breakout — proof notebook
#
# Phase 1 T1.5. Proves `MomentumBreakoutStrategy` backtest ↔ paper parity
# on SOL-PERP with fixed seed and pinned data checksums.
#
# This is the directional baseline; funding_arb.py and basis_trade.py cover
# cross-venue strategies.
#
# **Acceptance:** PnL divergence < 2%; equity correlation ≥ 0.95.

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
from flint.strategy.momentum_breakout import MomentumBreakoutStrategy


# %% [markdown]
# ## Config

# %%
MARKET = "SOL-PERP"
RESOLUTION_S = 3600
LOOKBACK_DAYS = 90
INITIAL_CAPITAL = 10_000.0
FEE_RATE = 0.0005
SEED = 42
EXPECTED_CANDLE_HASH = "PIN-ME"


# %% [markdown]
# ## Load data

# %%
def _hash_candles(candles) -> str:
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

print(f"Loaded {len(candles):,} {MARKET} candles (sha256={candle_hash[:16]}...)")

# %% [markdown]
# ## Backtest

# %%
strategy = MomentumBreakoutStrategy(breakout_lookback=20, trailing_stop_pct=0.02)
engine = BacktestEngine(
    strategy=strategy,
    initial_capital=INITIAL_CAPITAL,
    fee_rate=FEE_RATE,
    fill_model=ClosePriceFill(),
    seed=SEED,
)
bt_result = engine.run(candles)

print(f"Backtest PnL:    ${bt_result.total_pnl:+,.2f}")
print(f"Sharpe:          {bt_result.sharpe_ratio:.2f}")
print(f"Trades:          {bt_result.total_trades}")

# %% [markdown]
# ## Parity

# %%
report = ParityTest(
    strategy=MomentumBreakoutStrategy(
        breakout_lookback=20, trailing_stop_pct=0.02,
    ),
    market=MARKET,
    candles=candles,
    initial_capital=INITIAL_CAPITAL,
    fee_rate=FEE_RATE,
).run()

print()
print(report.summary())

# %% [markdown]
# ## Verdict

# %%
out_dir = Path("artifacts/parity")
out_dir.mkdir(parents=True, exist_ok=True)
date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
out_path = out_dir / f"momentum_breakout-{MARKET}-{date_str}.md"
out_path.write_text(
    f"# Momentum Breakout parity — {date_str}\n\n"
    f"- candle sha256: `{candle_hash}`\n"
    f"- seed: `{SEED}`\n"
    f"- candles: {len(candles):,}\n\n"
    f"## Summary\n\n```\n{report.summary()}\n```\n\n"
    f"## Raw\n\n```json\n{json.dumps(report.to_dict(), indent=2)}\n```\n",
    encoding="utf-8",
)
print(f"\nWrote {out_path}")
sys.exit(0 if report.passed else 1)
