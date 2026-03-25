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

    @property
    def taker_fee_rate(self) -> float:
        return self.taker_fee_bps / 10_000

    @property
    def maker_fee_rate(self) -> float:
        return self.maker_fee_bps / 10_000


# Hardcoded defaults for major venues
VENUE_DEFAULTS: Dict[str, VenueConfig] = {
    "drift": VenueConfig(
        name="drift", taker_fee_bps=10, maker_fee_bps=-2,
        initial_margin=0.10, maintenance_margin=0.05, max_leverage=10,
    ),
    "hyperliquid": VenueConfig(
        name="hyperliquid", taker_fee_bps=3.5, maker_fee_bps=1,
        initial_margin=0.05, maintenance_margin=0.025, max_leverage=20,
    ),
    "binance": VenueConfig(
        name="binance", taker_fee_bps=4.5, maker_fee_bps=2,
        initial_margin=0.02, maintenance_margin=0.01, max_leverage=50,
    ),
    "okx": VenueConfig(
        name="okx", taker_fee_bps=5, maker_fee_bps=2,
        initial_margin=0.02, maintenance_margin=0.01, max_leverage=50,
    ),
    "bybit": VenueConfig(
        name="bybit", taker_fee_bps=5.5, maker_fee_bps=2,
        initial_margin=0.02, maintenance_margin=0.01, max_leverage=50,
    ),
    "dydx": VenueConfig(
        name="dydx", taker_fee_bps=5, maker_fee_bps=1,
        initial_margin=0.05, maintenance_margin=0.03, max_leverage=20,
    ),
    # Default venue (used when no venue specified)
    "default": VenueConfig(
        name="default", taker_fee_bps=5, maker_fee_bps=0,
        initial_margin=1.0, maintenance_margin=1.0, max_leverage=1,
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
            )
    return configs
