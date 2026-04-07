"""Tests for LiveJupiterContext."""
from unittest.mock import MagicMock, patch

from flint.execution.jupiter_live import LiveJupiterContext
from flint.models import Side


def _mock_response(data, status_code=200):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


def test_get_mark_price():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    with patch.object(ctx, "_http") as mock:
        mock.get.return_value = _mock_response({"price": 150.25})
        assert ctx.get_mark_price("SOL-PERP") == 150.25
        mock.get.assert_called_once_with(
            "http://127.0.0.1:8401/oracle/SOL-PERP", timeout=10)


def test_get_positions():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    with patch.object(ctx, "_http") as mock:
        mock.get.return_value = _mock_response({
            "positions": [
                {"market": "SOL-PERP", "side": "long", "size": 10.0,
                 "entry_price": 148.0, "unrealized_pnl": 22.5, "venue": "jupiter"},
            ]
        })
        positions = ctx.get_positions()
        assert len(positions) == 1
        assert positions[0].market == "SOL-PERP"
        assert positions[0].side == Side.LONG
        assert positions[0].size == 10.0
        assert positions[0].entry_price == 148.0
        assert positions[0].unrealized_pnl == 22.5
        assert positions[0].venue == "jupiter"


def test_get_positions_short():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    with patch.object(ctx, "_http") as mock:
        mock.get.return_value = _mock_response({
            "positions": [
                {"market": "BTC-PERP", "side": "short", "size": 0.5,
                 "entry_price": 65000.0},
            ]
        })
        positions = ctx.get_positions()
        assert positions[0].side == Side.SHORT
        assert positions[0].unrealized_pnl == 0.0  # default


def test_get_positions_empty():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    with patch.object(ctx, "_http") as mock:
        mock.get.return_value = _mock_response({"positions": []})
        assert ctx.get_positions() == []


def test_get_balances():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    with patch.object(ctx, "_http") as mock:
        mock.get.return_value = _mock_response({"usdc": 10000.0, "sol": 5.0})
        balances = ctx.get_balances()
        assert balances["usdc"] == 10000.0


def test_market_order_returns_pending():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    with patch.object(ctx, "_http") as mock:
        mock.post.return_value = _mock_response(
            {"request_id": "abc123", "tx_signature": "5xYz", "status": "pending"})
        result = ctx.market_order("SOL-PERP", "buy", 5.0)
        assert result == "abc123"
        mock.post.assert_called_once_with(
            "http://127.0.0.1:8401/increase",
            json={"market": "SOL-PERP", "side": "buy", "size": 5.0},
            timeout=30)


def test_market_order_sell_uses_decrease():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    with patch.object(ctx, "_http") as mock:
        mock.post.return_value = _mock_response({"request_id": "xyz789"})
        ctx.market_order("SOL-PERP", "sell", 3.0)
        call_args = mock.post.call_args
        assert "/decrease" in call_args[0][0]


def test_get_borrow_rate():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    mock_store = MagicMock()
    mock_store.query_borrow_rates.return_value = [
        MagicMock(rate_hourly=0.00008, ts=1000)]
    ctx._store = mock_store
    assert ctx.get_borrow_rate("SOL-PERP") == 0.00008


def test_get_borrow_rate_no_store():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    assert ctx.get_borrow_rate("SOL-PERP") is None


def test_get_borrow_rate_no_market():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    ctx._store = MagicMock()
    assert ctx.get_borrow_rate(None) is None


def test_get_borrow_rate_empty_history():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    mock_store = MagicMock()
    mock_store.query_borrow_rates.return_value = []
    ctx._store = mock_store
    assert ctx.get_borrow_rate("SOL-PERP") is None


def test_get_borrow_rates():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    mock_store = MagicMock()
    mock_store.query_borrow_rates.return_value = [
        MagicMock(ts=1000, rate_hourly=0.00008),
        MagicMock(ts=2000, rate_hourly=0.00009),
    ]
    ctx._store = mock_store
    result = ctx.get_borrow_rates("SOL-PERP", lookback=24)
    assert len(result) == 2
    assert result[0] == (1000, 0.00008)
    assert result[1] == (2000, 0.00009)


def test_get_borrow_rates_no_store():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    assert ctx.get_borrow_rates("SOL-PERP") == []


def test_collateral_validation():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    assert ctx._validate_collateral("SOL-PERP", "buy") is True
    assert ctx._validate_collateral("BTC-PERP", "sell") is True


def test_sidecar_url_trailing_slash_stripped():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401/")
    assert ctx._sidecar_url == "http://127.0.0.1:8401"


def test_close():
    ctx = LiveJupiterContext(sidecar_url="http://127.0.0.1:8401")
    with patch.object(ctx._http, "close") as mock_close:
        ctx.close()
        mock_close.assert_called_once()
