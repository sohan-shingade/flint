"""Tests for the _download_init_candles helper in flint/cli.py."""
from unittest.mock import MagicMock, patch
from flint.models import Candle


def test_init_uses_pyth_provider():
    mock_candles = [
        Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "pyth"),
    ]
    with patch("flint.cli.PythCandleProvider") as MockProvider:
        instance = MockProvider.return_value
        instance.fetch_candles.return_value = mock_candles
        instance.close = MagicMock()
        from flint.cli import _download_init_candles
        candles = _download_init_candles("SOL-PERP", 3600, 1700000000, 1700003600)
        assert len(candles) == 1
        assert candles[0].venue == "pyth"


def test_init_falls_back_to_drift():
    with patch("flint.cli.PythCandleProvider") as MockPyth:
        pyth_instance = MockPyth.return_value
        pyth_instance.fetch_candles.return_value = []
        pyth_instance.close = MagicMock()

        drift_candles = [Candle(1700000000, 100.0, 101.0, 99.0, 100.5, 1000.0, "SOL-PERP", 3600, "drift")]
        with patch("flint.cli.DriftCandleProvider") as MockDrift:
            drift_instance = MockDrift.return_value
            drift_instance.fetch_candles.return_value = drift_candles
            drift_instance.close = MagicMock()
            from flint.cli import _download_init_candles
            candles = _download_init_candles("SOL-PERP", 3600, 1700000000, 1700003600)
            assert len(candles) == 1
