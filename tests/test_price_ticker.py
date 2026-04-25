"""Tests for the price ticker — fallback chain semantics.

Original tests exercised a single Drift DLOB call. Post-redesign the
ticker walks a list of `PriceSource` instances per tick and the first
source to return a quote for a given market wins. Tests use fakes
that satisfy the `PriceSource` interface — no network.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Iterable, List

import pytest

from flint.paper.price_sources import PriceQuote, PriceSource
from flint.paper.price_ticker import PriceTicker


class _FakeSource(PriceSource):
    """Test double — returns a fixed map and counts fetch calls."""

    def __init__(self, name: str, prices: Dict[str, float], stale: bool = False):
        self.name = name
        super().__init__()
        self._prices = prices
        self._stale = stale
        self.calls = 0
        self.last_wanted: List[str] = []

    def fetch(self, markets: Iterable[str]) -> Dict[str, PriceQuote]:
        self.calls += 1
        wanted = list(markets)
        self.last_wanted = wanted
        out: Dict[str, PriceQuote] = {}
        now = int(time.time())
        for m in wanted:
            if m in self._prices:
                out[m] = PriceQuote(
                    market=m, price=self._prices[m], ts=now,
                    source=self.name, stale=self._stale,
                )
        if out:
            self.health.record_success()
        return out


class _DeadSource(PriceSource):
    """Always raises — the ticker should swallow + record on health."""
    name = "dead"

    def fetch(self, markets):
        raise RuntimeError("source is down")


def test_get_price_returns_none_initially():
    ticker = PriceTicker(["SOL-PERP"], sources=[])
    assert ticker.get_price("SOL-PERP") is None


def test_get_all_prices_back_compat():
    src = _FakeSource("hl", {"SOL-PERP": 128.50, "BTC-PERP": 65000.0})
    ticker = PriceTicker(["SOL-PERP", "BTC-PERP"], sources=[src])
    ticker._poll_once()
    prices = ticker.get_all_prices()
    assert prices["SOL-PERP"] == 128.50
    assert prices["BTC-PERP"] == 65000.0


def test_prices_property_back_compat():
    """Old code did `ticker.prices[m] = …` directly. Read path stays
    working via property; tests that mutate it directly are gone."""
    src = _FakeSource("hl", {"SOL-PERP": 100.0})
    ticker = PriceTicker(["SOL-PERP"], sources=[src])
    ticker._poll_once()
    assert ticker.prices == {"SOL-PERP": 100.0}


def test_add_market():
    ticker = PriceTicker(["SOL-PERP"], sources=[])
    ticker.add_market("BTC-PERP")
    assert "BTC-PERP" in ticker.markets
    ticker.add_market("SOL-PERP")
    assert ticker.markets.count("SOL-PERP") == 1


def test_first_source_wins():
    primary = _FakeSource("primary", {"SOL-PERP": 100.0})
    secondary = _FakeSource("secondary", {"SOL-PERP": 200.0})
    ticker = PriceTicker(["SOL-PERP"], sources=[primary, secondary])
    ticker._poll_once()
    assert ticker.get_price("SOL-PERP") == 100.0
    assert ticker.get_quote("SOL-PERP").source == "primary"
    # secondary not called since primary covered the market
    assert secondary.calls == 0


def test_fallback_when_primary_misses_market():
    primary = _FakeSource("hl", {"BTC-PERP": 65000.0})  # no SOL
    secondary = _FakeSource("cache", {"SOL-PERP": 128.0}, stale=True)
    ticker = PriceTicker(["SOL-PERP", "BTC-PERP"], sources=[primary, secondary])
    ticker._poll_once()
    assert ticker.get_price("BTC-PERP") == 65000.0
    assert ticker.get_price("SOL-PERP") == 128.0
    assert ticker.get_quote("SOL-PERP").stale is True
    assert ticker.get_quote("BTC-PERP").stale is False
    assert secondary.calls == 1
    assert secondary.last_wanted == ["SOL-PERP"]


def test_dead_source_falls_through():
    dead = _DeadSource()
    fallback = _FakeSource("cache", {"SOL-PERP": 128.0}, stale=True)
    ticker = PriceTicker(["SOL-PERP"], sources=[dead, fallback])
    ticker._poll_once()
    assert ticker.get_price("SOL-PERP") == 128.0
    assert dead.health.error_count == 1
    assert "down" in dead.health.last_error


def test_no_source_serves_market_keeps_none():
    src = _FakeSource("hl", {"BTC-PERP": 1.0})
    ticker = PriceTicker(["SOL-PERP"], sources=[src])
    ticker._poll_once()
    assert ticker.get_price("SOL-PERP") is None


def test_supports_filter_skips_source():
    class _PartialSource(_FakeSource):
        def supports(self, market: str) -> bool:
            return market == "BTC-PERP"

    partial = _PartialSource("partial", {"BTC-PERP": 1.0, "SOL-PERP": 9.0})
    fallback = _FakeSource("fb", {"SOL-PERP": 200.0})
    ticker = PriceTicker(["SOL-PERP", "BTC-PERP"], sources=[partial, fallback])
    ticker._poll_once()
    # partial only sees BTC because supports() filtered SOL out
    assert partial.last_wanted == ["BTC-PERP"]
    assert ticker.get_price("BTC-PERP") == 1.0
    assert ticker.get_price("SOL-PERP") == 200.0


def test_get_health_reports_per_source():
    a = _FakeSource("a", {"SOL-PERP": 1.0})
    b = _FakeSource("b", {})
    ticker = PriceTicker(["SOL-PERP"], sources=[a, b])
    ticker._poll_once()
    health = ticker.get_health()
    assert [h["name"] for h in health] == ["a", "b"]
    assert health[0]["success_count"] == 1
    assert health[1]["success_count"] == 0


@pytest.mark.asyncio
async def test_run_polls_chain():
    src = _FakeSource("hl", {"SOL-PERP": 130.0})
    ticker = PriceTicker(["SOL-PERP"], interval_s=0.05, sources=[src])
    task = asyncio.create_task(ticker.run())
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert ticker.get_price("SOL-PERP") == 130.0
    assert src.calls >= 1


def test_stop():
    ticker = PriceTicker(["SOL-PERP"], sources=[])
    assert not ticker._running
    ticker._running = True
    ticker.stop()
    assert not ticker._running
