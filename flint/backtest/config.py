"""BacktestConfig — canonical entry point for running a backtest.

Phase 2 T2.3. Collapses config sprawl (flint.yaml + BacktestEngine kwargs +
FillPipeline args + MarginEngine kwargs + allocator kwargs) into a single
dataclass tree with round-trip YAML + stable checksum.

The `checksum()` method is load-bearing for proof notebooks (Phase 1.5):
every run's config SHA is embedded in the parity report so the exact inputs
are pinned forever.

Usage
-----
    from flint.backtest.config import BacktestConfig

    cfg = BacktestConfig(
        market="SOL-PERP",
        start_ts=1700000000,
        end_ts=1700000000 + 30 * 86400,
        initial_capital=10_000.0,
        fee_rate=0.0005,
        seed=42,
    )
    engine = BacktestEngine.from_config(cfg, strategy)
    result = engine.run(candles)

Legacy kwargs on BacktestEngine remain supported for one release (see
docs/specs/phase-2-structural-cleanup.md#23-single-backtestconfig).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------

@dataclass
class FillConfig:
    """Fill-model parameters. Covers the common FillPipeline knobs; callers
    needing a fully-custom FillModel instance can still construct one and
    pass it directly to BacktestEngine as a legacy kwarg."""
    model: str = "pipeline"  # "pipeline" | "close" | "next_bar_open" | "slippage"
    slippage_bps: float = 5.0
    latency_enabled: bool = True
    latency_base_s: float = 1.0
    latency_jitter_s: float = 0.5
    impact_coefficient: float = 0.005
    participation_rate: float = 0.10
    tx_cost_enabled: bool = False


@dataclass
class MarginConfig:
    """Per-venue margin + liquidation settings."""
    enabled: bool = False
    maintenance_margin_ratio: float = 0.05  # 5% default (Drift-like)
    max_leverage: float = 10.0
    cross_margin: bool = True


@dataclass
class AllocatorConfig:
    """Multi-strategy capital allocator."""
    enabled: bool = False
    mode: str = "equal"  # "equal" | "risk_parity" | "fixed"
    per_strategy_pct: Dict[str, float] = field(default_factory=dict)


@dataclass
class VenueConfig:
    """Per-venue fee + constraint overrides."""
    taker_fee_bps: float = 5.0
    maker_fee_bps: float = 0.0
    max_leverage: float = 10.0
    tick_size: float = 0.01
    lot_size: float = 0.001


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    """Canonical backtest configuration.

    One dataclass tree, one checksum, one YAML file. Every field that
    affects numerical output belongs here; ephemeral fields (wall-clock
    limits, logging) do not.
    """
    # Market + data window
    market: str = "SOL-PERP"
    start_ts: int = 0
    end_ts: int = 0
    resolution_s: int = 3600

    # Capital + fees
    initial_capital: float = 10_000.0
    fee_rate: float = 0.0005
    position_size: float = 1.0

    # Determinism (Phase 1 T1.1.c / T1.3.b — always seedable)
    seed: Optional[int] = None

    # Composable sub-configs
    fill: FillConfig = field(default_factory=FillConfig)
    margin: MarginConfig = field(default_factory=MarginConfig)
    allocator: Optional[AllocatorConfig] = None
    venues: Dict[str, VenueConfig] = field(default_factory=dict)

    # -- Serialization --------------------------------------------------------

    def to_json(self) -> Dict[str, Any]:
        """Plain-dict representation. Stable field order for reproducible
        checksums across Python runs."""
        return asdict(self)

    def to_json_str(self) -> str:
        return json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"))

    def checksum(self) -> str:
        """SHA-256 of the canonical JSON form. Used by proof notebooks and
        parity reports to pin exact inputs."""
        return hashlib.sha256(self.to_json_str().encode("utf-8")).hexdigest()

    # -- Construction ---------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BacktestConfig":
        """Reconstruct from a plain dict (e.g. JSON, YAML). Tolerant of
        extra keys; strict on nested type mismatches."""
        d = dict(data)
        if "fill" in d and isinstance(d["fill"], dict):
            d["fill"] = FillConfig(**d["fill"])
        if "margin" in d and isinstance(d["margin"], dict):
            d["margin"] = MarginConfig(**d["margin"])
        if "allocator" in d and isinstance(d["allocator"], dict):
            d["allocator"] = AllocatorConfig(**d["allocator"])
        if "venues" in d and isinstance(d["venues"], dict):
            d["venues"] = {
                k: VenueConfig(**v) if isinstance(v, dict) else v
                for k, v in d["venues"].items()
            }
        # Ignore keys not on the dataclass (forward-compat with newer configs).
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        d = {k: v for k, v in d.items() if k in allowed}
        return cls(**d)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "BacktestConfig":
        import yaml  # PyYAML is already a hard dep
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data or {})

    # -- Legacy kwargs shim ---------------------------------------------------

    @classmethod
    def from_legacy_kwargs(cls, **kwargs) -> "BacktestConfig":
        """Build a config from the old BacktestEngine kwarg flavor.

        Used by `BacktestEngine.__init__` during the one-release deprecation
        window so existing callers don't break. The returned config is NOT
        round-tripped through YAML because legacy kwargs include FillModel
        and other non-JSON-serializable objects; use `from_yaml` / `from_dict`
        for proof-artifact provenance instead.
        """
        # Pull known fields; leave everything else to the engine's existing
        # handling (fill_model instance, fee_model instance, etc.).
        cfg = cls(
            market=kwargs.get("market", "SOL-PERP"),
            start_ts=kwargs.get("start_ts", 0),
            end_ts=kwargs.get("end_ts", 0),
            resolution_s=kwargs.get("resolution_s", 3600),
            initial_capital=kwargs.get("initial_capital", 10_000.0),
            fee_rate=kwargs.get("fee_rate", 0.0005),
            position_size=kwargs.get("position_size", 1.0),
            seed=kwargs.get("seed"),
        )
        return cfg
