"""Tests for the multi-source backfill fallback chain.

`backfill_candle_gap` walks Hyperliquid → Drift API → Drift S3 so a
resumed paper session catches up even when one source is offline (Drift
is offline post-hack; HL is the primary live source). These tests pin
that chain order + that any source returning rows short-circuits.
"""
from unittest.mock import MagicMock, patch

from flint.models import Candle
from flint.paper.engine import backfill_candle_gap


def _candle(ts: int) -> Candle:
    return Candle(
        market="SOL-PERP", resolution_s=3600, ts=ts,
        open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0,
    )


def _store_with_upsert():
    store = MagicMock()
    store.upsert_candles.side_effect = lambda candles: len(candles)
    return store


def test_short_window_skips_backfill():
    """Gap < 1 hour returns 0 without any provider call."""
    store = _store_with_upsert()
    n = backfill_candle_gap(store, "SOL-PERP", 1_700_000_000, 1_700_001_000)
    assert n == 0
    store.upsert_candles.assert_not_called()


def test_hyperliquid_first_wins():
    """When HL returns candles, neither Drift source is touched."""
    store = _store_with_upsert()

    hl_provider = MagicMock()
    hl_provider.fetch_candles.return_value = [_candle(1_700_000_000)]
    drift_api = MagicMock()
    drift_s3 = MagicMock()

    with patch("flint.providers.hyperliquid_candles.HyperliquidCandleProvider",
               return_value=hl_provider), \
         patch("flint.providers.drift_candles.DriftCandleProvider",
               return_value=drift_api), \
         patch("flint.providers.drift_s3.DriftS3Provider",
               return_value=drift_s3):
        n = backfill_candle_gap(
            store, "SOL-PERP", 1_700_000_000, 1_700_010_000,
        )

    assert n == 1
    hl_provider.fetch_candles.assert_called_once()
    drift_api.fetch_candles.assert_not_called()
    drift_s3.fetch_candles.assert_not_called()
    hl_provider.close.assert_called_once()


def test_falls_to_drift_api_when_hl_empty():
    """HL returning [] (e.g. unsupported market) falls to Drift API."""
    store = _store_with_upsert()

    hl_provider = MagicMock()
    hl_provider.fetch_candles.return_value = []
    drift_api = MagicMock()
    drift_api.fetch_candles.return_value = [_candle(1_700_000_000)]
    drift_s3 = MagicMock()

    with patch("flint.providers.hyperliquid_candles.HyperliquidCandleProvider",
               return_value=hl_provider), \
         patch("flint.providers.drift_candles.DriftCandleProvider",
               return_value=drift_api), \
         patch("flint.providers.drift_s3.DriftS3Provider",
               return_value=drift_s3):
        n = backfill_candle_gap(
            store, "SOL-PERP", 1_700_000_000, 1_700_010_000,
        )

    assert n == 1
    drift_api.fetch_candles.assert_called_once()
    drift_s3.fetch_candles.assert_not_called()


def test_falls_to_s3_when_hl_and_api_fail():
    """Both HL and Drift API throwing → S3 picks up the gap."""
    store = _store_with_upsert()

    hl_provider = MagicMock()
    hl_provider.fetch_candles.side_effect = RuntimeError("hl offline")
    drift_api = MagicMock()
    drift_api.fetch_candles.side_effect = RuntimeError("drift api down")
    drift_s3 = MagicMock()
    drift_s3.fetch_candles.return_value = [_candle(1_700_000_000)]

    with patch("flint.providers.hyperliquid_candles.HyperliquidCandleProvider",
               return_value=hl_provider), \
         patch("flint.providers.drift_candles.DriftCandleProvider",
               return_value=drift_api), \
         patch("flint.providers.drift_s3.DriftS3Provider",
               return_value=drift_s3):
        n = backfill_candle_gap(
            store, "SOL-PERP", 1_700_000_000, 1_700_010_000,
        )

    assert n == 1
    drift_s3.fetch_candles.assert_called_once()


def test_returns_zero_when_all_sources_fail():
    store = _store_with_upsert()

    hl_provider = MagicMock()
    hl_provider.fetch_candles.side_effect = RuntimeError("hl down")
    drift_api = MagicMock()
    drift_api.fetch_candles.side_effect = RuntimeError("api down")
    drift_s3 = MagicMock()
    drift_s3.fetch_candles.side_effect = RuntimeError("s3 down")

    with patch("flint.providers.hyperliquid_candles.HyperliquidCandleProvider",
               return_value=hl_provider), \
         patch("flint.providers.drift_candles.DriftCandleProvider",
               return_value=drift_api), \
         patch("flint.providers.drift_s3.DriftS3Provider",
               return_value=drift_s3):
        n = backfill_candle_gap(
            store, "SOL-PERP", 1_700_000_000, 1_700_010_000,
        )
    assert n == 0
    store.upsert_candles.assert_not_called()


def test_unsupported_market_skips_hl():
    """Markets not in the HL map skip directly to Drift, even if HL
    is reachable."""
    store = _store_with_upsert()

    hl_provider = MagicMock()
    drift_api = MagicMock()
    drift_api.fetch_candles.return_value = [_candle(1_700_000_000)]
    drift_s3 = MagicMock()

    with patch("flint.providers.hyperliquid_candles.HyperliquidCandleProvider",
               return_value=hl_provider), \
         patch("flint.providers.drift_candles.DriftCandleProvider",
               return_value=drift_api), \
         patch("flint.providers.drift_s3.DriftS3Provider",
               return_value=drift_s3):
        n = backfill_candle_gap(
            store, "OBSCURE-PERP", 1_700_000_000, 1_700_010_000,
        )

    assert n == 1
    hl_provider.fetch_candles.assert_not_called()
    drift_api.fetch_candles.assert_called_once()
