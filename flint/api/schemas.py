"""Request bodies for the REST surface (§12).

Pydantic models validate the wire shape before anything reaches ``services/``;
malformed bodies become the uniform ``validation`` error (§19.1). Responses are
plain JSON-safe dicts assembled by the services, so they are not modelled here —
the services own the result schema (metrics, cost, equity curve, …).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
