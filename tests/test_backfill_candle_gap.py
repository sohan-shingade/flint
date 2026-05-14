"""Tests for the multi-source backfill fallback chain.

`backfill_candle_gap` tries Hyperliquid only (Drift was removed post-hack).
If HL fails or returns empty, the function returns 0 and the caller falls
back to the DuckDB cache (already queried after this function returns).
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


def test_hyperliquid_succeeds():
    """When HL returns candles, they are upserted."""
    store = _store_with_upsert()

    hl_provider = MagicMock()
    hl_provider.fetch_candles.return_value = [_candle(1_700_000_000)]

    with patch("flint.providers.hyperliquid_candles.HyperliquidCandleProvider",
               return_value=hl_provider):
        n = backfill_candle_gap(
            store, "SOL-PERP", 1_700_000_000, 1_700_010_000,
        )

    assert n == 1
    hl_provider.fetch_candles.assert_called_once()
    hl_provider.close.assert_called_once()


def test_returns_zero_when_hl_empty():
    """HL returning [] means no data — function returns 0."""
    store = _store_with_upsert()

    hl_provider = MagicMock()
    hl_provider.fetch_candles.return_value = []

    with patch("flint.providers.hyperliquid_candles.HyperliquidCandleProvider",
               return_value=hl_provider):
        n = backfill_candle_gap(
            store, "SOL-PERP", 1_700_000_000, 1_700_010_000,
        )

    assert n == 0
    store.upsert_candles.assert_not_called()


def test_returns_zero_when_hl_fails():
    """HL throwing an exception — function returns 0."""
    store = _store_with_upsert()

    hl_provider = MagicMock()
    hl_provider.fetch_candles.side_effect = RuntimeError("hl down")

    with patch("flint.providers.hyperliquid_candles.HyperliquidCandleProvider",
               return_value=hl_provider):
        n = backfill_candle_gap(
            store, "SOL-PERP", 1_700_000_000, 1_700_010_000,
        )

    assert n == 0
    store.upsert_candles.assert_not_called()


def test_unsupported_market_returns_zero():
    """Markets not in the HL map return 0 (no Drift fallback anymore)."""
    store = _store_with_upsert()

    hl_provider = MagicMock()

    with patch("flint.providers.hyperliquid_candles.HyperliquidCandleProvider",
               return_value=hl_provider):
        n = backfill_candle_gap(
            store, "OBSCURE-PERP", 1_700_000_000, 1_700_010_000,
        )

    assert n == 0
    hl_provider.fetch_candles.assert_not_called()
