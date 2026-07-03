"""Venue-number tests — every hard number cited to a primary source (D14).

Each assertion pins a VenueSpec constant against the source URL in its docstring.
The round-1 lesson (a review *correction* was itself wrong) is why these exist:
a number without a cited, tested source is a guess. Numbers we could not verify
against primary docs at coding time are asserted to be flagged UNVERIFIED, not
asserted to be correct.
"""

from __future__ import annotations

import pytest

from flint.venues import HYPERLIQUID, MaintTier, MarketStructure


def test_hyperliquid_is_a_clob_venue():
    assert HYPERLIQUID.structure is MarketStructure.CLOB


def test_hyperliquid_taker_fee_base_tier():
    # Source: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
    # Perps Tier 0 (no volume threshold, no staking discount): taker 0.045%.
    assert HYPERLIQUID.taker_fee_rate == 0.00045


def test_hyperliquid_maker_fee_base_tier():
    # Source: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
    # Perps Tier 0: maker 0.015%.
    assert HYPERLIQUID.maker_fee_rate == 0.00015


def test_hyperliquid_taker_is_three_times_maker():
    # The ~3x maker/taker gap is why liquidity must be classified per fill (§6.3).
    assert HYPERLIQUID.taker_fee_rate == HYPERLIQUID.maker_fee_rate * 3


def test_hyperliquid_price_rounding_is_five_significant_figures():
    # HL price rule: 5 significant figures. Source: HL tick/lot-size docs.
    assert HYPERLIQUID.price_sig_figs == 5


def test_hyperliquid_maintenance_is_half_initial_at_max_leverage():
    # Source: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations
    # "maintenance margin is half the initial margin at max leverage", ranging
    # 1.25% (40x) to 16.7% (3x). The §6.5 golden asset is 20x → 2.5%. Verified
    # 2026-07 (D14). Per-asset tier BOUNDARIES are unpublished → tiers_verified False.
    liq = HYPERLIQUID.liquidation
    assert liq.maint_frac(1_000.0) == 0.025  # 20x base tier → 2.5%
    assert MaintTier(0.0, 40.0).maint_frac == 0.0125  # 1.25% at 40x
    assert MaintTier(0.0, 3.0).maint_frac == pytest.approx(1 / 6)  # 16.7% at 3x
    assert liq.tiers_verified is False
    assert "liquidations" in liq.sources


def test_hyperliquid_has_no_clearance_fee_and_a_two_thirds_backstop():
    # Source: liquidations page — "Unlike CEXs there is no clearance fee on
    # liquidations"; backstop (HLP vault takeover) triggers below 2/3 maintenance.
    # Verified 2026-07 (D14).
    liq = HYPERLIQUID.liquidation
    assert liq.clearance_fee_frac == 0.0
    assert liq.backstop_maint_frac == pytest.approx(2 / 3)
    assert liq.adl_rank == "pnl_pct_x_leverage"  # HL ranks by PnL%×lev, not loser
    assert "no_clearance_fee" in liq.sources


def test_hyperliquid_funding_rate_cap_is_four_percent_hourly():
    # Source: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding
    # "Funding on Hyperliquid is capped at 4%/hour" and is paid hourly (1/8 of the
    # 8h rate). The cap does not depend on the asset. Verified 2026-07 (D14).
    assert HYPERLIQUID.rate_cap_hourly == 0.04
    assert "funding" in HYPERLIQUID.sources


def test_oracle_band_is_flagged_unverified():
    # The oracle price band width could NOT be fetched from primary docs at
    # coding time (order-book/robust-price pages unreachable, 2026-07). It is a
    # placeholder and MUST be flagged so, not presented as a checked fact (D14).
    assert HYPERLIQUID.oracle_band_verified is False
    assert "UNVERIFIED" in HYPERLIQUID.sources["oracle_band"]


def test_latency_profile_is_a_visible_default():
    # Latency is a Flint modelling default (calibratable), not venue law — but it
    # is anchored to real block timing and must be present.
    assert HYPERLIQUID.latency.base_ms == 250.0
    assert HYPERLIQUID.latency.p95_ms == 600.0
