"""Request bodies for the REST surface (§12).

Pydantic models validate the wire shape before anything reaches ``services/``;
malformed bodies become the uniform ``validation`` error (§19.1). Responses are
plain JSON-safe dicts assembled by the services, so they are not modelled here —
the services own the result schema (metrics, cost, equity curve, …).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# The wire vocabulary for the market-data granularity tier (§B7). Enum-validated
# here so a bad value is a uniform ``validation`` error before it reaches the
# services; "auto" resolves to the highest fully-covered tier (candles floor).
Granularity = Literal["auto", "candles", "ticks", "book"]

# The wire vocabulary for the simulation substrate (§6.0, D29) — mirrors
# ``flint.engine.select.KNOWN_ENGINES``. Enum-validated here so an unknown engine
# is the uniform ``validation`` error at the surface; "auto" resolves to the
# Nautilus engine as of the N9 parity flip ("legacy-bar" stays selectable).
Engine = Literal["auto", "legacy-bar", "nautilus"]


class RangeModel(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)


class BacktestBody(BaseModel):
    strategy: str
    universe: list[str] = Field(default_factory=lambda: ["SOL-PERP"], min_length=1)
    venues: list[str] = Field(default_factory=lambda: ["hyperliquid"], min_length=1)
    range: RangeModel
    fill_mode: str = "auto"
    resolution_s: int = Field(default=3600, gt=0)
    granularity: Granularity = "auto"
    engine: Engine = "auto"
    seed: int = 0
    initial_capital: str = "100000"
    overrides: dict[str, Any] = Field(default_factory=dict)
    signal_venues: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class SourceBacktestBody(BaseModel):
    """A user-source backtest (§13.2): ``source`` replaces the template name."""

    source: str = Field(min_length=1)
    universe: list[str] = Field(default_factory=lambda: ["SOL-PERP"], min_length=1)
    venues: list[str] = Field(default_factory=lambda: ["hyperliquid"], min_length=1)
    range: RangeModel
    fill_mode: str = "auto"
    resolution_s: int = Field(default=3600, gt=0)
    granularity: Granularity = "auto"
    engine: Engine = "auto"
    seed: int = 0
    initial_capital: str = "100000"
    overrides: dict[str, Any] = Field(default_factory=dict)
    signal_venues: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class DataPullBody(BaseModel):
    market: str
    venues: list[str] = Field(min_length=1)
    kind: str
    range: RangeModel


class AlertBody(BaseModel):
    rule: str
    threshold: float | None = None
    channel: str = "collect"
