"""Tests for CustomCSVProvider + CustomParquetProvider — Phase 1 T1.6."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from flint.providers.custom import (
    CustomCSVProvider,
    CustomDataValidationError,
    CustomParquetProvider,
    compute_source_hash,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_candles_csv(path: Path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_epoch_s", "open", "high", "low", "close",
                    "volume", "market", "resolution_s"])
        w.writerows(rows)


def _write_funding_csv(path: Path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_epoch_s", "market", "venue", "rate", "interval_s"])
        w.writerows(rows)


class _InMemStore:
    """Minimal stand-in for FlintStore."""

    def __init__(self):
        self.candles = []
        self.funding = []

    def upsert_candles(self, cs):
        self.candles.extend(cs)
        return len(cs)

    def upsert_funding_rates(self, rs):
        self.funding.extend(rs)
        return len(rs)


# ---------------------------------------------------------------------------
# Source hash
# ---------------------------------------------------------------------------

def test_source_hash_is_deterministic(tmp_path):
    p = tmp_path / "a.csv"
    p.write_bytes(b"hello world")
    h1 = compute_source_hash(p)
    h2 = compute_source_hash(p)
    assert h1 == h2
    assert len(h1) == 64


def test_source_hash_changes_on_content_change(tmp_path):
    p = tmp_path / "a.csv"
    p.write_bytes(b"hello world")
    h1 = compute_source_hash(p)
    p.write_bytes(b"hello, world")
    h2 = compute_source_hash(p)
    assert h1 != h2


# ---------------------------------------------------------------------------
# Candles happy path
# ---------------------------------------------------------------------------

def test_load_valid_candles(tmp_path):
    path = tmp_path / "btc.csv"
    _write_candles_csv(path, [
        [1700000000, 100, 101, 99, 100.5, 1000, "custom:BTC-SPOT", 3600],
        [1700003600, 100.5, 102, 100, 101.5, 1100, "custom:BTC-SPOT", 3600],
        [1700007200, 101.5, 103, 101, 102.5, 1200, "custom:BTC-SPOT", 3600],
    ])
    store = _InMemStore()
    provider = CustomCSVProvider(path=path, table="candles",
                                 markets=["custom:BTC-SPOT"])
    result = provider.load_and_upsert(store)
    assert result.rows_loaded == 3
    assert result.source_hash == provider.source_hash
    assert len(result.source_hash) == 64
    assert result.markets == ["custom:BTC-SPOT"]
    assert result.table == "candles"
    assert len(store.candles) == 3
    assert store.candles[0].venue == "custom"


# ---------------------------------------------------------------------------
# Market namespace enforcement
# ---------------------------------------------------------------------------

def test_rejects_non_custom_market_prefix(tmp_path):
    path = tmp_path / "bad.csv"
    _write_candles_csv(path, [
        [1700000000, 100, 101, 99, 100.5, 1000, "SOL-PERP", 3600],
    ])
    with pytest.raises(CustomDataValidationError, match="custom:"):
        CustomCSVProvider(path=path, table="candles",
                          markets=["SOL-PERP"])


def test_rejects_market_not_in_declared_list(tmp_path):
    path = tmp_path / "mismatch.csv"
    _write_candles_csv(path, [
        [1700000000, 100, 101, 99, 100.5, 1000, "custom:ETH-SPOT", 3600],
    ])
    store = _InMemStore()
    provider = CustomCSVProvider(path=path, table="candles",
                                 markets=["custom:BTC-SPOT"])
    with pytest.raises(CustomDataValidationError, match="not in declared"):
        provider.load_and_upsert(store)


# ---------------------------------------------------------------------------
# OHLCV sanity
# ---------------------------------------------------------------------------

def test_rejects_impossible_high(tmp_path):
    path = tmp_path / "bad_ohlc.csv"
    _write_candles_csv(path, [
        # high < close
        [1700000000, 100, 99, 99, 101, 1000, "custom:X", 3600],
    ])
    store = _InMemStore()
    provider = CustomCSVProvider(path=path, table="candles",
                                 markets=["custom:X"])
    with pytest.raises(CustomDataValidationError, match="high="):
        provider.load_and_upsert(store)


def test_rejects_negative_volume(tmp_path):
    path = tmp_path / "bad_vol.csv"
    _write_candles_csv(path, [
        [1700000000, 100, 101, 99, 100.5, -10, "custom:X", 3600],
    ])
    store = _InMemStore()
    provider = CustomCSVProvider(path=path, table="candles",
                                 markets=["custom:X"])
    with pytest.raises(CustomDataValidationError, match="volume"):
        provider.load_and_upsert(store)


# ---------------------------------------------------------------------------
# Monotonic / resolution
# ---------------------------------------------------------------------------

def test_rejects_non_monotonic_timestamps(tmp_path):
    path = tmp_path / "nonmonotonic.csv"
    _write_candles_csv(path, [
        [1700000000, 100, 101, 99, 100.5, 1000, "custom:X", 3600],
        [1700000000, 101, 102, 100, 101.5, 1100, "custom:X", 3600],  # duplicate ts
    ])
    store = _InMemStore()
    provider = CustomCSVProvider(path=path, table="candles",
                                 markets=["custom:X"])
    with pytest.raises(CustomDataValidationError, match="non-monotonic"):
        provider.load_and_upsert(store)


def test_rejects_resolution_mismatch(tmp_path):
    path = tmp_path / "badres.csv"
    _write_candles_csv(path, [
        [1700000000, 100, 101, 99, 100.5, 1000, "custom:X", 3600],
        [1700001800, 100, 101, 99, 100.5, 1000, "custom:X", 3600],  # 1800s gap, declared 3600
    ])
    store = _InMemStore()
    provider = CustomCSVProvider(path=path, table="candles",
                                 markets=["custom:X"])
    with pytest.raises(CustomDataValidationError, match="resolution mismatch"):
        provider.load_and_upsert(store)


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------

def test_load_valid_funding(tmp_path):
    path = tmp_path / "funding.csv"
    _write_funding_csv(path, [
        [1700000000, "custom:BTC-PERP", "myvenue", 0.0001, 3600],
        [1700003600, "custom:BTC-PERP", "myvenue", 0.00012, 3600],
    ])
    store = _InMemStore()
    provider = CustomCSVProvider(path=path, table="funding",
                                 markets=["custom:BTC-PERP"])
    result = provider.load_and_upsert(store)
    assert result.rows_loaded == 2
    assert len(store.funding) == 2
    assert store.funding[0].rate == pytest.approx(0.0001)


def test_rejects_funding_rate_out_of_range(tmp_path):
    path = tmp_path / "bad_funding.csv"
    _write_funding_csv(path, [
        [1700000000, "custom:X", "v", 1.5, 3600],  # unit error
    ])
    store = _InMemStore()
    provider = CustomCSVProvider(path=path, table="funding",
                                 markets=["custom:X"])
    with pytest.raises(CustomDataValidationError, match="out of"):
        provider.load_and_upsert(store)


# ---------------------------------------------------------------------------
# Parquet
# ---------------------------------------------------------------------------

def test_load_fills_csv(tmp_path):
    """D-1.6 — fill-log ingest returns parsed records."""
    import csv as _csv

    path = tmp_path / "fills.csv"
    with open(path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["ts_epoch_s", "market", "venue", "side", "size",
                    "price", "fee", "mid_price", "realized_slippage_bps"])
        w.writerow([1700000000, "custom:SOL-PERP", "drift", "long",
                    10.0, 150.05, 0.05, 150.00, 3.33])
        w.writerow([1700003600, "custom:SOL-PERP", "drift", "short",
                    10.0, 149.90, 0.05, 150.00, 6.67])

    store = _InMemStore()
    provider = CustomCSVProvider(path=path, table="fills",
                                 markets=["custom:SOL-PERP"])
    result = provider.load_and_upsert(store)
    assert result.rows_loaded == 2
    assert result.fills is not None
    assert len(result.fills) == 2
    assert result.fills[0]["side"] == "long"
    assert result.fills[0]["realized_slippage_bps"] == 3.33
    assert result.fills[1]["side"] == "short"


def test_fills_buy_normalized_to_long(tmp_path):
    """buy/sell → long/short normalization."""
    import csv as _csv
    path = tmp_path / "fills.csv"
    with open(path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["ts_epoch_s", "market", "venue", "side", "size", "price"])
        w.writerow([1700000000, "custom:X", "v", "buy", 1.0, 100.0])
        w.writerow([1700003600, "custom:X", "v", "sell", 1.0, 100.0])
    provider = CustomCSVProvider(path=path, table="fills", markets=["custom:X"])
    result = provider.load_and_upsert(_InMemStore())
    assert [f["side"] for f in result.fills] == ["long", "short"]


def test_fills_negative_fee_rejected(tmp_path):
    import csv as _csv
    path = tmp_path / "fills.csv"
    with open(path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["ts_epoch_s", "market", "venue", "side", "size", "price", "fee"])
        w.writerow([1700000000, "custom:X", "v", "long", 1.0, 100.0, -0.5])
    provider = CustomCSVProvider(path=path, table="fills", markets=["custom:X"])
    with pytest.raises(CustomDataValidationError, match="fee"):
        provider.load_and_upsert(_InMemStore())


def test_load_valid_parquet_candles(tmp_path):
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "btc.parquet"
    tbl = pa.table({
        "ts_epoch_s": [1700000000, 1700003600, 1700007200],
        "open": [100.0, 100.5, 101.5],
        "high": [101.0, 102.0, 103.0],
        "low": [99.0, 100.0, 101.0],
        "close": [100.5, 101.5, 102.5],
        "volume": [1000.0, 1100.0, 1200.0],
        "market": ["custom:BTC-SPOT"] * 3,
        "resolution_s": [3600, 3600, 3600],
    })
    pq.write_table(tbl, path)

    store = _InMemStore()
    provider = CustomParquetProvider(path=path, table="candles",
                                     markets=["custom:BTC-SPOT"])
    result = provider.load_and_upsert(store)
    assert result.rows_loaded == 3
    assert len(result.source_hash) == 64
