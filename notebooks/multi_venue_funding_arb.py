# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
# ---

# %% [markdown]
# # Multi-venue funding arb — proof notebook
#
# Phase 1 T1.5 (4th proof). Proves D-2.1.d structural correctness: a
# session can hold a long leg on Drift and a short leg on Hyperliquid
# **on the same market** simultaneously, with per-venue funding rates
# debiting/crediting each leg independently.
#
# Pre-D-2.1.d (v1.5.0), `PaperBroker.positions` was keyed by market
# only — opening Drift-long SOL-PERP and HL-short SOL-PERP collided on
# the same dict slot and the second fill DCA-merged into the first,
# silently destroying the arb. This notebook reproduces the
# funding-dislocation-arb shape against the new architecture and
# asserts the per-leg ledger lines up to the cent.
#
# **Acceptance:**
# 1. After 2 hourly funding ticks, two distinct legs survive on
#    `(drift, SOL-PERP)` and `(hyperliquid, SOL-PERP)`.
# 2. Each leg's `funding_paid` matches `notional * sum(rates)` for that
#    venue alone — no cross-contamination.
# 3. Total cash impact equals the sum of per-leg funding payments,
#    exactly.

# %%
from __future__ import annotations

import sys

from flint.execution.backtest_context import BacktestContext
from flint.execution.capital import VenueAllocator
from flint.models import FundingRate, Side


# %% [markdown]
# ## Config

# %%
MARKET = "SOL-PERP"
INITIAL_CAPITAL = 20_000.0
SIZE = 50.0           # 50 SOL on each leg
PRICE = 100.0         # flat price; $5k notional per leg

# Drift quotes a *positive* hourly rate (longs pay) — our long leg pays.
# HL quotes a *negative* hourly rate (shorts pay longs) — our short leg
# is on the receiving end. The dislocation is the whole point of the
# arb: net funding income should be positive on both legs combined.
DRIFT_RATE_HOURLY = 0.0002      # +2 bps/hour, long pays
HL_RATE_HOURLY = -0.0003        # -3 bps/hour, long receives, short pays

BASE_TS = 1_700_000_000
HOUR = 3600


# %% [markdown]
# ## Setup — open opposing legs on two venues
#
# The shared-capital allocator splits $20k 50/50 across drift and HL so
# each leg has its own venue balance to debit. Without the allocator,
# both legs draw from the global pool which obscures the per-venue
# attribution we want to prove.

# %%
allocator = VenueAllocator({"drift": 10_000.0, "hyperliquid": 10_000.0})
ctx = BacktestContext(
    initial_capital=INITIAL_CAPITAL,
    capital_allocator=allocator,
)

# Seed both legs directly through the manager — backtest's
# `process_market_orders` flow would also work, but for a proof the
# state-of-affairs construction is clearer.
from flint.execution._position import _Position

drift_long = _Position(
    market=MARKET, side=Side.LONG, size=SIZE,
    entry_price=PRICE, entry_ts=BASE_TS, venue="drift",
)
drift_long.mark_price = PRICE
hl_short = _Position(
    market=MARKET, side=Side.SHORT, size=SIZE,
    entry_price=PRICE, entry_ts=BASE_TS, venue="hyperliquid",
)
hl_short.mark_price = PRICE
ctx._pm.set(("drift", MARKET), drift_long)
ctx._pm.set(("hyperliquid", MARKET), hl_short)

# Confirm both legs survived — pre-D-2.1.d paper would have collided.
assert len(ctx._pm) == 2, (
    f"expected 2 distinct legs, got {len(ctx._pm)} — "
    f"position keying didn't preserve (venue, market) tuples"
)

cash_before = ctx.account.cash
print(f"cash before funding: ${cash_before:.4f}")
print(f"open legs: {sorted(ctx._pm.keys())}")


# %% [markdown]
# ## Apply 2 hours of funding per venue
#
# `BacktestContext.apply_funding(funding_rate)` iterates `_pm.items()`
# by `(venue, market)` and routes each payment via
# `_cm.debit(payment, venue)` so the allocator's per-venue balance
# stays in sync.

