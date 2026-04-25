"""Tests for the pluggable price-source layer.

Each source has its own failure modes — HL parses a JSON map, Drift
parses an L2 book, DuckDB walks resolutions. We mock the network /
store boundary and assert the contract: success → quote, failure →
empty + health.error_count up.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from flint.paper.price_sources import (
    DriftDLOBSource,
    HyperliquidInfoSource,
    LocalDuckDBSource,
    SourceHealth,
    default_chain,
)
from flint.paper.price_sources.duckdb_cache import _RESOLUTIONS


class _FakeResp:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom", request=None, response=None,  # type: ignore[arg-type]
            )


# ─── Hyperliquid ────────────────────────────────────────────────

def test_hl_supports_known_market():
    src = HyperliquidInfoSource()
    assert src.supports("SOL-PERP") is True
    assert src.supports("FAKE-PERP") is False


def test_hl_fetch_returns_quotes():
    src = HyperliquidInfoSource()
    payload = {"SOL": "128.50", "BTC": "65000.0", "ETH": "3200.5"}
    with patch("flint.paper.price_sources.hyperliquid.httpx.post",
               return_value=_FakeResp(payload)):
        out = src.fetch(["SOL-PERP", "BTC-PERP"])
    assert out["SOL-PERP"].price == 128.50
    assert out["BTC-PERP"].price == 65000.0
    assert out["SOL-PERP"].source == "hyperliquid"
    assert out["SOL-PERP"].stale is False
    assert src.health.success_count == 1


def test_hl_fetch_skips_unknown_markets():
    src = HyperliquidInfoSource()
    payload = {"SOL": "128.50"}
    with patch("flint.paper.price_sources.hyperliquid.httpx.post",
               return_value=_FakeResp(payload)):
        out = src.fetch(["FAKE-PERP"])
    assert out == {}


def test_hl_fetch_records_error_on_network_fail():
    src = HyperliquidInfoSource()
    with patch("flint.paper.price_sources.hyperliquid.httpx.post",
               side_effect=httpx.ConnectError("dns")):
        out = src.fetch(["SOL-PERP"])
    assert out == {}
    assert src.health.error_count == 1
    assert "dns" in src.health.last_error


def test_hl_fetch_handles_non_dict_response():
    src = HyperliquidInfoSource()
    with patch("flint.paper.price_sources.hyperliquid.httpx.post",
               return_value=_FakeResp([1, 2, 3])):
        out = src.fetch(["SOL-PERP"])
    assert out == {}
    assert src.health.error_count == 1


def test_hl_fetch_skips_unparseable_price():
    src = HyperliquidInfoSource()
    payload = {"SOL": "not-a-number", "BTC": "65000.0"}
    with patch("flint.paper.price_sources.hyperliquid.httpx.post",
               return_value=_FakeResp(payload)):
        out = src.fetch(["SOL-PERP", "BTC-PERP"])
    assert "SOL-PERP" not in out
    assert out["BTC-PERP"].price == 65000.0


# ─── Drift DLOB ─────────────────────────────────────────────────

def test_drift_fetch_returns_mid():
    src = DriftDLOBSource()
    payload = {
        "bids": [{"price": str(int(100.0 * 1e6))}],
        "asks": [{"price": str(int(102.0 * 1e6))}],
    }
    with patch("flint.paper.price_sources.drift_dlob.httpx.get",
               return_value=_FakeResp(payload)):
        out = src.fetch(["SOL-PERP"])
    assert "SOL-PERP" in out
    assert abs(out["SOL-PERP"].price - 101.0) < 1e-6
    assert out["SOL-PERP"].source == "drift_dlob"


def test_drift_fetch_skips_empty_book():
    src = DriftDLOBSource()
    payload = {"bids": [], "asks": []}
    with patch("flint.paper.price_sources.drift_dlob.httpx.get",
               return_value=_FakeResp(payload)):
        out = src.fetch(["SOL-PERP"])
    assert out == {}


def test_drift_fetch_records_error_on_failure():
    """Drift is offline post-hack — exercise the fail-fast path."""
    src = DriftDLOBSource()
    with patch("flint.paper.price_sources.drift_dlob.httpx.get",
               side_effect=httpx.ConnectError("ECONNREFUSED")):
        out = src.fetch(["SOL-PERP"])
    assert out == {}
    assert src.health.error_count == 1


# ─── DuckDB cache ───────────────────────────────────────────────

class _FakeCandle:
    def __init__(self, ts: int, close: float):
        self.ts = ts
        self.close = close


def test_duckdb_returns_latest_with_stale_flag():
    store = MagicMock()
    store.query_candles.return_value = [_FakeCandle(1700000000, 128.0)]
    src = LocalDuckDBSource(store)
    out = src.fetch(["SOL-PERP"])
    assert out["SOL-PERP"].price == 128.0
    assert out["SOL-PERP"].stale is True
    assert out["SOL-PERP"].source == "duckdb_cache"
    # First resolution that returned a row wins; called at least once
    assert store.query_candles.called


def test_duckdb_walks_resolutions_until_hit():
    store = MagicMock()

    def _query(market, res, limit):
        if res == _RESOLUTIONS[0]:
            return []
        if res == _RESOLUTIONS[1]:
            return [_FakeCandle(1700000000, 99.0)]
        return []

    store.query_candles.side_effect = _query
    src = LocalDuckDBSource(store)
    out = src.fetch(["SOL-PERP"])
    assert out["SOL-PERP"].price == 99.0


def test_duckdb_returns_empty_when_no_candles_at_any_resolution():
    store = MagicMock()
    store.query_candles.return_value = []
    src = LocalDuckDBSource(store)
    out = src.fetch(["SOL-PERP"])
    assert out == {}


def test_duckdb_handles_query_exception():
    store = MagicMock()
    store.query_candles.side_effect = RuntimeError("locked")
    src = LocalDuckDBSource(store)
    out = src.fetch(["SOL-PERP"])
    assert out == {}
    assert src.health.error_count == 1


def test_duckdb_no_store_returns_empty():
    src = LocalDuckDBSource(None)
    assert src.fetch(["SOL-PERP"]) == {}


# ─── default_chain ──────────────────────────────────────────────

def test_default_chain_without_store_skips_cache():
    chain = default_chain()
    names = [s.name for s in chain]
    assert "hyperliquid" in names
    assert "drift_dlob" in names
    assert "duckdb_cache" not in names


def test_default_chain_with_store_includes_cache():
    chain = default_chain(store=MagicMock())
    names = [s.name for s in chain]
    # Order matters — HL first, then Drift, then cache last
    assert names == ["hyperliquid", "drift_dlob", "duckdb_cache"]


# ─── SourceHealth ───────────────────────────────────────────────

def test_source_health_to_dict_shape():
    h = SourceHealth(name="x")
    h.record_success()
    h.record_error("oops")
    d = h.to_dict()
    assert d["name"] == "x"
    assert d["success_count"] == 1
    assert d["error_count"] == 1
    assert d["last_error"] == "oops"
    assert d["last_success_ts"] is not None
