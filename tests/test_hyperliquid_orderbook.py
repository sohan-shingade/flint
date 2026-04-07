"""Tests for the Hyperliquid public S3 orderbook snapshot provider."""
import json

from flint.providers.hyperliquid_orderbook import HyperliquidOrderbookProvider


def test_is_available():
    assert HyperliquidOrderbookProvider().is_available() is True


def test_build_s3_url():
    p = HyperliquidOrderbookProvider()
    url = p._build_url("2024", "06", "01", "12", "SOL")
    assert "hyperliquid-archive" in url
    assert "2024/06/01/12" in url


def test_market_to_coin():
    p = HyperliquidOrderbookProvider()
    assert p._market_to_coin("SOL-PERP") == "SOL"
    assert p._market_to_coin("BTC-PERP") == "BTC"


def test_align_ts_5min():
    p = HyperliquidOrderbookProvider()
    # _align_ts receives unix *seconds* (ms already divided by 1000 by the caller)
    # 1700000123 % 300 = 23  →  aligned = 1700000100
    assert p._align_ts(1700000123) == 1700000100
    # 1700000400 is a clean 5-min boundary (% 300 == 0)
    assert p._align_ts(1700000400) == 1700000400
    # 1700000699 % 300 = 299  →  aligned = 1700000400
    assert p._align_ts(1700000699) == 1700000400


def test_build_url_contains_coin_and_lz4():
    p = HyperliquidOrderbookProvider()
    url = p._build_url("2024", "06", "01", "00", "ETH")
    assert "ETH.lz4" in url
    assert "l2Book" in url


def test_fetch_invalid_date_returns_empty():
    p = HyperliquidOrderbookProvider()
    result = p.fetch("SOL-PERP", "2024-13")  # bad date format
    assert result == []


def test_fetch_skips_404_hours():
    class _MockResponse:
        status_code = 404
        content = b""

        def raise_for_status(self):
            pass

    class _MockClient:
        def get(self, url, **kwargs):
            return _MockResponse()

    p = HyperliquidOrderbookProvider(client=_MockClient())
    result = p.fetch("SOL-PERP", "2024-06-01", hours=[0, 1])
    assert result == []


def test_fetch_returns_empty_when_lz4_missing(monkeypatch):
    """If lz4 is not installed, fetch returns [] gracefully."""
    import sys

    class _MockResponse:
        status_code = 200
        content = b"fake_lz4_bytes"

        def raise_for_status(self):
            pass

    class _MockClient:
        def get(self, url, **kwargs):
            return _MockResponse()

    # Temporarily remove lz4 from sys.modules so ImportError is triggered
    original = sys.modules.get("lz4")
    original_frame = sys.modules.get("lz4.frame")
    sys.modules["lz4"] = None
    sys.modules["lz4.frame"] = None
    try:
        p = HyperliquidOrderbookProvider(client=_MockClient())
        result = p.fetch("SOL-PERP", "2024-06-01", hours=[0])
        assert result == []
    finally:
        if original is None:
            sys.modules.pop("lz4", None)
        else:
            sys.modules["lz4"] = original
        if original_frame is None:
            sys.modules.pop("lz4.frame", None)
        else:
            sys.modules["lz4.frame"] = original_frame


def test_fetch_parses_lz4_lines():
    """Provide real lz4-compressed NDJSON and verify parsed snapshots."""
    try:
        import lz4.frame
    except ImportError:
        import pytest
        pytest.skip("lz4 not installed")

    record = {
        "time": 1700000000000,
        "levels": [
            [{"px": "99.9", "sz": "60.0"}, {"px": "99.8", "sz": "120.0"}],
            [{"px": "100.1", "sz": "50.0"}, {"px": "100.2", "sz": "100.0"}],
        ],
    }
    ndjson = json.dumps(record).encode("utf-8")
    compressed = lz4.frame.compress(ndjson)

    class _MockResponse:
        status_code = 200
        content = compressed

        def raise_for_status(self):
            pass

    class _MockClient:
        def get(self, url, **kwargs):
            return _MockResponse()

    p = HyperliquidOrderbookProvider(client=_MockClient())
    result = p.fetch("SOL-PERP", "2024-06-01", hours=[0])
    assert len(result) == 1
    snap = result[0]
    assert snap["venue"] == "hyperliquid"
    assert snap["market"] == "SOL-PERP"
    assert snap["bid_prices"][0] == 99.9
    assert snap["ask_prices"][0] == 100.1


def test_fetch_skips_malformed_json_lines():
    try:
        import lz4.frame
    except ImportError:
        import pytest
        pytest.skip("lz4 not installed")

    good = json.dumps({
        "time": 1700000000000,
        "levels": [[{"px": "99.0", "sz": "1.0"}], [{"px": "100.0", "sz": "1.0"}]],
    })
    data = (good + "\nNOT_JSON\n").encode("utf-8")
    compressed = lz4.frame.compress(data)

    class _MockResponse:
        status_code = 200
        content = compressed

        def raise_for_status(self):
            pass

    class _MockClient:
        def get(self, url, **kwargs):
            return _MockResponse()

    p = HyperliquidOrderbookProvider(client=_MockClient())
    result = p.fetch("SOL-PERP", "2024-06-01", hours=[0])
    assert len(result) == 1
