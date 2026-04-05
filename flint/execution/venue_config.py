"""Venue configuration for multi-venue backtesting.

Defines per-venue fee structures, margin rules, and trading parameters.
Loaded from flint.yaml or hardcoded defaults.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class VenueConfig:
    """Trading parameters for a single venue."""
    name: str
    taker_fee_bps: float = 10.0
    maker_fee_bps: float = -2.0
    initial_margin: float = 0.10     # 10% = 10x max leverage
    maintenance_margin: float = 0.05  # 5%
    max_leverage: float = 10.0
    liquidation_penalty: float = 0.01  # 1%
    impact_coefficient: float = 0.05    # sqrt model k factor
    base_latency_s: float = 1.0         # base execution delay in seconds
    latency_jitter_s: float = 0.5       # +/- jitter range in seconds

    @property
    def taker_fee_rate(self) -> float:
        return self.taker_fee_bps / 10_000

    @property
    def maker_fee_rate(self) -> float:
        return self.maker_fee_bps / 10_000


# Hardcoded defaults for major venues
VENUE_DEFAULTS: Dict[str, VenueConfig] = {
    # Impact coefficients calibrated for candle-level backtesting.
    # Candle volume is a sample of total market volume, so raw participation
    # rates are inflated ~10x. These k values produce realistic impact:
    # $10k order on SOL-PERP hourly candle ≈ 5-15bps (matching real fills).
    "drift": VenueConfig(
        name="drift", taker_fee_bps=10, maker_fee_bps=-2,
        initial_margin=0.10, maintenance_margin=0.05, max_leverage=10,
        impact_coefficient=0.01, base_latency_s=8.0, latency_jitter_s=5.0,
    ),
    "hyperliquid": VenueConfig(
        name="hyperliquid", taker_fee_bps=3.5, maker_fee_bps=1,
        initial_margin=0.05, maintenance_margin=0.025, max_leverage=20,
        impact_coefficient=0.005, base_latency_s=1.0, latency_jitter_s=0.5,
    ),
    "binance": VenueConfig(
        name="binance", taker_fee_bps=4.5, maker_fee_bps=2,
        initial_margin=0.02, maintenance_margin=0.01, max_leverage=50,
        impact_coefficient=0.002, base_latency_s=0.2, latency_jitter_s=0.1,
    ),
    "okx": VenueConfig(
        name="okx", taker_fee_bps=5, maker_fee_bps=2,
        initial_margin=0.02, maintenance_margin=0.01, max_leverage=50,
        impact_coefficient=0.003, base_latency_s=0.3, latency_jitter_s=0.15,
    ),
    "bybit": VenueConfig(
        name="bybit", taker_fee_bps=5.5, maker_fee_bps=2,
        initial_margin=0.02, maintenance_margin=0.01, max_leverage=50,
        impact_coefficient=0.003, base_latency_s=0.3, latency_jitter_s=0.15,
    ),
    "dydx": VenueConfig(
        name="dydx", taker_fee_bps=5, maker_fee_bps=1,
        initial_margin=0.05, maintenance_margin=0.03, max_leverage=20,
        impact_coefficient=0.006, base_latency_s=2.0, latency_jitter_s=1.0,
    ),
    "jupiter": VenueConfig(
        name="jupiter",
        taker_fee_bps=6.0,
        maker_fee_bps=6.0,
        initial_margin=0.01,
        maintenance_margin=0.002,
        max_leverage=100.0,
        liquidation_penalty=0.0,
        impact_coefficient=0.03,
        base_latency_s=12.0,
        latency_jitter_s=8.0,
    ),
    # Default venue (used when no venue specified) — Drift-like defaults
    "default": VenueConfig(
        name="default", taker_fee_bps=5, maker_fee_bps=0,
        initial_margin=0.10, maintenance_margin=0.05, max_leverage=10,
        impact_coefficient=0.005, base_latency_s=1.0, latency_jitter_s=0.5,
    ),
}


def get_venue_config(venue: str) -> VenueConfig:
    """Get venue config by name, falling back to default."""
    return VENUE_DEFAULTS.get(venue, VENUE_DEFAULTS["default"])


def load_venue_configs(yaml_config: Optional[dict] = None) -> Dict[str, VenueConfig]:
    """Load venue configs from YAML config, merged with defaults."""
    configs = dict(VENUE_DEFAULTS)
    if yaml_config and "venues" in yaml_config:
        for name, overrides in yaml_config["venues"].items():
            base = VENUE_DEFAULTS.get(name, VENUE_DEFAULTS["default"])
            configs[name] = VenueConfig(
                name=name,
                taker_fee_bps=overrides.get("taker_fee_bps", base.taker_fee_bps),
                maker_fee_bps=overrides.get("maker_fee_bps", base.maker_fee_bps),
                initial_margin=overrides.get("initial_margin", base.initial_margin),
                maintenance_margin=overrides.get("maintenance_margin", base.maintenance_margin),
                max_leverage=overrides.get("max_leverage", base.max_leverage),
                liquidation_penalty=overrides.get("liquidation_penalty", base.liquidation_penalty),
                impact_coefficient=overrides.get("impact_coefficient", base.impact_coefficient),
                base_latency_s=overrides.get("base_latency_s", base.base_latency_s),
                latency_jitter_s=overrides.get("latency_jitter_s", base.latency_jitter_s),
            )
    return configs
