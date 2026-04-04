"""Tests for HyperliquidClient — mocked HTTP, no real connections."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

pytest.importorskip("eth_account", reason="eth_account not installed (optional dep)")

from flint.connectors.hyperliquid import HyperliquidClient


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestNetworkConfig:
    def test_testnet_url(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        assert "testnet" in client._base_url
        run(client.close())

    def test_mainnet_url(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="mainnet")
        assert client._base_url == "https://api.hyperliquid.xyz"
        run(client.close())

    def test_address_derived_from_key(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        assert client.address.startswith("0x")
        assert len(client.address) == 42
        run(client.close())


class TestGetMeta:
    def test_get_meta_returns_universe(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "universe": [
                {"name": "BTC", "szDecimals": 5},
                {"name": "ETH", "szDecimals": 4},
                {"name": "SOL", "szDecimals": 2},
            ]
        }
        mock_response.raise_for_status = MagicMock()
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = run(client.get_meta())
        assert "universe" in result
        assert len(result["universe"]) == 3
        run(client.close())

    def test_build_asset_maps(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        meta = {
            "universe": [
                {"name": "BTC", "szDecimals": 5},
                {"name": "ETH", "szDecimals": 4},
                {"name": "SOL", "szDecimals": 2},
            ]
        }
        client._build_asset_maps(meta)
        assert client._coin_to_asset_index["BTC"] == 0
        assert client._coin_to_asset_index["SOL"] == 2
        assert client._asset_index_to_coin[1] == "ETH"
        assert client._asset_info["SOL"]["szDecimals"] == 2
        run(client.close())


class TestGetClearinghouseState:
    def test_returns_parsed_state(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "marginSummary": {"accountValue": "10000.0", "totalMarginUsed": "500.0"},
            "assetPositions": [{"position": {"coin": "SOL", "szi": "10.0", "entryPx": "150.0", "unrealizedPnl": "50.0"}}],
        }
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = run(client.get_clearinghouse_state(client.address))
        assert result["marginSummary"]["accountValue"] == "10000.0"
        assert len(result["assetPositions"]) == 1
        run(client.close())


class TestGetOpenOrders:
    def test_returns_orders_list(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [{"coin": "SOL", "oid": 12345, "side": "B", "sz": "10.0", "limitPx": "150.0"}]
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = run(client.get_open_orders(client.address))
        assert len(result) == 1
        assert result[0]["oid"] == 12345
        run(client.close())


class TestGetUserFills:
    def test_returns_fills_list(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [{"coin": "SOL", "px": "150.0", "sz": "10.0", "fee": "0.05", "time": 1700000000000, "oid": 12345}]
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = run(client.get_user_fills(client.address))
        assert len(result) == 1
        assert result[0]["oid"] == 12345
        run(client.close())


class TestGetCandleSnapshot:
    def test_returns_candles(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [{"t": 1700000000000, "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "1000.0"}]
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = run(client.get_candle_snapshot("SOL", "1m", 1700000000000, 1700003600000))
        assert len(result) == 1
        assert result[0]["o"] == "150.0"
        run(client.close())


class TestGetL2Book:
    def test_returns_book(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "levels": [[{"px": "149.0", "sz": "100.0"}], [{"px": "151.0", "sz": "80.0"}]],
        }
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = run(client.get_l2_book("SOL"))
        assert "levels" in result
        assert len(result["levels"]) == 2
        run(client.close())


class TestPrecisionFormatting:
    def test_format_size(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        client._asset_info = {"SOL": {"szDecimals": 2}, "BTC": {"szDecimals": 5}}
        assert client.format_size("SOL", 10.123456) == "10.12"
        assert client.format_size("BTC", 0.123456789) == "0.12346"
        run(client.close())

    def test_format_price(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        assert client.format_price(150.123456) == "150.123"
        assert client.format_price(65000.5) == "65000.5"
        run(client.close())


class TestEIP712Signing:
    def test_sign_order_action_produces_valid_signature(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        action = {
            "type": "order",
            "orders": [{"a": 2, "b": True, "p": "150.0", "s": "10.0", "r": False, "t": {"limit": {"tif": "Gtc"}}}],
            "grouping": "na",
        }
        signature, nonce = client._sign_action(action)
        assert isinstance(signature, dict)
        assert "r" in signature
        assert "s" in signature
        assert "v" in signature
        assert isinstance(nonce, int)
        run(client.close())

    def test_testnet_chain_id(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        assert client._chain_id == 13337
        run(client.close())

    def test_mainnet_chain_id(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="mainnet")
        assert client._chain_id == 1337
        run(client.close())


class TestPlaceOrder:
    def test_place_order_sends_signed_request(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 99}}]}},
        }
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = run(client.place_order(asset=2, is_buy=True, size="10.0", price="150.0",
                                            order_type={"limit": {"tif": "Gtc"}}))
        assert result["status"] == "ok"
        call_url = mock_post.call_args[0][0]
        assert "/exchange" in call_url
        run(client.close())

    def test_place_order_returns_oid(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 42}}]}},
        }
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = run(client.place_order(asset=2, is_buy=True, size="10.0", price="150.0",
                                            order_type={"limit": {"tif": "Gtc"}}))
        oid = HyperliquidClient.parse_order_id(result)
        assert oid == 42
        run(client.close())


class TestCancelOrder:
    def test_cancel_order_sends_signed_request(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"status": "ok"}
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = run(client.cancel_order(asset=2, oid=42))
        assert result["status"] == "ok"
        call_url = mock_post.call_args[0][0]
        assert "/exchange" in call_url
        run(client.close())


class TestCancelAllOrders:
    def test_cancel_all_sends_request(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"status": "ok"}
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = run(client.cancel_all_orders())
        assert result["status"] == "ok"
        run(client.close())
