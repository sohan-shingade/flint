"""``VenueSpec`` — a venue's hard numbers, each with a primary source (D14).

Every hard venue number (fee tiers, oracle band, and — as later slices grow this
spec — funding cap and maintenance tiers) lives here with a cited source URL, and
each gets a unit test asserting the value against that source (D14: the round-1
lesson that a review *correction* was itself wrong). A number we could not verify
against primary docs at coding time is marked ``UNVERIFIED`` so it is impossible
to mistake a guess for a checked fact.

Slice 3.2 populates the fill-relevant fields (fees, oracle band, latency, tick,
Tier-C parametric defaults). Slice 3.3 adds the funding cap; slice 3.4 adds the
``LiquidationSpec`` (maintenance tiers, liquidation fee, ADL rank). The shape is
additive — later slices add fields, never restructure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .structure import MarketStructure


@dataclass(frozen=True)
class LatencyProfile:
    """One-way decision→eligible latency: a lognormal (median, p95) in ms (§6.3).

    Latency is the time from the strategy deciding to the order being eligible at
    the venue; we fill against the book at ``submit_ts + latency`` because the
    book has moved since the decision. Modelled lognormal (heavy right tail,
    never negative). These are Flint modelling defaults — calibratable and
    visible (``--latency-profile``), not venue law — but anchored to real block
    timing (``note``).
    """

    base_ms: float  # median one-way latency
    p95_ms: float  # 95th percentile
    note: str = ""


@dataclass(frozen=True)
class VenueSpec:
    """The cited, testable constants for one venue's fills (§6.3, D14)."""

    name: str
    structure: MarketStructure
    taker_fee_rate: float  # fraction of notional
    maker_fee_rate: float  # fraction of notional
    oracle_band_bps: float  # market-order clip / limit-reject band vs oracle
    oracle_band_verified: bool  # False → the band width is a placeholder (D14)
    book_staleness_s: float  # book older than this at effective time → treat absent
    price_sig_figs: int  # venue price rounding (HL: 5 significant figures)
    size_decimals: int  # venue lot rounding (per-asset szDecimals; a default here)
    latency: LatencyProfile
    default_half_spread_bps: float  # Tier-C majors bucket half-spread
    tier_c_impact_k: float  # Tier-C sqrt-impact coefficient (uncalibrated default)
    sources: Mapping[str, str] = field(default_factory=dict)


# --- Hyperliquid: the only executable v1 venue (D28) -----------------------
#
# Fees fetched 2026-07 from the perps fee table (Tier 0, no volume threshold, no
# staking discount). NOTE these differ from the design spec's placeholder
# (0.035%/0.01%, flagged "verify at coding" in §6.3) — exactly the D14 case.
#
# The oracle price band that clips/rejects orders vs the oracle could NOT be
# fetched at coding time (the order-book/robust-price docs pages did not respond)
# → its width is UNVERIFIED: 1% is the design-spec placeholder (§6.3 item 4),
# flagged so, and surfaced to team-lead. book_staleness_s and the latency profile
# are Flint modelling defaults (recorder cadence / block timing), not HL numbers.

_HL_SOURCES = {
    "fees": "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees",
    "margining": "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining",
    "oracle_band": "UNVERIFIED — docs page unreachable at coding (2026-07); 1% is the §6.3 placeholder",
    "block_time": "https://hyperliquid.gitbook.io/hyperliquid-docs (HyperBFT ~70-500ms; latency is a Flint default)",
}

HYPERLIQUID = VenueSpec(
    name="hyperliquid",
    structure=MarketStructure.CLOB,
    taker_fee_rate=0.00045,  # 0.045% — HL Tier 0 (fetched 2026-07)
    maker_fee_rate=0.00015,  # 0.015% — HL Tier 0 (fetched 2026-07)
    oracle_band_bps=100.0,  # 1% — UNVERIFIED placeholder (§6.3), see sources
    oracle_band_verified=False,
    book_staleness_s=30.0,  # Flint recorder-cadence default (§6.3)
    price_sig_figs=5,  # HL price rule: 5 significant figures
    size_decimals=2,  # per-asset szDecimals; a conservative default
    latency=LatencyProfile(
        base_ms=250.0,
        p95_ms=600.0,
        note="Flint default; ~1 HyperBFT block + network. Calibratable (--latency-profile).",
    ),
    default_half_spread_bps=1.0,  # majors bucket (SOL/BTC/ETH); mid 3 / tail 10
    tier_c_impact_k=0.1,  # sqrt-law default — flagged uncalibrated until fitted
    sources=_HL_SOURCES,
)
