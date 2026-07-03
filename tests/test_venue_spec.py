"""Venue-number tests — every hard number cited to a primary source (D14).

Each assertion pins a VenueSpec constant against the source URL in its docstring.
The round-1 lesson (a review *correction* was itself wrong) is why these exist:
a number without a cited, tested source is a guess. Numbers we could not verify
against primary docs at coding time are asserted to be flagged UNVERIFIED, not
asserted to be correct.
"""

from __future__ import annotations

from flint.venues import HYPERLIQUID, MarketStructure


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
