"""Shared test fixtures."""
from __future__ import annotations

import pytest

from flint.models import Candle
from flint.store import FlintStore


@pytest.fixture
def sample_candles() -> list[Candle]:
    """60 synthetic hourly candles: down → up → down (triggers BUY then SELL)."""
    base_ts = 1_700_000_000
    res = 3600
    candles = []
    # Phase 1 (0-19):  price drops 150 → 112 — establishes fast < slow
    # Phase 2 (20-39): price rises 112 → 150 — fast crosses above slow → BUY
    # Phase 3 (40-59): price drops 150 → 112 — fast crosses below slow → SELL
    for i in range(60):
        if i < 20:
            price = 150 - i * 2.0
        elif i < 40:
            price = 112 + (i - 20) * 2.0
        else:
            price = 150 - (i - 40) * 2.0
        candles.append(
            Candle(
                ts=base_ts + i * res,
                open=price - 0.5,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
                volume=100.0 + i,
                market="TEST-PERP",
                resolution_s=res,
            )
        )
    return candles


@pytest.fixture
def store():
    """In-memory DuckDB store, auto-closed."""
    s = FlintStore(":memory:")
    yield s
    s.close()