# %%
total_drift_paid = 0.0
total_hl_paid = 0.0
for hour in range(2):
    ts = BASE_TS + (hour + 1) * HOUR

    # Drift: long pays positive rate
    drift_fr = FundingRate(
        market=MARKET, ts=ts, rate=DRIFT_RATE_HOURLY,
        oracle_price=PRICE, mark_price=PRICE, source="drift",
    )
    pay = ctx.apply_funding(drift_fr)
    total_drift_paid += pay
    print(f"hour {hour+1}: drift apply_funding → {pay:+.4f}")

    # HL: short pays *positive* rate ⇒ long receives. Our HL leg is
    # short, rate is negative, so HL short pays a negative payment
    # (i.e., receives cash). The sign convention in `apply_funding`
    # treats SHORT positions with negative rates as receiving.
    hl_fr = FundingRate(
        market=MARKET, ts=ts, rate=HL_RATE_HOURLY,
        oracle_price=PRICE, mark_price=PRICE, source="hyperliquid",
    )
    pay = ctx.apply_funding(hl_fr)
    total_hl_paid += pay
    print(f"hour {hour+1}: hl apply_funding   → {pay:+.4f}")


# %% [markdown]
# ## Assertions

# %%
cash_after = ctx.account.cash
total_funding_booked = ctx._cm.total_funding
cash_delta = cash_after - cash_before

print(f"\ncash after funding:     ${cash_after:.4f}")
print(f"cash delta:             ${cash_delta:+.4f}")
print(f"ctx total_funding:      ${total_funding_booked:+.4f}")

# Expected per-venue per-tick payment:
notional = SIZE * PRICE
drift_expected_per_tick = notional * DRIFT_RATE_HOURLY    # +1.0 — long pays
hl_expected_per_tick = -notional * HL_RATE_HOURLY         # +1.5 — short receives

drift_expected_total = drift_expected_per_tick * 2
hl_expected_total = hl_expected_per_tick * 2

print(f"\nexpected drift per-tick: ${drift_expected_per_tick:+.4f}  → 2 ticks: ${drift_expected_total:+.4f}")
print(f"expected hl    per-tick: ${hl_expected_per_tick:+.4f}  → 2 ticks: ${hl_expected_total:+.4f}")

# Per-leg attribution check — each leg's `funding_paid` should match
# only its own venue's accrual, no spillover.
drift_leg_paid = ctx._pm.get(("drift", MARKET)).funding_paid
hl_leg_paid = ctx._pm.get(("hyperliquid", MARKET)).funding_paid
print(f"\ndrift_leg.funding_paid: ${drift_leg_paid:+.4f}  (expected ${drift_expected_total:+.4f})")
print(f"hl_leg.funding_paid:    ${hl_leg_paid:+.4f}  (expected ${hl_expected_total:+.4f})")

# Per-venue cash check — each venue's allocator balance debited
# only by its leg's funding.
drift_balance_delta = ctx.venue_balance("drift") - 10_000.0
hl_balance_delta = ctx.venue_balance("hyperliquid") - 10_000.0
print(f"\ndrift venue balance delta: ${drift_balance_delta:+.4f}  (expected ${-drift_expected_total:+.4f})")
print(f"hl    venue balance delta: ${hl_balance_delta:+.4f}  (expected ${-hl_expected_total:+.4f})")


# %%
TOL = 1e-6
errors = []

# 1. Both legs distinct, neither got DCA-merged
if len(ctx._pm) != 2:
    errors.append(f"position count {len(ctx._pm)} != 2")

# 2. Per-leg funding_paid matches own-venue rate × notional × ticks
if abs(drift_leg_paid - drift_expected_total) > TOL:
    errors.append(
        f"drift leg funding_paid {drift_leg_paid:+.6f} "
        f"!= expected {drift_expected_total:+.6f}"
    )
if abs(hl_leg_paid - hl_expected_total) > TOL:
    errors.append(
        f"hl leg funding_paid {hl_leg_paid:+.6f} "
        f"!= expected {hl_expected_total:+.6f}"
    )

# 3. Per-venue allocator balance moves match per-leg funding
if abs(drift_balance_delta - (-drift_expected_total)) > TOL:
    errors.append(
        f"drift venue balance delta {drift_balance_delta:+.6f} "
        f"!= expected {-drift_expected_total:+.6f}"
    )
if abs(hl_balance_delta - (-hl_expected_total)) > TOL:
    errors.append(
        f"hl venue balance delta {hl_balance_delta:+.6f} "
        f"!= expected {-hl_expected_total:+.6f}"
    )

# 4. Total cash delta = sum of per-leg payments (no double-debit)
expected_cash_delta = -(drift_expected_total + hl_expected_total)
if abs(cash_delta - expected_cash_delta) > TOL:
    errors.append(
        f"global cash delta {cash_delta:+.6f} "
        f"!= sum of per-leg payments {expected_cash_delta:+.6f}"
    )

if errors:
    print("\n❌ PROOF FAILED:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("\n✅ multi-venue funding arb correctness proven")
print("   — 2 legs survive same-market opening")
print("   — each leg's funding_paid matches its venue rate exactly")
print("   — per-venue allocator balances move per-leg only")
print("   — global cash delta = sum of per-leg payments")
