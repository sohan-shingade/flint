import os
import tempfile
from unittest.mock import MagicMock, patch

from flint.models import Candle
from flint.store import FlintStore
from flint.migration import run_pyth_migration


def _make_store():
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    return FlintStore(path), path


def test_migration_downloads_pyth_for_existing_markets():
    store, path = _make_store()
    try:
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift"),
            Candle(1700003600, 100.5, 102.0, 100.0, 101.0, 1100.0, "SOL-PERP", 3600, "drift"),
        ])
        mock_pyth = [
            Candle(1700000000, 100.1, 101.1, 99.1, 100.6, 1000.0, "SOL-PERP", 3600, "pyth"),
            Candle(1700003600, 100.6, 102.1, 100.1, 101.1, 1100.0, "SOL-PERP", 3600, "pyth"),
        ]
        with patch("flint.migration.PythCandleProvider") as Mock:
            Mock.return_value.fetch_candles.return_value = mock_pyth
            Mock.return_value.close = MagicMock()
            result = run_pyth_migration(store)
        assert result["markets_migrated"] == ["SOL-PERP"]
        assert result["candles_downloaded"] == 2
        pyth = store.query_candles("SOL-PERP", 3600, venue="pyth")
        assert len(pyth) == 2
    finally:
        store.close()
        os.unlink(path)


def test_migration_skips_already_migrated():
    store, path = _make_store()
    try:
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "pyth"),
        ])
        result = run_pyth_migration(store)
        assert result["markets_migrated"] == []
        assert result["candles_downloaded"] == 0
    finally:
        store.close()
        os.unlink(path)


def test_migration_handles_pyth_failure():
    store, path = _make_store()
    try:
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift"),
        ])
        with patch("flint.migration.PythCandleProvider") as Mock:
            Mock.return_value.fetch_candles.side_effect = Exception("Pyth API down")
            Mock.return_value.close = MagicMock()
            result = run_pyth_migration(store)
        assert result["errors"] == ["SOL-PERP: Pyth API down"]
        assert result["markets_migrated"] == []
    finally:
        store.close()
        os.unlink(path)


def test_migration_is_idempotent():
    store, path = _make_store()
    try:
        store.upsert_candles([
            Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift"),
        ])
        mock_pyth = [Candle(1700000000, 100.1, 101.1, 99.1, 100.6, 1000.0, "SOL-PERP", 3600, "pyth")]
        with patch("flint.migration.PythCandleProvider") as Mock:
            Mock.return_value.fetch_candles.return_value = mock_pyth
            Mock.return_value.close = MagicMock()
            run_pyth_migration(store)
            result = run_pyth_migration(store)
        assert result["markets_migrated"] == []
    finally:
        store.close()
        os.unlink(path)
