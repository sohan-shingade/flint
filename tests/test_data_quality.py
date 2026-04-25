"""Tests for data quality checks and Parquet export."""
from __future__ import annotations

import tempfile
from pathlib import Path

from flint.data.quality import check_candle_quality
from flint.models import Candle
from flint.store import FlintStore


def _c(ts, close, market="SOL-PERP"):
    return Candle(ts=ts, open=close, high=close + 1, low=close - 1,
                  close=close, volume=100, market=market, resolution_s=3600)


class TestDataQuality:
    def test_empty_candles(self):
        report = check_candle_quality([], 3600)
        assert report.total_candles == 0

    def test_complete_data(self):
        candles = [_c(i * 3600, 100 + i) for i in range(24)]
        report = check_candle_quality(candles, 3600)
        assert report.total_candles == 24
        assert report.completeness_pct > 95
        assert len(report.gaps) == 0
        assert report.duplicates == 0

    def test_detects_gaps(self):
        candles = [
            _c(0, 100), _c(3600, 101), _c(7200, 102),
            # gap: missing 10800, 14400
            _c(18000, 105), _c(21600, 106),
        ]
        report = check_candle_quality(candles, 3600)
        assert len(report.gaps) >= 1
        assert report.completeness_pct < 100

    def test_detects_duplicates(self):
        candles = [_c(0, 100), _c(3600, 101), _c(3600, 101.5)]
        report = check_candle_quality(candles, 3600)
        assert report.duplicates == 1

    def test_detects_outliers(self):
        # Normal data then a huge spike
        candles = [_c(i * 3600, 100 + i * 0.1) for i in range(50)]
        # Add outlier: price jumps 50%
        candles.append(_c(50 * 3600, 175))
        report = check_candle_quality(candles, 3600, outlier_std_threshold=3.0)
        assert len(report.outliers) >= 1


class TestParquetExport:
    def test_export_and_import(self):
        store = FlintStore(":memory:")
        candles = [_c(i * 3600, 100 + i) for i in range(10)]
        store.upsert_candles(candles)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "candles.parquet")
            from flint.data.export import export_table_parquet, import_parquet

            count = export_table_parquet(store, "candles", path)
            assert count == 10
            assert Path(path).exists()

            # Import into a fresh store
            store2 = FlintStore(":memory:")
            imported = import_parquet(store2, path, "candles")
            assert imported == 10

            result = store2.query_candles("SOL-PERP", 3600)
            assert len(result) == 10
            store2.close()
        store.close()
