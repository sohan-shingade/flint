"""Tests for the data download/management pipeline.

Covers: download endpoint, provider fallback, store batching,
venue funding, error handling, and edge cases.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from flint.models import Candle, FundingRate
from flint.store import FlintStore


# ─── Fixtures ────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    s = FlintStore(str(tmp_path / "test.duckdb"))
    yield s
    s.close()


@pytest.fixture
def app_client(store):
    """FastAPI test client with real store, bypassing lifespan's own store creation."""
    from flint.api.main import app

    with patch("flint.api.main.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(collector_enabled=False, db_path=":memory:")
        with TestClient(app) as c:
            # Override the lifespan-created store with our test store
            app.state.store = store
            yield c


def _make_candles(market: str, start_ts: int, count: int, resolution_s: int = 3600) -> list[Candle]:
    return [
        Candle(
            market=market, resolution_s=resolution_s,
            ts=start_ts + i * resolution_s,
            open=100.0 + i, high=101.0 + i, low=99.0 + i,
            close=100.5 + i, volume=1000.0,
        )
        for i in range(count)
    ]


# ─── Store: Batch Upsert ────────────────────────────


class TestStoreBatchUpsert:
    def test_small_batch_works(self, store):
        candles = _make_candles("SOL-PERP", 1000000, 10)
        count = store.upsert_candles(candles)
        assert count == 10
        result = store.query_candles("SOL-PERP", 3600)
        assert len(result) == 10

    def test_large_batch_works(self, store):
        """Large insert should be split into batches internally."""
        candles = _make_candles("SOL-PERP", 1000000, 5000)
        count = store.upsert_candles(candles)
        assert count == 5000
        result = store.query_candles("SOL-PERP", 3600)
        assert len(result) == 5000

    def test_empty_batch(self, store):
        assert store.upsert_candles([]) == 0

    def test_duplicate_candles_replaced(self, store):
        c1 = [Candle(market="SOL-PERP", resolution_s=3600, ts=1000000,
                      open=100, high=105, low=95, close=102, volume=500)]
        c2 = [Candle(market="SOL-PERP", resolution_s=3600, ts=1000000,
                      open=200, high=205, low=195, close=202, volume=999)]
        store.upsert_candles(c1)
        store.upsert_candles(c2)
        result = store.query_candles("SOL-PERP", 3600)
        assert len(result) == 1
        assert result[0].close == 202


# ─── Store: Venue Funding ────────────────────────────


class TestVenueFunding:
    def test_upsert_via_funding_rate_model(self, store):
        """upsert_funding_rates should insert into venue_funding_rates."""
        rates = [
            FundingRate(market="SOL-PERP", ts=1000000, rate=0.0001,
                        oracle_price=100, mark_price=100.5, slot=0, source="drift"),
            FundingRate(market="SOL-PERP", ts=1003600, rate=0.0002,
                        oracle_price=101, mark_price=101.5, slot=0, source="hyperliquid"),
        ]
        count = store.upsert_funding_rates(rates)
        assert count == 2

        # Should be queryable by venue
        result = store.query_funding_rates("SOL-PERP")
        assert len(result) == 2

    def test_query_by_venue(self, store):
        """query_funding_rates with venue filter."""
        rates = [
            FundingRate(market="SOL-PERP", ts=1000000, rate=0.0001,
                        oracle_price=100, mark_price=100.5, source="drift"),
            FundingRate(market="SOL-PERP", ts=1000000, rate=0.0002,
                        oracle_price=100, mark_price=100.5, source="hyperliquid"),
        ]
        store.upsert_funding_rates(rates)

        drift_only = store.query_funding_rates("SOL-PERP", venue="drift")
        assert len(drift_only) == 1
        assert drift_only[0].source == "drift"

        hl_only = store.query_funding_rates("SOL-PERP", venue="hyperliquid")
        assert len(hl_only) == 1
        assert hl_only[0].source == "hyperliquid"

    def test_query_funding_by_venue(self, store):
        """query_funding_by_venue returns grouped data."""
        rates = [
            FundingRate(market="SOL-PERP", ts=1000000, rate=0.0001,
                        oracle_price=100, mark_price=100.5, source="drift"),
            FundingRate(market="SOL-PERP", ts=1003600, rate=0.0002,
                        oracle_price=100, mark_price=100.5, source="drift"),
            FundingRate(market="SOL-PERP", ts=1000000, rate=0.0003,
                        oracle_price=100, mark_price=100.5, source="okx"),
        ]
        store.upsert_funding_rates(rates)

        by_venue = store.query_funding_by_venue("SOL-PERP")
        assert "drift" in by_venue
        assert "okx" in by_venue
        assert len(by_venue["drift"]) == 2
        assert len(by_venue["okx"]) == 1

    def test_corrupted_rates_rejected(self, store):
        rates = [
            FundingRate(market="SOL-PERP", ts=1000000, rate=0.0001,
                        oracle_price=100, mark_price=100.5, source="drift"),
            FundingRate(market="SOL-PERP", ts=1003600, rate=0.5,  # too large
                        oracle_price=100, mark_price=100.5, source="drift"),
        ]
        count = store.upsert_funding_rates(rates)
        assert count == 1

    def test_upsert_venue_funding_directly(self, store):
        """Test direct upsert_venue_funding with FundingSnapshot objects."""
        from flint.providers.funding_rates import FundingSnapshot
        snapshots = [
            FundingSnapshot(venue="drift", market="SOL-PERP", ts=1000000,
                           rate_hourly=0.0001, mark_price=100, index_price=100),
            FundingSnapshot(venue="hyperliquid", market="SOL-PERP", ts=1000000,
                           rate_hourly=0.0002, mark_price=100, index_price=100),
        ]
        count = store.upsert_venue_funding(snapshots)
        assert count == 2

        by_venue = store.query_funding_by_venue("SOL-PERP")
        assert len(by_venue) == 2


# ─── Download Endpoint ──────────────────────────────


class TestDownloadEndpoint:
    def test_download_new_market(self, app_client, store):
        candles = _make_candles("ETH-PERP", 1700000000, 24)
        with patch("flint.api.routes.data._download_pyth_candles", return_value=(candles, None)), \
             patch("flint.api.routes.data._download_funding_all_venues", return_value=10):
            resp = app_client.post("/api/v1/data/download", json={
                "market": "ETH-PERP", "resolution_s": 3600,
                "start_ts": 1700000000, "end_ts": 1700086400,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["downloaded"] == 24
        assert data["total"] >= 24

    def test_download_cached_skips(self, app_client, store):
        candles = _make_candles("SOL-PERP", 1700000000, 24)
        store.upsert_candles(candles)

        with patch("flint.api.routes.data._download_pyth_candles") as mock_dl, \
             patch("flint.api.routes.data._download_funding_all_venues", return_value=0):
            resp = app_client.post("/api/v1/data/download", json={
                "market": "SOL-PERP", "resolution_s": 3600,
                "start_ts": 1700000000, "end_ts": 1700086400,
            })
            data = resp.json()
            assert data["skipped"] is True
            assert data["downloaded"] == 0
            mock_dl.assert_not_called()

    def test_download_fills_gap(self, app_client, store):
        existing = _make_candles("SOL-PERP", 1700000000, 12)
        store.upsert_candles(existing)

        new_candles = _make_candles("SOL-PERP", 1700043200, 12)
        with patch("flint.api.routes.data._download_pyth_candles", return_value=(new_candles, None)), \
             patch("flint.api.routes.data._download_funding_all_venues", return_value=5):
            resp = app_client.post("/api/v1/data/download", json={
                "market": "SOL-PERP", "resolution_s": 3600,
                "start_ts": 1700000000, "end_ts": 1700086400,
            })
        data = resp.json()
        assert data["downloaded"] == 12
        assert data["existing"] == 12

    def test_download_all_providers_fail(self, app_client):
        with patch("flint.api.routes.data._download_pyth_candles", return_value=([], "all failed")), \
             patch("flint.api.routes.data._download_funding_all_venues", return_value=0):
            resp = app_client.post("/api/v1/data/download", json={
                "market": "FAKE-PERP", "resolution_s": 3600,
                "start_ts": 1700000000, "end_ts": 1700086400,
            })
        data = resp.json()
        assert data["downloaded"] == 0
        assert "error" in data

    def test_download_invalid_range(self, app_client):
        resp = app_client.post("/api/v1/data/download", json={
            "market": "SOL-PERP", "resolution_s": 3600,
            "start_ts": 1700086400, "end_ts": 1700000000,
        })
        assert resp.status_code == 400


# ─── Provider: DriftCandleProvider ───────────────────


class TestDriftCandleProvider:
    @patch("httpx.Client")
    def test_pagination_stops_on_no_records(self, mock_client_cls):
        from flint.providers.drift_candles import DriftCandleProvider
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"records": []}
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        provider = DriftCandleProvider(client=mock_client)
        result = provider.fetch_candles("SOL-PERP", 3600, 1700000000, 1700086400)
        assert result == []

    @patch("httpx.Client")
    def test_pagination_stops_on_no_progress(self, mock_client_cls):
        from flint.providers.drift_candles import DriftCandleProvider
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "records": [{"ts": 1700086400, "oracleOpen": 100, "oracleHigh": 101,
                          "oracleLow": 99, "oracleClose": 100.5, "baseVolume": 1000}]
        }
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        provider = DriftCandleProvider(client=mock_client)
        result = provider.fetch_candles("SOL-PERP", 3600, 1700000000, 1700086400)
        assert mock_client.get.call_count <= 3

    @patch("httpx.Client")
    def test_handles_timeout(self, mock_client_cls):
        import httpx
        from flint.providers.drift_candles import DriftCandleProvider
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.TimeoutException("timed out")
        provider = DriftCandleProvider(client=mock_client)
        result = provider.fetch_candles("SOL-PERP", 3600, 1700000000, 1700003600)
        assert result == []


# ─── Provider: DriftS3Provider ───────────────────────


class TestDriftS3Provider:
    @patch("httpx.Client")
    def test_404_returns_none(self, mock_client_cls):
        from flint.providers.drift_s3 import DriftS3Provider
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        provider = DriftS3Provider(client=mock_client)
        result = provider._download_day("SOL-PERP", "20240101")
        assert result is None

    @patch("httpx.Client")
    def test_handles_request_error(self, mock_client_cls):
        from flint.providers.drift_s3 import DriftS3Provider
        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("connection refused")
        provider = DriftS3Provider(client=mock_client)
        result = provider._download_day("SOL-PERP", "20240101")
        assert result is None


# ─── Provider: CoinGecko ─────────────────────────────


class TestCoinGeckoProvider:
    @patch("flint.providers.coingecko.time.sleep")
    @patch("httpx.Client")
    def test_rate_limit_retries(self, mock_client_cls, mock_sleep):
        from flint.providers.coingecko import CoinGeckoProvider
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"prices": [], "total_volumes": []}
        mock_client = MagicMock()
        mock_client.get.side_effect = [resp_429, resp_200]
        provider = CoinGeckoProvider(client=mock_client)
        result = provider._fetch_chunk("bitcoin", "BTC", 3600, 1700000000, 1700086400)
        assert result == []
        assert mock_client.get.call_count == 2

    @patch("httpx.Client")
    def test_unknown_market_returns_empty(self, mock_client_cls):
        from flint.providers.coingecko import CoinGeckoProvider
        mock_client = MagicMock()
        provider = CoinGeckoProvider(client=mock_client)
        result = provider.fetch_candles("NONEXIST-TOKEN", 3600, 1700000000, 1700086400)
        assert result == []


