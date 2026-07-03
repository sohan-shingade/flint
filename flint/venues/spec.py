"""``VenueSpec`` — a venue's hard numbers, each with a primary source (D14).

Every hard venue number (fee tiers, oracle band, and — as later slices grow this
spec — funding cap and maintenance tiers) lives here with a cited source URL, and
each gets a unit test asserting the value against that source (D14: the round-1
lesson that a review *correction* was itself wrong). A number we could not verify
against primary docs at coding time is marked ``UNVERIFIED`` so it is impossible
to mistake a guess for a checked fact.

Slice 3.2 populates the fill-relevant fields (fees, oracle band, latency, tick,
Tier-C parametric defaults); slice 3.3 adds ``rate_cap_hourly`` (the funding
clamp); slice 3.4 adds the ``LiquidationSpec`` (maintenance tiers, liquidation
fee, ADL rank). The shape is additive — later slices add fields, never
restructure.
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
class MaintTier:
    """One size band of a venue's maintenance-margin schedule (§6.5, D14).

    HL sets the maintenance margin at **half the initial margin at the tier's max
    leverage**, so ``maint_frac = 0.5 / max_leverage`` (verified: 1.25% at 40x …
    16.7% at 3x). Larger positions fall into a lower-max-leverage tier — more
    margin required. ``lower_notional`` is the inclusive USD-notional floor of the
    band; tiers are held ascending by that floor.
    """

    lower_notional: float
    max_leverage: float

    @property
    def maint_frac(self) -> float:
        """Maintenance fraction for this band = half the initial margin at max lev."""
        return 0.5 / self.max_leverage


@dataclass(frozen=True)
class LiquidationSpec:
    """What happens at and after a liquidation on one venue (§6.5, D14).

    The *trigger* (equity < maintenance) is universal; the loss *after* it is
    venue law. HL specifics verified 2026-07 against the margining + liquidations
    docs (``sources``):

    * ``maint_tiers`` — maintenance = ``0.5 / max_leverage``; max leverage falls
      with position value across tiers. The FORMULA is cited; the per-asset tier
      *boundaries* are not published on the docs pages → ``tiers_verified=False``
      (v1 carries a single base tier for the golden's 20x asset).
    * ``clearance_fee_frac`` — HL charges **no** liquidation/clearance fee ("Unlike
      CEXs there is no clearance fee on liquidations"). Normal liquidations lose
      only book slippage — this **corrects the design-spec's "distance-based
      liquidation fee" premise** (flagged to team-lead): the sub-mark loss is the
      *backstop*, below, not a fee.
    * ``backstop_maint_frac`` — when equity falls below this × maintenance (HL:
      2/3) without a successful book liquidation, the HLP liquidator vault takes
      over and the user **forfeits the remaining margin** (the real distance-based
      loss toward bankruptcy, via the vault). For cross, all cross margin can
      transfer (equity → ~0).
    * ``insurance_fund_solvent`` — v1 assumes the HLP/insurance backstop is solvent;
      recorded on every liquidation event so the tearsheet surfaces the assumption.
    * ``adl_rank`` — how counterparties are chosen if the backstop fails: HL ranks
      by PnL% × leverage, **not** "largest loser". Recorded for provenance; v1
      never triggers ADL under the solvency simplification.
    """

    maint_tiers: tuple[MaintTier, ...]
    clearance_fee_frac: float
    backstop_maint_frac: float
    insurance_fund_solvent: bool
    adl_rank: str
    tiers_verified: bool
    sources: Mapping[str, str] = field(default_factory=dict)

    def maint_frac(self, notional: float) -> float:
        """The maintenance fraction for a position of ``notional`` USD (§6.5)."""
        frac = self.maint_tiers[0].maint_frac
        for tier in self.maint_tiers:
            if notional >= tier.lower_notional:
                frac = tier.maint_frac
        return frac


@dataclass(frozen=True)
class VenueSpec:
    """The cited, testable constants for one venue's fills (§6.3, D14)."""

    name: str
    structure: MarketStructure
    taker_fee_rate: float  # fraction of notional
    maker_fee_rate: float  # fraction of notional
    rate_cap_hourly: float  # funding rate clamp per hour (HL: 4%/hr, cited D14)
    oracle_band_bps: float  # market-order clip / limit-reject band vs oracle
    oracle_band_verified: bool  # False → the band width is a placeholder (D14)
    book_staleness_s: float  # book older than this at effective time → treat absent
    price_sig_figs: int  # venue price rounding (HL: 5 significant figures)
    size_decimals: int  # venue lot rounding (per-asset szDecimals; a default here)
    latency: LatencyProfile
    default_half_spread_bps: float  # Tier-C majors bucket half-spread
    tier_c_impact_k: float  # Tier-C sqrt-impact coefficient (uncalibrated default)
    liquidation: LiquidationSpec  # trigger + post-trigger loss model (§6.5, D14)
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
    "funding": "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding",
    "oracle_band": "UNVERIFIED — docs page unreachable at coding (2026-07); 1% is the §6.3 placeholder",
    "block_time": "https://hyperliquid.gitbook.io/hyperliquid-docs (HyperBFT ~70-500ms; latency is a Flint default)",
}

# --- HL liquidation model (§6.5) -------------------------------------------
#
# Verified 2026-07 against the margining + liquidations docs. Maintenance margin
# is half the initial margin at the asset's max leverage (1.25% at 40x … 16.7% at
# 3x); the 20x → 2.5% case is the §6.5 golden. HL charges NO clearance fee on
# liquidations (this contradicts the design-spec's "distance-based liquidation
# fee" language — the sub-mark loss is the 2/3-maintenance backstop, where the HLP
# vault takes over and the user forfeits remaining margin). Per-asset margin-tier
# *notional boundaries* are not published on the docs pages → tiers_verified=False,
# and v1 carries only the base 20x tier.

_HL_LIQ_SOURCES = {
    "maintenance": "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining (maint = 1/2 initial margin at max leverage)",
    "liquidations": "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations",
    "no_clearance_fee": "liquidations page: 'Unlike CEXs there is no clearance fee on liquidations' (verified 2026-07)",
    "backstop_two_thirds": "liquidations page: backstop when equity < 2/3 maintenance margin, HLP vault takes over (verified 2026-07)",
    "adl_rank": "liquidations/§6.5: ADL ranks by PnL% × leverage, not largest-loser",
    "tier_boundaries": "UNVERIFIED — per-asset margin-tier notional boundaries not published on docs pages (2026-07); v1 carries base 20x tier only",
}

HYPERLIQUID_LIQUIDATION = LiquidationSpec(
    maint_tiers=(
        # SOL-class asset: 20x max leverage → 2.5% maintenance (the §6.5 golden).
        # HL max leverage is per-asset (3–40x → maint 16.7%–1.25%); the notional
        # boundaries between an asset's own tiers are UNVERIFIED (see sources).
        MaintTier(lower_notional=0.0, max_leverage=20.0),
    ),
    clearance_fee_frac=0.0,  # HL: NO clearance fee on liquidations (verified)
    backstop_maint_frac=2.0 / 3.0,  # HL: backstop below 2/3 maintenance (verified)
    insurance_fund_solvent=True,  # v1 simplification (§6.5) — surfaced on every event
    adl_rank="pnl_pct_x_leverage",  # HL ranking rule (§6.5) — not "largest loser"
    tiers_verified=False,  # formula verified; per-asset tier boundaries UNVERIFIED
    sources=_HL_LIQ_SOURCES,
)

HYPERLIQUID = VenueSpec(
    name="hyperliquid",
    structure=MarketStructure.CLOB,
    taker_fee_rate=0.00045,  # 0.045% — HL Tier 0 (fetched 2026-07)
    maker_fee_rate=0.00015,  # 0.015% — HL Tier 0 (fetched 2026-07)
    rate_cap_hourly=0.04,  # 4%/hr — HL funding cap, paid hourly (verified 2026-07, see sources[funding])
    liquidation=HYPERLIQUID_LIQUIDATION,
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
