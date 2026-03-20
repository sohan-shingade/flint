"""Tests for the data collector service."""
import pytest
from unittest.mock import patch, MagicMock

from flint.collector.service import CollectorService
from flint.collector.tasks import CollectorConfig, collect_oracle_prices
from flint.store import FlintStore


@pytest.fixture
def store():
    s = FlintStore(":memory:")
    yield s
    s.close()


def test_collector_config_defaults():
    config = CollectorConfig()
    assert "SOL-PERP" in config.markets
    assert config.candle_backfill_days == 90


def test_collector_service_init(store):
    service = CollectorService(store)
    assert service.store is store
    assert len(service.status) == 0


def test_collector_status_tracking(store):
    service = CollectorService(store)
    service.update_status("SOL-PERP", "candles", "collecting")
    s = service.get_status()
    assert any(st["market"] == "SOL-PERP" and st["data_type"] == "candles" for st in s)


def test_collector_status_error(store):
    service = CollectorService(store)
    service.update_status("SOL-PERP", "candles", "error", error_message="API timeout")
    s = service.get_status()
    entry = next(st for st in s if st["market"] == "SOL-PERP" and st["data_type"] == "candles")
    assert entry["state"] == "error"
    assert entry["error_message"] == "API timeout"


def test_collect_oracle_prices_success(store):
    """Oracle price collection should write to store on success (sync function)."""
    mock_provider = MagicMock()
    mock_provider.fetch_mid_price = MagicMock(return_value=150.0)
    mock_provider.close = MagicMock()

    with patch("flint.collector.tasks.DriftDataProvider", return_value=mock_provider):
        count = collect_oracle_prices(store, "SOL-PERP")
        assert count >= 1
        prices = store.query_oracle_prices("SOL-PERP")
        assert len(prices) >= 1
        assert prices[0].price == 150.0


def test_needs_backfill_empty_db(store):
    """Service should detect empty database needs backfill."""
    service = CollectorService(store)
    assert service._needs_backfill() is True