# ─── Download Funding All Venues ─────────────────────


class TestDownloadFundingAllVenues:
    def test_spot_market_returns_zero(self, store):
        """Spot markets should not download funding."""
        import logging
        from flint.api.routes.data import _download_funding_all_venues
        result = _download_funding_all_venues(store, "SOL", 1700000000, 1700086400, logging.getLogger())
        assert result == 0

    @patch("flint.providers.funding_rates.DriftFundingProvider")
    @patch("flint.providers.funding_rates.HyperliquidFundingProvider")
    @patch("flint.providers.funding_rates.OKXFundingProvider")
    @patch("flint.providers.funding_rates.BybitFundingProvider")
    def test_fetches_from_all_venues(self, mock_bybit, mock_okx, mock_hl, mock_drift, store):
        """Should attempt all 4 venues."""
        import logging
        from flint.api.routes.data import _download_funding_all_venues
        from flint.providers.funding_rates import FundingSnapshot

        for mock_cls, venue in [(mock_drift, "drift"), (mock_hl, "hyperliquid"),
                                 (mock_okx, "okx"), (mock_bybit, "bybit")]:
            mock_instance = MagicMock()
            mock_instance.fetch_funding.return_value = [
                FundingSnapshot(venue=venue, market="SOL-PERP", ts=1700000000,
                               rate_hourly=0.0001, mark_price=100, index_price=100),
            ]
            mock_cls.return_value = mock_instance

        result = _download_funding_all_venues(
            store, "SOL-PERP", 1700000000, 1700086400, logging.getLogger()
        )
        assert result >= 4  # at least 1 from each venue

    @patch("flint.providers.funding_rates.DriftFundingProvider")
    @patch("flint.providers.funding_rates.HyperliquidFundingProvider")
    @patch("flint.providers.funding_rates.OKXFundingProvider")
    @patch("flint.providers.funding_rates.BybitFundingProvider")
    def test_one_venue_failure_doesnt_block_others(self, mock_bybit, mock_okx, mock_hl, mock_drift, store):
        """If one venue fails, others should still succeed."""
        import logging
        from flint.api.routes.data import _download_funding_all_venues
        from flint.providers.funding_rates import FundingSnapshot

        # Drift fails
        mock_drift.return_value.fetch_funding.side_effect = Exception("drift down")
        # Others succeed
        for mock_cls, venue in [(mock_hl, "hyperliquid"), (mock_okx, "okx"), (mock_bybit, "bybit")]:
            mock_instance = MagicMock()
            mock_instance.fetch_funding.return_value = [
                FundingSnapshot(venue=venue, market="SOL-PERP", ts=1700000000,
                               rate_hourly=0.0001, mark_price=100, index_price=100),
            ]
            mock_cls.return_value = mock_instance

        result = _download_funding_all_venues(
            store, "SOL-PERP", 1700000000, 1700086400, logging.getLogger()
        )
        assert result >= 3  # 3 venues succeeded


