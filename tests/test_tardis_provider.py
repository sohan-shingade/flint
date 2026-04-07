"""Tests for the Tardis.dev orderbook snapshot provider."""
from flint.providers.tardis import TardisOrderbookProvider

MOCK_CSV = """timestamp,local_timestamp,ask_price_0,ask_amount_0,ask_price_1,ask_amount_1,bid_price_0,bid_amount_0,bid_price_1,bid_amount_1
1700000000000000,1700000000100000,100.1,50.0,100.2,100.0,99.9,60.0,99.8,120.0
1700000300000000,1700000300100000,100.15,55.0,100.25,95.0,99.85,65.0,99.75,115.0
"""


def test_parse_csv_row():
    p = TardisOrderbookProvider(api_key="fake")
    s = p._parse_csv(MOCK_CSV, "binance", "SOL-PERP", num_levels=2)
    assert len(s) == 2
    assert s[0]["venue"] == "binance"
    assert s[0]["ask_prices"][0] == 100.1
    assert s[0]["bid_prices"][0] == 99.9


def test_parse_csv_second_row():
    p = TardisOrderbookProvider(api_key="fake")
    s = p._parse_csv(MOCK_CSV, "binance", "SOL-PERP", num_levels=2)
    assert s[1]["ask_prices"][0] == 100.15
    assert s[1]["bid_prices"][0] == 99.85
    assert s[1]["market"] == "SOL-PERP"


def test_is_available():
    assert TardisOrderbookProvider(api_key="").is_available() is False
    assert TardisOrderbookProvider(api_key="td_test").is_available() is True


def test_timestamp_aligned_to_5min():
    p = TardisOrderbookProvider(api_key="fake")
    # 1700000123 % 300 = 23  →  aligned = 1700000100
    assert p._align_ts(1700000123) == 1700000100
    # Already on a 5-min boundary (1700000100 + 300 = 1700000400; 1700000400 % 300 = 0)
    assert p._align_ts(1700000400) == 1700000400
    # 1700000699 % 300 = 299  →  aligned = 1700000400
    assert p._align_ts(1700000699) == 1700000400


def test_parse_csv_deduplicates_same_bucket():
    """Two rows in the same 5-min bucket → only the first is kept."""
    csv_same_bucket = """timestamp,local_timestamp,ask_price_0,ask_amount_0,bid_price_0,bid_amount_0
1700000001000000,1700000001100000,101.0,10.0,100.0,10.0
1700000060000000,1700000060100000,102.0,10.0,101.0,10.0
"""
    p = TardisOrderbookProvider(api_key="fake")
    s = p._parse_csv(csv_same_bucket, "binance", "SOL-PERP", num_levels=1)
    assert len(s) == 1
    assert s[0]["ask_prices"][0] == 101.0


def test_fetch_returns_empty_when_no_key():
    p = TardisOrderbookProvider(api_key="")
    result = p.fetch("binance", "SOL-PERP", "2024/01/15")
    assert result == []


def test_fetch_returns_empty_for_unknown_venue():
    import httpx

    class _MockClient:
        def get(self, url, **kwargs):
            raise AssertionError("should not be called")

    p = TardisOrderbookProvider(api_key="td_test", client=_MockClient())
    result = p.fetch("unknown_venue", "SOL-PERP", "2024/01/15")
    assert result == []


def test_fetch_returns_empty_for_unknown_market():
    class _MockClient:
        def get(self, url, **kwargs):
            raise AssertionError("should not be called")

    p = TardisOrderbookProvider(api_key="td_test", client=_MockClient())
    result = p.fetch("binance", "UNKNOWN-PERP", "2024/01/15")
    assert result == []


def test_fetch_handles_http_error_gracefully():
    import httpx

    class _MockResponse:
        status_code = 403
        content = b""

        def raise_for_status(self):
            raise httpx.HTTPStatusError("403", request=None, response=None)

    class _MockClient:
        def get(self, url, **kwargs):
            return _MockResponse()

    p = TardisOrderbookProvider(api_key="td_test", client=_MockClient())
    result = p.fetch("binance", "SOL-PERP", "2024/01/15")
    assert result == []


def test_fetch_returns_parsed_snapshots_on_success():
    import gzip

    csv_content = (
        "timestamp,local_timestamp,ask_price_0,ask_amount_0,bid_price_0,bid_amount_0\n"
        "1700000000000000,1700000000100000,100.5,30.0,100.0,40.0\n"
    )
    compressed = gzip.compress(csv_content.encode("utf-8"))

    class _MockResponse:
        status_code = 200
        content = compressed

        def raise_for_status(self):
            pass

    class _MockClient:
        def get(self, url, **kwargs):
            return _MockResponse()

    p = TardisOrderbookProvider(api_key="td_test", client=_MockClient())
    result = p.fetch("binance", "SOL-PERP", "2024/01/15")
    assert len(result) == 1
    assert result[0]["venue"] == "binance"
    assert result[0]["market"] == "SOL-PERP"
    assert result[0]["ask_prices"][0] == 100.5
    assert result[0]["bid_prices"][0] == 100.0


def test_parse_csv_skips_empty_price_fields():
    csv_partial = (
        "timestamp,local_timestamp,"
        "ask_price_0,ask_amount_0,ask_price_1,ask_amount_1,"
        "bid_price_0,bid_amount_0,bid_price_1,bid_amount_1\n"
        "1700000000000000,1700000000100000,100.1,50.0,,,"
        "99.9,60.0,,\n"
    )
    p = TardisOrderbookProvider(api_key="fake")
    s = p._parse_csv(csv_partial, "binance", "SOL-PERP", num_levels=2)
    assert len(s) == 1
    assert len(s[0]["ask_prices"]) == 1
    assert len(s[0]["bid_prices"]) == 1