# ─── Funding API Endpoint ────────────────────────────


class TestFundingEndpoint:
    def test_returns_venues_grouped(self, app_client, store):
        """GET /funding should return data grouped by venue."""
        from flint.providers.funding_rates import FundingSnapshot
        snapshots = [
            FundingSnapshot(venue="drift", market="SOL-PERP", ts=1700000000,
                           rate_hourly=0.0001, mark_price=100, index_price=100),
            FundingSnapshot(venue="hyperliquid", market="SOL-PERP", ts=1700000000,
                           rate_hourly=0.0002, mark_price=100, index_price=100),
        ]
        store.upsert_venue_funding(snapshots)

        resp = app_client.get("/api/v1/data/funding?market=SOL-PERP")
        data = resp.json()
        assert data["count"] == 2
        assert "drift" in data["venues"]
        assert "hyperliquid" in data["venues"]
        assert len(data["venues"]["drift"]) == 1

    def test_empty_market(self, app_client):
        resp = app_client.get("/api/v1/data/funding?market=NONEXIST")
        data = resp.json()
        assert data["count"] == 0
        assert data["venues"] == {}


# ─── Edge Cases ──────────────────────────────────────


class TestEdgeCases:
    def test_download_zero_length_range(self, app_client):
        resp = app_client.post("/api/v1/data/download", json={
            "market": "SOL-PERP", "resolution_s": 3600,
            "start_ts": 1700000000, "end_ts": 1700000000,
        })
        assert resp.status_code == 400

    def test_download_missing_params(self, app_client):
        resp = app_client.post("/api/v1/data/download", json={
            "market": "SOL-PERP", "resolution_s": 3600,
        })
        assert resp.status_code == 400

    def test_ohlcv_empty_market(self, app_client):
        resp = app_client.get("/api/v1/data/ohlcv?market=NONEXIST&resolution_s=3600")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
