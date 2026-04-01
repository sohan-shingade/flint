# Hyperliquid Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Hyperliquid as a second live trading venue — same strategy code deploys to Drift or Hyperliquid with zero changes.

**Architecture:** Standalone REST connector (`connectors/hyperliquid.py`) handles HTTP + EIP-712 signing. `LiveHyperliquidContext` extends `LiveExecutionContext` (same 7 abstract methods as Drift). `HyperliquidWebSocketFeed` extends `WebSocketFeed` for candle/orderbook/fill streaming. `HyperliquidCandleProvider` provides historical data for backtests.

**Tech Stack:** `eth_account` (EIP-712 signing), `httpx` (async HTTP), `websockets` (WS connections), existing `LiveExecutionContext` + `WebSocketFeed` base classes.

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `flint/connectors/hyperliquid.py` | REST client, EIP-712 signing, market metadata | Create |
| `flint/execution/hyperliquid_live.py` | LiveHyperliquidContext — 7 abstract methods | Create |
| `flint/providers/hyperliquid_ws.py` | WebSocket feed — candles, L2 book, order updates | Create |
| `flint/providers/hyperliquid_candles.py` | Historical candle provider for backtests | Create |
| `flint/config.py` | 3 new Hyperliquid config fields | Modify |
| `flint/api/routes/data.py` | Add Hyperliquid to download pipeline | Modify |
| `ROADMAP.md` | Mark Phase 2 sections as implemented | Modify |
| `tests/test_hyperliquid_client.py` | Connector tests | Create |
| `tests/test_hyperliquid_live.py` | Execution context tests | Create |
| `tests/test_hyperliquid_ws.py` | WebSocket feed tests | Create |
| `tests/test_hyperliquid_candles.py` | Candle provider tests | Create |
| `tests/test_hyperliquid_integration.py` | End-to-end integration tests | Create |

---

### Task 1: Config Additions

**Files:**
- Modify: `flint/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
class TestHyperliquidConfig:
    def test_hyperliquid_defaults(self):
        from flint.config import FlintConfig
        config = FlintConfig()
        assert config.live_hyperliquid_network == "testnet"
        assert config.live_hyperliquid_market_order_slippage == 0.003
        assert config.live_hyperliquid_l2_persist_interval_s == 60

    def test_hyperliquid_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_LIVE_HYPERLIQUID_NETWORK", "mainnet")
        monkeypatch.setenv("FLINT_LIVE_HYPERLIQUID_MARKET_ORDER_SLIPPAGE", "0.005")
        from flint.config import FlintConfig
        config = FlintConfig()
        assert config.live_hyperliquid_network == "mainnet"
        assert config.live_hyperliquid_market_order_slippage == 0.005
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::TestHyperliquidConfig -v`
Expected: FAIL — `FlintConfig` has no field `live_hyperliquid_network`

- [ ] **Step 3: Add config fields**

In `flint/config.py`, add after the safety rails section (after line 129):

```python
    # --- Hyperliquid ---
    live_hyperliquid_network: str = "testnet"
    live_hyperliquid_market_order_slippage: float = 0.003
    live_hyperliquid_l2_persist_interval_s: int = 60
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py::TestHyperliquidConfig -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/config.py tests/test_config.py
git commit -m "feat: add Hyperliquid config fields (network, slippage, L2 interval)"
```

---

### Task 2: HyperliquidClient — Info Endpoints (Read-Only)

**Files:**
- Create: `flint/connectors/hyperliquid.py`
- Create: `tests/test_hyperliquid_client.py`

- [ ] **Step 1: Write failing tests for info endpoints**

Create `tests/test_hyperliquid_client.py`:

```python
"""Tests for HyperliquidClient — mocked HTTP, no real connections."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

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
        mock_response.json.return_value = {
            "marginSummary": {"accountValue": "10000.0", "totalMarginUsed": "500.0"},
            "assetPositions": [
                {
                    "position": {
                        "coin": "SOL",
                        "szi": "10.0",
                        "entryPx": "150.0",
                        "unrealizedPnl": "50.0",
                    }
                }
            ],
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
        mock_response.json.return_value = [
            {"coin": "SOL", "oid": 12345, "side": "B", "sz": "10.0", "limitPx": "150.0"}
        ]
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
        mock_response.json.return_value = [
            {"coin": "SOL", "px": "150.0", "sz": "10.0", "fee": "0.05", "time": 1700000000000, "oid": 12345}
        ]
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
        mock_response.json.return_value = [
            {"t": 1700000000000, "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "1000.0"}
        ]
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
        mock_response.json.return_value = {
            "levels": [
                [{"px": "149.0", "sz": "100.0"}, {"px": "148.0", "sz": "200.0"}],
                [{"px": "151.0", "sz": "80.0"}, {"px": "152.0", "sz": "150.0"}],
            ]
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
        assert client.format_size("BTC", 0.123456789) == "0.12345"
        run(client.close())

    def test_format_price(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        # Prices use 6 significant figures
        assert client.format_price(150.123456) == "150.123"
        assert client.format_price(65000.5) == "65000.5"
        run(client.close())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hyperliquid_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.connectors.hyperliquid'`

- [ ] **Step 3: Implement HyperliquidClient info methods**

Create `flint/connectors/hyperliquid.py`:

```python
"""HyperliquidClient — async REST client for Hyperliquid exchange.

Handles all HTTP communication, EIP-712 signing, and market metadata.
Consumed by LiveHyperliquidContext and HyperliquidCandleProvider.

Environment variables:
    FLINT_HYPERLIQUID_PRIVATE_KEY: Ethereum private key (hex).
        Recommended: use an API wallet key from Hyperliquid's web UI
        (trade-only, no withdrawal permission). Withdrawals should be
        done through Hyperliquid's web UI using the main wallet.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Optional

import httpx
from eth_account import Account

logger = logging.getLogger("flint.hyperliquid")

_NETWORK_URLS = {
    "testnet": "https://api.hyperliquid-testnet.xyz",
    "mainnet": "https://api.hyperliquid.xyz",
}

_CHAIN_IDS = {
    "testnet": 13337,
    "mainnet": 1337,
}


class HyperliquidClient:
    """Async HTTP client for Hyperliquid REST API.

    Args:
        private_key: Ethereum private key (hex string, with or without 0x prefix).
        network: "testnet" or "mainnet".
    """

    def __init__(self, private_key: str, network: str = "testnet"):
        self._network = network
        self._base_url = _NETWORK_URLS.get(network, _NETWORK_URLS["testnet"])
        self._chain_id = _CHAIN_IDS.get(network, _CHAIN_IDS["testnet"])
        self._http = httpx.AsyncClient(timeout=15)

        # Derive address from private key
        key = private_key if private_key.startswith("0x") else f"0x{private_key}"
        self._account = Account.from_key(key)
        self._private_key = key

        # Market metadata (populated by _build_asset_maps after get_meta())
        self._coin_to_asset_index: Dict[str, int] = {}
        self._asset_index_to_coin: Dict[int, str] = {}
        self._asset_info: Dict[str, dict] = {}

    @property
    def address(self) -> str:
        """Ethereum address derived from private key."""
        return self._account.address

    # --- Info endpoints (read, no signature) ---

    async def get_meta(self) -> dict:
        """Fetch market metadata (universe, tick sizes, leverage tiers)."""
        resp = await self._http.post(f"{self._base_url}/info", json={"type": "meta"})
        resp.raise_for_status()
        return resp.json()

    async def get_clearinghouse_state(self, address: str) -> dict:
        """Fetch positions, margin summary, and equity for an address."""
        resp = await self._http.post(
            f"{self._base_url}/info",
            json={"type": "clearinghouseState", "user": address},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_open_orders(self, address: str) -> list:
        """Fetch all open orders for an address."""
        resp = await self._http.post(
            f"{self._base_url}/info",
            json={"type": "openOrders", "user": address},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_user_fills(self, address: str, start_time: Optional[int] = None) -> list:
        """Fetch recent fills for an address."""
        payload: dict = {"type": "userFills", "user": address}
        if start_time is not None:
            payload["startTime"] = start_time
        resp = await self._http.post(f"{self._base_url}/info", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def get_candle_snapshot(
        self, coin: str, interval: str, start: int, end: int
    ) -> list:
        """Fetch historical candle data.

        Args:
            coin: Coin name (e.g. "SOL", "BTC").
            interval: Resolution ("1m", "5m", "15m", "1h", "4h", "1d").
            start: Start time in milliseconds.
            end: End time in milliseconds.
        """
        resp = await self._http.post(
            f"{self._base_url}/info",
            json={
                "type": "candleSnapshot",
                "req": {"coin": coin, "interval": interval, "startTime": start, "endTime": end},
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def get_l2_book(self, coin: str) -> dict:
        """Fetch L2 orderbook snapshot."""
        resp = await self._http.post(
            f"{self._base_url}/info",
            json={"type": "l2Book", "coin": coin},
        )
        resp.raise_for_status()
        return resp.json()

    # --- Helpers ---

    def _build_asset_maps(self, meta: dict) -> None:
        """Build coin→index and index→coin maps from meta response."""
        universe = meta.get("universe", [])
        self._coin_to_asset_index = {}
        self._asset_index_to_coin = {}
        self._asset_info = {}
        for i, asset in enumerate(universe):
            name = asset.get("name", "")
            self._coin_to_asset_index[name] = i
            self._asset_index_to_coin[i] = name
            self._asset_info[name] = asset

    def format_size(self, coin: str, size: float) -> str:
        """Format order size to the correct decimal places for a coin."""
        info = self._asset_info.get(coin, {})
        decimals = info.get("szDecimals", 2)
        return f"{size:.{decimals}f}"

    def format_price(self, price: float) -> str:
        """Format price string. Uses up to 6 significant figures."""
        if price == 0:
            return "0"
        # Find the right number of decimals for 6 sig figs
        import math
        if price >= 1:
            int_digits = int(math.log10(price)) + 1
            decimals = max(0, 6 - int_digits)
        else:
            decimals = 6
        formatted = f"{price:.{decimals}f}"
        # Strip trailing zeros but keep at least one decimal
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._http.aclose()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hyperliquid_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/connectors/hyperliquid.py tests/test_hyperliquid_client.py
git commit -m "feat: add HyperliquidClient with info endpoints and precision helpers"
```

---

### Task 3: HyperliquidClient — EIP-712 Signing + Exchange Endpoints

**Files:**
- Modify: `flint/connectors/hyperliquid.py`
- Modify: `tests/test_hyperliquid_client.py`

- [ ] **Step 1: Write failing tests for signing and exchange methods**

Add to `tests/test_hyperliquid_client.py`:

```python
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
        client._coin_to_asset_index = {"SOL": 2}
        client._asset_info = {"SOL": {"szDecimals": 2}}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 99}}]}},
        }
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = run(client.place_order(
                asset=2, is_buy=True, size="10.0", price="150.0",
                order_type={"limit": {"tif": "Gtc"}},
            ))
        assert result["status"] == "ok"
        # Verify the request was sent to /exchange
        call_url = mock_post.call_args[0][0]
        assert "/exchange" in call_url
        run(client.close())

    def test_place_order_returns_oid(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 42}}]}},
        }
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = run(client.place_order(
                asset=2, is_buy=True, size="10.0", price="150.0",
                order_type={"limit": {"tif": "Gtc"}},
            ))
        oid = client.parse_order_id(result)
        assert oid == 42
        run(client.close())


class TestCancelOrder:
    def test_cancel_order_sends_signed_request(self):
        client = HyperliquidClient(private_key="0x" + "ab" * 32, network="testnet")
        mock_response = MagicMock()
        mock_response.status_code = 200
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
        mock_response.json.return_value = {"status": "ok"}
        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = run(client.cancel_all_orders())
        assert result["status"] == "ok"
        run(client.close())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hyperliquid_client.py::TestEIP712Signing -v`
Expected: FAIL — `HyperliquidClient has no attribute '_sign_action'`

- [ ] **Step 3: Implement signing and exchange methods**

Add to `flint/connectors/hyperliquid.py` in the `HyperliquidClient` class:

```python
    # --- EIP-712 Signing ---

    def _sign_action(self, action: dict) -> tuple:
        """Sign an exchange action using EIP-712.

        Returns (signature_dict, nonce).
        """
        nonce = int(time.time() * 1000)

        # Hyperliquid EIP-712 phantom agent signing
        # The agent signs a hash of the action + nonce + vault_address=0
        import hashlib
        import json

        action_bytes = self._action_hash(action, nonce)
        phantom_agent = {
            "source": "a" if self._network == "mainnet" else "b",
            "connectionId": action_bytes,
        }

        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "HyperliquidTransaction:Approve": [
                    {"name": "hyperliquidChain", "type": "string"},
                    {"name": "source", "type": "string"},
                    {"name": "connectionId", "type": "bytes32"},
                ],
            },
            "primaryType": "HyperliquidTransaction:Approve",
            "domain": {
                "name": "Exchange",
                "version": "1",
                "chainId": self._chain_id,
                "verifyingContract": "0x0000000000000000000000000000000000000000",
            },
            "message": {
                "hyperliquidChain": "Mainnet" if self._network == "mainnet" else "Testnet",
                "source": phantom_agent["source"],
                "connectionId": phantom_agent["connectionId"],
            },
        }

        signed = self._account.sign_typed_data(
            typed_data["domain"],
            {"HyperliquidTransaction:Approve": typed_data["types"]["HyperliquidTransaction:Approve"]},
            typed_data["message"],
        )
        signature = {
            "r": hex(signed.r),
            "s": hex(signed.s),
            "v": signed.v,
        }
        return signature, nonce

    def _action_hash(self, action: dict, nonce: int) -> bytes:
        """Compute the action hash for signing."""
        import msgpack
        import hashlib
        data = msgpack.packb(action, use_bin_type=True)
        combined = data + nonce.to_bytes(8, "big") + b"\x00"
        return hashlib.sha256(combined).digest()

    # --- Exchange endpoints (write, signed) ---

    async def place_order(
        self,
        asset: int,
        is_buy: bool,
        size: str,
        price: str,
        order_type: dict,
        reduce_only: bool = False,
    ) -> dict:
        """Place an order on Hyperliquid.

        Args:
            asset: Asset index from meta.
            is_buy: True for buy, False for sell.
            size: Order size as string (precision matters).
            price: Price as string.
            order_type: e.g. {"limit": {"tif": "Gtc"}} or {"trigger": {...}}.
            reduce_only: If True, only reduces existing position.
        """
        action = {
            "type": "order",
            "orders": [{
                "a": asset,
                "b": is_buy,
                "p": price,
                "s": size,
                "r": reduce_only,
                "t": order_type,
            }],
            "grouping": "na",
        }
        return await self._exchange_request(action)

    async def cancel_order(self, asset: int, oid: int) -> dict:
        """Cancel a specific order."""
        action = {
            "type": "cancel",
            "cancels": [{"a": asset, "o": oid}],
        }
        return await self._exchange_request(action)

    async def cancel_all_orders(self, asset: Optional[int] = None) -> dict:
        """Cancel all orders, optionally filtered by asset."""
        action: dict = {"type": "cancelByCloid"}
        if asset is not None:
            action = {
                "type": "cancel",
                "cancels": [{"a": asset, "o": -1}],  # -1 = all for asset
            }
        else:
            # Cancel all by sending an empty cancel
            action = {"type": "cancelByCloid", "cancels": []}
        return await self._exchange_request(action)

    async def _exchange_request(self, action: dict) -> dict:
        """Send a signed exchange request."""
        signature, nonce = self._sign_action(action)
        payload = {
            "action": action,
            "nonce": nonce,
            "signature": signature,
        }
        resp = await self._http.post(f"{self._base_url}/exchange", json=payload)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def parse_order_id(response: dict) -> Optional[int]:
        """Extract the order ID from a place_order response."""
        try:
            statuses = response["response"]["data"]["statuses"]
            if statuses:
                status = statuses[0]
                if "resting" in status:
                    return status["resting"]["oid"]
                elif "filled" in status:
                    return status["filled"]["oid"]
        except (KeyError, IndexError):
            pass
        return None
```

Note: This also requires adding `msgpack` as a dependency. Add to `pyproject.toml` optional dependencies. However, if `msgpack` is not desired, the action hashing can alternatively use JSON encoding. Check at implementation time whether Hyperliquid's actual signing uses msgpack or JSON — their Python SDK uses msgpack. If msgpack is unavailable, use `json.dumps(action, sort_keys=True).encode()` as a fallback.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hyperliquid_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/connectors/hyperliquid.py tests/test_hyperliquid_client.py
git commit -m "feat: add EIP-712 signing and exchange endpoints to HyperliquidClient"
```

---

### Task 4: LiveHyperliquidContext — Constructor + Connection

**Files:**
- Create: `flint/execution/hyperliquid_live.py`
- Create: `tests/test_hyperliquid_live.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_hyperliquid_live.py`:

```python
"""Tests for LiveHyperliquidContext — mocked, no real connections."""
import asyncio
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from flint.models import Side, OrderType, OrderState, PositionInfo


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestConstruction:
    def test_creates_with_key(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")
        assert ctx._venue == "hyperliquid"
        assert ctx.timestamp > 0
        assert ctx.positions == []

    def test_no_key_raises(self, monkeypatch):
        monkeypatch.delenv("FLINT_HYPERLIQUID_PRIVATE_KEY", raising=False)
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        with pytest.raises(ValueError, match="No private key"):
            LiveHyperliquidContext(private_key="", network="testnet")

    def test_key_from_env(self, monkeypatch):
        monkeypatch.setenv("FLINT_HYPERLIQUID_PRIVATE_KEY", "0x" + "cd" * 32)
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(network="testnet")
        assert ctx._venue == "hyperliquid"

    def test_testnet_default(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32)
        assert ctx._network == "testnet"

    def test_mainnet_network(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="mainnet")
        assert ctx._network == "mainnet"


class TestMarketMapping:
    def test_flint_to_hl_symbol(self):
        from flint.execution.hyperliquid_live import FLINT_TO_HL
        assert FLINT_TO_HL["SOL-PERP"] == "SOL"
        assert FLINT_TO_HL["BTC-PERP"] == "BTC"

    def test_hl_to_flint_symbol(self):
        from flint.execution.hyperliquid_live import HL_TO_FLINT
        assert HL_TO_FLINT["SOL"] == "SOL-PERP"
        assert HL_TO_FLINT["BTC"] == "BTC-PERP"


class TestOrderQueuing:
    def _make_ctx(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        return LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

    def test_market_order_queues(self):
        ctx = self._make_ctx()
        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid != ""
        assert len(ctx._tracker.active_orders) == 1

    def test_limit_order_queues(self):
        ctx = self._make_ctx()
        oid = ctx.limit_order("SOL-PERP", Side.LONG, 10.0, 150.0)
        tracked = ctx._tracker.get(oid)
        assert tracked.order.order_type == OrderType.LIMIT
        assert tracked.order.price == 150.0

    def test_stop_order_queues(self):
        ctx = self._make_ctx()
        oid = ctx.stop_order("SOL-PERP", Side.SHORT, 5.0, 140.0)
        tracked = ctx._tracker.get(oid)
        assert tracked.order.order_type == OrderType.STOP_LOSS

    def test_cancel_all(self):
        ctx = self._make_ctx()
        ctx.market_order("SOL-PERP", Side.LONG, 5.0)
        ctx.market_order("BTC-PERP", Side.SHORT, 0.1)
        assert ctx.cancel_all() == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hyperliquid_live.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.execution.hyperliquid_live'`

- [ ] **Step 3: Implement constructor and market mapping**

Create `flint/execution/hyperliquid_live.py`:

```python
"""LiveHyperliquidContext — ExecutionContext for live trading on Hyperliquid.

Extends LiveExecutionContext with the same 7 abstract methods as LiveDriftContext.
Strategies deploy to Drift or Hyperliquid with zero code changes.

Environment variables:
    FLINT_HYPERLIQUID_PRIVATE_KEY: Ethereum private key (hex string).
        Recommended: use an API wallet key from Hyperliquid's web UI
        (trade-only permissions). Withdrawals should be done through
        Hyperliquid's web UI using the main wallet.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional, Tuple

from ..models import (
    Fill, Order, OrderState, OrderType, PositionInfo, Side,
)
from .live_base import LiveExecutionContext

logger = logging.getLogger("flint.hyperliquid_live")

# Flint symbol ↔ Hyperliquid coin mapping
# Reuses the same markets as HYPERLIQUID_SYMBOLS in providers/funding_rates.py
FLINT_TO_HL: Dict[str, str] = {
    "SOL-PERP": "SOL", "BTC-PERP": "BTC", "ETH-PERP": "ETH",
    "DOGE-PERP": "DOGE", "AVAX-PERP": "AVAX", "LINK-PERP": "LINK",
    "ARB-PERP": "ARB", "SUI-PERP": "SUI", "XRP-PERP": "XRP",
    "OP-PERP": "OP", "INJ-PERP": "INJ", "TIA-PERP": "TIA",
    "SEI-PERP": "SEI", "WIF-PERP": "WIF", "JUP-PERP": "JUP",
    "RENDER-PERP": "RENDER", "BNB-PERP": "BNB",
}

HL_TO_FLINT: Dict[str, str] = {v: k for k, v in FLINT_TO_HL.items()}


class LiveHyperliquidContext(LiveExecutionContext):
    """ExecutionContext that submits real orders to Hyperliquid.

    Same interface as LiveDriftContext — strategies work identically.
    """

    def __init__(
        self,
        private_key: Optional[str] = None,
        network: str = "testnet",
        market_order_slippage: float = 0.003,
        initial_capital: float = 0,
        risk_manager=None,
        store=None,
        session_id: str = "",
        max_retries: int = 3,
        on_failure: str = "drop",
        **kwargs,
    ):
        key = private_key or os.environ.get("FLINT_HYPERLIQUID_PRIVATE_KEY", "")
        if not key:
            raise ValueError(
                "No private key provided. Set FLINT_HYPERLIQUID_PRIVATE_KEY environment "
                "variable or pass private_key parameter."
            )
        self._private_key = key
        self._network = network
        self._market_order_slippage = market_order_slippage
        self._client = None  # Created in _connect()

        super().__init__(
            venue="hyperliquid",
            initial_capital=initial_capital,
            risk_manager=risk_manager,
            store=store,
            session_id=session_id,
            max_retries=max_retries,
            on_failure=on_failure,
            **kwargs,
        )

        logger.info("LiveHyperliquidContext initialized (network=%s)", network)

    # --- Abstract method implementations (stubs — filled in Task 5) ---

    async def _connect(self) -> None:
        from ..connectors.hyperliquid import HyperliquidClient
        self._client = HyperliquidClient(
            private_key=self._private_key,
            network=self._network,
        )
        meta = await self._client.get_meta()
        self._client._build_asset_maps(meta)
        logger.info("Connected to Hyperliquid (%s), %d assets",
                     self._network, len(self._client._coin_to_asset_index))

    async def _disconnect(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            logger.info("Disconnected from Hyperliquid")

    async def _place_order(self, order: Order) -> Tuple[str, Optional[int]]:
        raise NotImplementedError("Implemented in Task 5")

    async def _cancel_order(self, venue_order_id: int) -> bool:
        raise NotImplementedError("Implemented in Task 5")

    async def _fetch_positions(self) -> List[PositionInfo]:
        raise NotImplementedError("Implemented in Task 5")

    async def _fetch_balance(self) -> float:
        raise NotImplementedError("Implemented in Task 5")

    async def _poll_order_status(self, venue_order_id: int) -> OrderState:
        raise NotImplementedError("Implemented in Task 5")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hyperliquid_live.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/execution/hyperliquid_live.py tests/test_hyperliquid_live.py
git commit -m "feat: add LiveHyperliquidContext constructor and market mapping"
```

---

### Task 5: LiveHyperliquidContext — 7 Abstract Methods

**Files:**
- Modify: `flint/execution/hyperliquid_live.py`
- Modify: `tests/test_hyperliquid_live.py`

- [ ] **Step 1: Write failing tests for the 7 abstract methods**

Add to `tests/test_hyperliquid_live.py`:

```python
class TestPlaceOrder:
    def test_market_order_uses_ioc_with_slippage(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        # Set up mock client
        from flint.connectors.hyperliquid import HyperliquidClient
        mock_client = MagicMock(spec=HyperliquidClient)
        mock_client.address = "0x1234"
        mock_client._coin_to_asset_index = {"SOL": 2}
        mock_client._asset_info = {"SOL": {"szDecimals": 2}}
        mock_client.format_size.return_value = "10.00"
        mock_client.format_price.return_value = "150.45"
        mock_client.place_order = AsyncMock(return_value={
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 99}}]}},
        })
        mock_client.parse_order_id = HyperliquidClient.parse_order_id
        ctx._client = mock_client

        # Mock a current candle for mark price
        from flint.models import Candle
        ctx._current_candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0, close=150.0, volume=100.0, market="SOL-PERP", resolution_s=60)

        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET, size=10.0, order_id="test-1", ts=1000)
        tx_sig, venue_oid = run(ctx._place_order(order))
        assert venue_oid == 99

        # Verify IOC was used
        call_args = mock_client.place_order.call_args
        assert call_args.kwargs["order_type"] == {"limit": {"tif": "Ioc"}}

    def test_limit_order_uses_gtc(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        from flint.connectors.hyperliquid import HyperliquidClient
        mock_client = MagicMock(spec=HyperliquidClient)
        mock_client._coin_to_asset_index = {"SOL": 2}
        mock_client._asset_info = {"SOL": {"szDecimals": 2}}
        mock_client.format_size.return_value = "10.00"
        mock_client.format_price.return_value = "150.00"
        mock_client.place_order = AsyncMock(return_value={
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 50}}]}},
        })
        mock_client.parse_order_id = HyperliquidClient.parse_order_id
        ctx._client = mock_client

        order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.LIMIT, size=10.0, price=150.0, order_id="test-2", ts=1000)
        tx_sig, venue_oid = run(ctx._place_order(order))
        assert venue_oid == 50
        call_args = mock_client.place_order.call_args
        assert call_args.kwargs["order_type"] == {"limit": {"tif": "Gtc"}}

    def test_stop_order_uses_trigger(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        from flint.connectors.hyperliquid import HyperliquidClient
        mock_client = MagicMock(spec=HyperliquidClient)
        mock_client._coin_to_asset_index = {"SOL": 2}
        mock_client._asset_info = {"SOL": {"szDecimals": 2}}
        mock_client.format_size.return_value = "5.00"
        mock_client.format_price.return_value = "140.00"
        mock_client.place_order = AsyncMock(return_value={
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 60}}]}},
        })
        mock_client.parse_order_id = HyperliquidClient.parse_order_id
        ctx._client = mock_client

        order = Order(market="SOL-PERP", side=Side.SHORT, order_type=OrderType.STOP_LOSS, size=5.0, price=140.0, order_id="test-3", ts=1000)
        tx_sig, venue_oid = run(ctx._place_order(order))
        call_args = mock_client.place_order.call_args
        assert "trigger" in call_args.kwargs["order_type"]


class TestFetchPositions:
    def test_parses_clearinghouse_positions(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_clearinghouse_state = AsyncMock(return_value={
            "marginSummary": {"accountValue": "10000.0"},
            "assetPositions": [
                {"position": {"coin": "SOL", "szi": "10.0", "entryPx": "150.0", "unrealizedPnl": "50.0"}},
                {"position": {"coin": "BTC", "szi": "-0.5", "entryPx": "65000.0", "unrealizedPnl": "-100.0"}},
            ],
        })
        ctx._client = mock_client

        positions = run(ctx._fetch_positions())
        assert len(positions) == 2
        sol_pos = next(p for p in positions if p.market == "SOL-PERP")
        assert sol_pos.side == Side.LONG
        assert sol_pos.size == 10.0
        btc_pos = next(p for p in positions if p.market == "BTC-PERP")
        assert btc_pos.side == Side.SHORT
        assert btc_pos.size == 0.5

    def test_skips_zero_positions(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_clearinghouse_state = AsyncMock(return_value={
            "marginSummary": {"accountValue": "10000.0"},
            "assetPositions": [
                {"position": {"coin": "SOL", "szi": "0.0", "entryPx": "0", "unrealizedPnl": "0"}},
            ],
        })
        ctx._client = mock_client
        positions = run(ctx._fetch_positions())
        assert len(positions) == 0


class TestFetchBalance:
    def test_returns_account_value(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_clearinghouse_state = AsyncMock(return_value={
            "marginSummary": {"accountValue": "12345.67"},
            "assetPositions": [],
        })
        ctx._client = mock_client
        balance = run(ctx._fetch_balance())
        assert balance == 12345.67


class TestCancelOrder:
    def test_cancel_calls_client(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        mock_client = MagicMock()
        mock_client.cancel_order = AsyncMock(return_value={"status": "ok"})
        # Need to track which asset the order belongs to
        ctx._client = mock_client
        ctx._venue_order_to_asset = {42: 2}  # oid 42 → asset 2 (SOL)
        result = run(ctx._cancel_order(42))
        assert result is True


class TestPollOrderStatus:
    def test_order_in_open_orders_returns_confirmed(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_open_orders = AsyncMock(return_value=[
            {"oid": 42, "coin": "SOL", "side": "B", "sz": "10.0"},
        ])
        ctx._client = mock_client
        status = run(ctx._poll_order_status(42))
        assert status == OrderState.CONFIRMED

    def test_order_not_in_open_orders_with_fill_returns_filled(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_open_orders = AsyncMock(return_value=[])
        mock_client.get_user_fills = AsyncMock(return_value=[
            {"oid": 42, "px": "150.0", "sz": "10.0"},
        ])
        ctx._client = mock_client
        status = run(ctx._poll_order_status(42))
        assert status == OrderState.FILLED

    def test_order_not_in_open_or_fills_returns_cancelled(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext
        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")

        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_open_orders = AsyncMock(return_value=[])
        mock_client.get_user_fills = AsyncMock(return_value=[])
        ctx._client = mock_client
        status = run(ctx._poll_order_status(42))
        assert status == OrderState.CANCELLED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hyperliquid_live.py::TestPlaceOrder -v`
Expected: FAIL — `NotImplementedError: Implemented in Task 5`

- [ ] **Step 3: Replace stubs with real implementations**

Replace the 5 stub methods in `flint/execution/hyperliquid_live.py`:

```python
    async def _place_order(self, order: Order) -> Tuple[str, Optional[int]]:
        if self._client is None:
            raise RuntimeError("Not connected — call connect() first")

        coin = FLINT_TO_HL.get(order.market)
        if coin is None:
            raise ValueError(f"Unknown Hyperliquid market: {order.market}")

        asset = self._client._coin_to_asset_index.get(coin)
        if asset is None:
            raise ValueError(f"Asset index not found for {coin} — call get_meta() first")

        is_buy = order.side == Side.LONG

        if order.order_type == OrderType.MARKET:
            # Simulate market order with IOC limit at aggressive price
            mark = self._current_candle.close if self._current_candle else order.price
            if mark <= 0:
                raise ValueError("No mark price available for market order")
            if is_buy:
                price = mark * (1 + self._market_order_slippage)
            else:
                price = mark * (1 - self._market_order_slippage)
            size_str = self._client.format_size(coin, order.size)
            price_str = self._client.format_price(price)
            order_type = {"limit": {"tif": "Ioc"}}
        elif order.order_type == OrderType.LIMIT:
            size_str = self._client.format_size(coin, order.size)
            price_str = self._client.format_price(order.price)
            order_type = {"limit": {"tif": "Gtc"}}
        elif order.order_type == OrderType.STOP_LOSS:
            size_str = self._client.format_size(coin, order.size)
            price_str = self._client.format_price(order.price)
            order_type = {
                "trigger": {
                    "triggerPx": price_str,
                    "isMarket": True,
                    "tpsl": "sl",
                }
            }
        elif order.order_type == OrderType.TAKE_PROFIT:
            size_str = self._client.format_size(coin, order.size)
            price_str = self._client.format_price(order.price)
            order_type = {
                "trigger": {
                    "triggerPx": price_str,
                    "isMarket": True,
                    "tpsl": "tp",
                }
            }
        else:
            raise ValueError(f"Unsupported order type: {order.order_type}")

        from ..connectors.hyperliquid import HyperliquidClient

        result = await self._client.place_order(
            asset=asset, is_buy=is_buy, size=size_str, price=price_str,
            order_type=order_type,
        )
        oid = HyperliquidClient.parse_order_id(result)

        if oid is not None:
            self._venue_order_to_asset[oid] = asset

        tx_sig = str(oid) if oid else ""
        logger.info("Order submitted: %s oid=%s", order.order_id, oid)
        return (tx_sig, oid)

    async def _cancel_order(self, venue_order_id: int) -> bool:
        if self._client is None:
            return False
        asset = self._venue_order_to_asset.get(venue_order_id)
        if asset is None:
            logger.warning("Unknown asset for order %d, cannot cancel", venue_order_id)
            return False
        try:
            await self._client.cancel_order(asset, venue_order_id)
            return True
        except Exception as e:
            logger.error("Cancel order %d failed: %s", venue_order_id, e)
            return False

    async def _fetch_positions(self) -> List[PositionInfo]:
        if self._client is None:
            return []
        try:
            state = await self._client.get_clearinghouse_state(self._client.address)
            positions = []
            for item in state.get("assetPositions", []):
                pos = item.get("position", {})
                coin = pos.get("coin", "")
                szi = float(pos.get("szi", "0"))
                if szi == 0:
                    continue
                market = HL_TO_FLINT.get(coin)
                if market is None:
                    continue
                side = Side.LONG if szi > 0 else Side.SHORT
                size = abs(szi)
                entry_price = float(pos.get("entryPx", "0"))
                unrealized = float(pos.get("unrealizedPnl", "0"))
                positions.append(PositionInfo(
                    market=market, side=side, size=size,
                    entry_price=entry_price, unrealized_pnl=unrealized,
                    venue="hyperliquid",
                ))
            return positions
        except Exception as e:
            logger.error("Position fetch failed: %s", e)
            return []

    async def _fetch_balance(self) -> float:
        if self._client is None:
            return 0.0
        try:
            state = await self._client.get_clearinghouse_state(self._client.address)
            return float(state["marginSummary"]["accountValue"])
        except Exception as e:
            logger.error("Balance fetch failed: %s", e)
            return 0.0

    async def _poll_order_status(self, venue_order_id: int) -> OrderState:
        if self._client is None:
            return OrderState.FAILED
        try:
            open_orders = await self._client.get_open_orders(self._client.address)
            for o in open_orders:
                if o.get("oid") == venue_order_id:
                    return OrderState.CONFIRMED

            fills = await self._client.get_user_fills(self._client.address)
            for f in fills:
                if f.get("oid") == venue_order_id:
                    return OrderState.FILLED

            return OrderState.CANCELLED
        except Exception as e:
            logger.error("Order status poll failed for %d: %s", venue_order_id, e)
            return OrderState.CONFIRMED
```

Also add `self._venue_order_to_asset: Dict[int, int] = {}` to `__init__` (after `self._client = None`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hyperliquid_live.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/execution/hyperliquid_live.py tests/test_hyperliquid_live.py
git commit -m "feat: implement 7 abstract methods for LiveHyperliquidContext"
```

---

### Task 6: HyperliquidWebSocketFeed

**Files:**
- Create: `flint/providers/hyperliquid_ws.py`
- Create: `tests/test_hyperliquid_ws.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_hyperliquid_ws.py`:

```python
"""Tests for HyperliquidWebSocketFeed — mocked, no real connections."""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from flint.models import Candle
from flint.providers.hyperliquid_ws import HyperliquidWebSocketFeed


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestConstruction:
    def test_creates_with_markets(self):
        feed = HyperliquidWebSocketFeed(markets=["SOL-PERP", "BTC-PERP"])
        assert feed._name == "hyperliquid"
        assert len(feed._markets) == 2

    def test_testnet_url(self):
        feed = HyperliquidWebSocketFeed(markets=["SOL-PERP"], network="testnet")
        assert "testnet" in feed._url

    def test_mainnet_url(self):
        feed = HyperliquidWebSocketFeed(markets=["SOL-PERP"], network="mainnet")
        assert "testnet" not in feed._url


class TestCandleHandling:
    def test_candle_message_fires_callback(self):
        closed = []
        feed = HyperliquidWebSocketFeed(
            markets=["SOL-PERP"],
            on_candle_close=lambda c: closed.append(c),
        )
        # First candle message — sets up state
        run(feed._handle_message({
            "channel": "candle",
            "data": {
                "t": 1000000, "T": 1060000,
                "s": "SOL", "i": "1m",
                "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "1000.0",
            },
        }))
        # Second candle with new timestamp — closes the first
        run(feed._handle_message({
            "channel": "candle",
            "data": {
                "t": 1060000, "T": 1120000,
                "s": "SOL", "i": "1m",
                "o": "153.0", "h": "156.0", "l": "152.0", "c": "154.0", "v": "800.0",
            },
        }))
        assert len(closed) == 1
        assert closed[0].market == "SOL-PERP"
        assert closed[0].venue == "hyperliquid"
        assert closed[0].open == 150.0
        assert closed[0].close == 153.0

    def test_same_timestamp_updates_candle(self):
        closed = []
        feed = HyperliquidWebSocketFeed(
            markets=["SOL-PERP"],
            on_candle_close=lambda c: closed.append(c),
        )
        # Two messages with same start timestamp — update, don't close
        run(feed._handle_message({
            "channel": "candle",
            "data": {"t": 1000000, "T": 1060000, "s": "SOL", "i": "1m",
                     "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "1000.0"},
        }))
        run(feed._handle_message({
            "channel": "candle",
            "data": {"t": 1000000, "T": 1060000, "s": "SOL", "i": "1m",
                     "o": "150.0", "h": "156.0", "l": "148.0", "c": "155.0", "v": "1200.0"},
        }))
        assert len(closed) == 0


class TestL2BookHandling:
    def test_l2_book_updates_state(self):
        feed = HyperliquidWebSocketFeed(markets=["SOL-PERP"])
        run(feed._handle_message({
            "channel": "l2Book",
            "data": {
                "coin": "SOL",
                "levels": [
                    [{"px": "149.0", "sz": "100.0"}],
                    [{"px": "151.0", "sz": "80.0"}],
                ],
            },
        }))
        book = feed.get_orderbook("SOL-PERP")
        assert book is not None
        assert "levels" in book

    def test_unknown_market_ignored(self):
        feed = HyperliquidWebSocketFeed(markets=["SOL-PERP"])
        run(feed._handle_message({
            "channel": "l2Book",
            "data": {"coin": "UNKNOWN", "levels": [[], []]},
        }))
        assert feed.get_orderbook("UNKNOWN-PERP") is None


class TestOrderUpdateHandling:
    def test_order_update_fires_callback(self):
        updates = []
        feed = HyperliquidWebSocketFeed(
            markets=["SOL-PERP"],
            on_order_update=lambda u: updates.append(u),
        )
        run(feed._handle_message({
            "channel": "orderUpdates",
            "data": [{"order": {"oid": 42, "coin": "SOL"}, "status": "filled"}],
        }))
        assert len(updates) == 1
        assert updates[0]["order"]["oid"] == 42


class TestSubscription:
    def test_subscribe_sends_messages(self):
        feed = HyperliquidWebSocketFeed(
            markets=["SOL-PERP", "BTC-PERP"],
            user_address="0x1234",
        )
        mock_ws = AsyncMock()
        run(feed._subscribe(mock_ws))
        # Should subscribe to candle + l2Book per market + orderUpdates
        # 2 markets * 2 channels + 1 orderUpdates = 5
        assert mock_ws.send.call_count == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hyperliquid_ws.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.providers.hyperliquid_ws'`

- [ ] **Step 3: Implement HyperliquidWebSocketFeed**

Create `flint/providers/hyperliquid_ws.py`:

```python
"""HyperliquidWebSocketFeed — real-time candle, orderbook, and order update data.

Subscribes to Hyperliquid's native candle channel (no CandleAggregator needed),
L2 orderbook snapshots, and user order update events.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable, Dict, List, Optional

from ..models import Candle
from .websocket import WebSocketFeed

logger = logging.getLogger("flint.hyperliquid_ws")

_NETWORK_WS_URLS = {
    "testnet": "wss://api.hyperliquid-testnet.xyz/ws",
    "mainnet": "wss://api.hyperliquid.xyz/ws",
}

# Flint symbol ↔ Hyperliquid coin (same as execution layer)
_FLINT_TO_HL = {
    "SOL-PERP": "SOL", "BTC-PERP": "BTC", "ETH-PERP": "ETH",
    "DOGE-PERP": "DOGE", "AVAX-PERP": "AVAX", "LINK-PERP": "LINK",
    "ARB-PERP": "ARB", "SUI-PERP": "SUI", "XRP-PERP": "XRP",
    "OP-PERP": "OP", "INJ-PERP": "INJ", "TIA-PERP": "TIA",
    "SEI-PERP": "SEI", "WIF-PERP": "WIF", "JUP-PERP": "JUP",
    "RENDER-PERP": "RENDER", "BNB-PERP": "BNB",
}
_HL_TO_FLINT = {v: k for k, v in _FLINT_TO_HL.items()}

# Map candle interval strings to seconds for resolution_s
_INTERVAL_TO_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400,
}


class HyperliquidWebSocketFeed(WebSocketFeed):
    """WebSocket feed for Hyperliquid candle, orderbook, and order update data.

    Args:
        markets: Flint market symbols (e.g. ["SOL-PERP", "BTC-PERP"]).
        network: "testnet" or "mainnet".
        candle_interval: Native candle interval ("1m", "5m", etc.).
        on_candle_close: Callback fired when a candle bar closes.
        on_order_update: Callback fired on order fill/cancel events.
        user_address: Ethereum address for orderUpdates subscription.
        store: Optional FlintStore for persisting orderbook snapshots.
        l2_persist_interval_s: How often to persist L2 book to store.
    """

    def __init__(
        self,
        markets: List[str],
        network: str = "testnet",
        candle_interval: str = "1m",
        on_candle_close: Optional[Callable[[Candle], None]] = None,
        on_order_update: Optional[Callable[[dict], None]] = None,
        user_address: Optional[str] = None,
        store=None,
        l2_persist_interval_s: int = 60,
        **kwargs,
    ):
        url = _NETWORK_WS_URLS.get(network, _NETWORK_WS_URLS["testnet"])
        super().__init__(url=url, name="hyperliquid", **kwargs)
        self._markets = markets
        self._candle_interval = candle_interval
        self._on_candle_close = on_candle_close or (lambda c: None)
        self._on_order_update = on_order_update or (lambda u: None)
        self._user_address = user_address
        self._store = store
        self._l2_persist_interval_s = l2_persist_interval_s

        # Current candle state per market (for detecting close)
        self._current_candles: Dict[str, dict] = {}
        # L2 orderbook state per market
        self._orderbooks: Dict[str, dict] = {}
        self._last_l2_persist: float = 0.0

    def get_orderbook(self, market: str) -> Optional[dict]:
        """Get the latest L2 orderbook for a market."""
        return self._orderbooks.get(market)

    async def _connect_ws(self):
        import websockets
        return await websockets.connect(self._url)

    async def _subscribe(self, ws) -> None:
        for market in self._markets:
            coin = _FLINT_TO_HL.get(market, market)
            # Candle channel
            await ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "candle", "coin": coin, "interval": self._candle_interval},
            }))
            # L2 book channel
            await ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "l2Book", "coin": coin},
            }))
        # Order updates (user-specific)
        if self._user_address:
            await ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "orderUpdates", "user": self._user_address},
            }))
        logger.info("Subscribed to %d markets (candle + l2Book)%s",
                     len(self._markets),
                     " + orderUpdates" if self._user_address else "")

    async def _handle_message(self, raw: dict) -> None:
        channel = raw.get("channel", "")
        if channel == "candle":
            self._handle_candle(raw.get("data", {}))
        elif channel == "l2Book":
            self._handle_l2_book(raw.get("data", {}))
        elif channel == "orderUpdates":
            self._handle_order_updates(raw.get("data", []))

    def _handle_candle(self, data: dict) -> None:
        coin = data.get("s", "")
        market = _HL_TO_FLINT.get(coin)
        if market is None:
            return

        # t = candle open time (ms), T = candle close time (ms)
        open_time = data.get("t", 0)
        prev = self._current_candles.get(market)

        if prev is not None and prev.get("t") != open_time:
            # New candle started — close the previous one
            resolution_s = _INTERVAL_TO_SECONDS.get(self._candle_interval, 60)
            candle = Candle(
                ts=prev["t"] // 1000,
                open=float(prev["o"]),
                high=float(prev["h"]),
                low=float(prev["l"]),
                close=float(prev["c"]),
                volume=float(prev["v"]),
                market=market,
                resolution_s=resolution_s,
                venue="hyperliquid",
            )
            self._on_candle_close(candle)
            if self._store:
                try:
                    self._store.upsert_candles([candle])
                except Exception as e:
                    logger.error("Failed to persist candle: %s", e)

        self._current_candles[market] = data

    def _handle_l2_book(self, data: dict) -> None:
        coin = data.get("coin", "")
        market = _HL_TO_FLINT.get(coin)
        if market is None:
            return
        self._orderbooks[market] = data

        # Periodic persistence
        now = time.time()
        if self._store and now - self._last_l2_persist > self._l2_persist_interval_s:
            self._last_l2_persist = now
            try:
                self._store.upsert_orderbook_snapshot(
                    market=market, venue="hyperliquid", ts=int(now),
                    bids=json.dumps(data.get("levels", [[]])[0]),
                    asks=json.dumps(data.get("levels", [[], []])[1]),
                )
            except Exception as e:
                logger.error("Failed to persist L2 book: %s", e)

    def _handle_order_updates(self, data: list) -> None:
        for update in data:
            self._on_order_update(update)

    async def _fallback_poll(self) -> None:
        """Poll REST for latest candle while WS is disconnected."""
        import asyncio
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                now_ms = int(time.time() * 1000)
                resolution_s = _INTERVAL_TO_SECONDS.get(self._candle_interval, 60)
                start_ms = now_ms - resolution_s * 3 * 1000
                for market in self._markets:
                    coin = _FLINT_TO_HL.get(market, market)
                    resp = await client.post(
                        self._url.replace("wss://", "https://").replace("/ws", "/info"),
                        json={
                            "type": "candleSnapshot",
                            "req": {"coin": coin, "interval": self._candle_interval,
                                    "startTime": start_ms, "endTime": now_ms},
                        },
                    )
                    if resp.status_code == 200:
                        candles = resp.json()
                        if candles:
                            last = candles[-1]
                            candle = Candle(
                                ts=last["t"] // 1000,
                                open=float(last["o"]),
                                high=float(last["h"]),
                                low=float(last["l"]),
                                close=float(last["c"]),
                                volume=float(last["v"]),
                                market=market,
                                resolution_s=resolution_s,
                                venue="hyperliquid",
                            )
                            self._on_candle_close(candle)
        except Exception as e:
            logger.error("Hyperliquid fallback poll failed: %s", e)

    async def _backfill_gap(self, disconnect_ts: int, reconnect_ts: int) -> None:
        """Fetch missed candles from REST after reconnect."""
        try:
            import httpx
            resolution_s = _INTERVAL_TO_SECONDS.get(self._candle_interval, 60)
            async with httpx.AsyncClient(timeout=10) as client:
                for market in self._markets:
                    coin = _FLINT_TO_HL.get(market, market)
                    resp = await client.post(
                        self._url.replace("wss://", "https://").replace("/ws", "/info"),
                        json={
                            "type": "candleSnapshot",
                            "req": {"coin": coin, "interval": self._candle_interval,
                                    "startTime": disconnect_ts * 1000,
                                    "endTime": reconnect_ts * 1000},
                        },
                    )
                    if resp.status_code == 200:
                        raw_candles = resp.json()
                        candles = []
                        for c in raw_candles:
                            candles.append(Candle(
                                ts=c["t"] // 1000,
                                open=float(c["o"]),
                                high=float(c["h"]),
                                low=float(c["l"]),
                                close=float(c["c"]),
                                volume=float(c["v"]),
                                market=market,
                                resolution_s=resolution_s,
                                venue="hyperliquid",
                            ))
                        if candles and self._store:
                            self._store.upsert_candles(candles)
                        logger.info("Backfilled %d candles for %s", len(candles), market)
        except Exception as e:
            logger.error("Hyperliquid backfill failed: %s", e)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hyperliquid_ws.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/providers/hyperliquid_ws.py tests/test_hyperliquid_ws.py
git commit -m "feat: add HyperliquidWebSocketFeed with candle, L2 book, and order update channels"
```

---

### Task 7: HyperliquidCandleProvider (Historical Data)

**Files:**
- Create: `flint/providers/hyperliquid_candles.py`
- Create: `tests/test_hyperliquid_candles.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_hyperliquid_candles.py`:

```python
"""Tests for HyperliquidCandleProvider — mocked HTTP."""
import pytest
from unittest.mock import patch, MagicMock

from flint.models import Candle
from flint.providers.hyperliquid_candles import HyperliquidCandleProvider


class TestFetchCandles:
    def test_parses_candle_response(self):
        provider = HyperliquidCandleProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"t": 1700000000000, "T": 1700000060000, "s": "SOL",
             "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "1000.0"},
            {"t": 1700000060000, "T": 1700000120000, "s": "SOL",
             "o": "153.0", "h": "156.0", "l": "152.0", "c": "154.0", "v": "800.0"},
        ]
        with patch.object(provider._client, "post", return_value=mock_response):
            candles = provider.fetch_candles("SOL-PERP", 1700000000, 1700003600)
        assert len(candles) == 2
        assert candles[0].market == "SOL-PERP"
        assert candles[0].venue == "hyperliquid"
        assert candles[0].open == 150.0
        assert candles[0].close == 153.0
        assert candles[0].ts == 1700000000
        provider.close()

    def test_unknown_market_returns_empty(self):
        provider = HyperliquidCandleProvider()
        candles = provider.fetch_candles("UNKNOWN-PERP", 1700000000, 1700003600)
        assert candles == []
        provider.close()

    def test_pagination(self):
        provider = HyperliquidCandleProvider()
        # First call returns 5000 candles, second returns 100 (< 5000 → done)
        first_batch = [
            {"t": 1700000000000 + i * 60000, "T": 1700000000000 + (i + 1) * 60000,
             "s": "SOL", "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "100.0"}
            for i in range(5000)
        ]
        second_batch = [
            {"t": 1700000000000 + 5000 * 60000 + i * 60000, "T": 1700000000000 + 5001 * 60000 + i * 60000,
             "s": "SOL", "o": "153.0", "h": "156.0", "l": "152.0", "c": "154.0", "v": "80.0"}
            for i in range(100)
        ]
        mock_resp1 = MagicMock()
        mock_resp1.status_code = 200
        mock_resp1.json.return_value = first_batch
        mock_resp2 = MagicMock()
        mock_resp2.status_code = 200
        mock_resp2.json.return_value = second_batch

        with patch.object(provider._client, "post", side_effect=[mock_resp1, mock_resp2]):
            candles = provider.fetch_candles("SOL-PERP", 1700000000, 1700400000)
        assert len(candles) == 5100
        provider.close()

    def test_resolution_parameter(self):
        provider = HyperliquidCandleProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"t": 1700000000000, "T": 1700003600000, "s": "SOL",
             "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "5000.0"},
        ]
        with patch.object(provider._client, "post", return_value=mock_response) as mock_post:
            candles = provider.fetch_candles("SOL-PERP", 1700000000, 1700003600, resolution="1h")
        call_json = mock_post.call_args.kwargs.get("json", mock_post.call_args[1].get("json"))
        assert call_json["req"]["interval"] == "1h"
        assert candles[0].resolution_s == 3600
        provider.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hyperliquid_candles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.providers.hyperliquid_candles'`

- [ ] **Step 3: Implement HyperliquidCandleProvider**

Create `flint/providers/hyperliquid_candles.py`:

```python
"""HyperliquidCandleProvider — historical candle data from Hyperliquid.

Uses sync HTTP (like HyperliquidFundingProvider) since this is for
batch downloads, not live trading.
"""
from __future__ import annotations

import logging
import time
from typing import List

import httpx

from ..models import Candle

logger = logging.getLogger("flint.hyperliquid_candles")

# Reuse the same symbol mapping
_FLINT_TO_HL = {
    "SOL-PERP": "SOL", "BTC-PERP": "BTC", "ETH-PERP": "ETH",
    "DOGE-PERP": "DOGE", "AVAX-PERP": "AVAX", "LINK-PERP": "LINK",
    "ARB-PERP": "ARB", "SUI-PERP": "SUI", "XRP-PERP": "XRP",
    "OP-PERP": "OP", "INJ-PERP": "INJ", "TIA-PERP": "TIA",
    "SEI-PERP": "SEI", "WIF-PERP": "WIF", "JUP-PERP": "JUP",
    "RENDER-PERP": "RENDER", "BNB-PERP": "BNB",
}

_INTERVAL_TO_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400,
}


class HyperliquidCandleProvider:
    """Fetch historical candles from Hyperliquid (always mainnet, free, no key)."""

    BASE_URL = "https://api.hyperliquid.xyz/info"

    def __init__(self):
        self._client = httpx.Client(timeout=15)

    def fetch_candles(
        self,
        market: str,
        start_ts: int,
        end_ts: int,
        resolution: str = "1m",
    ) -> List[Candle]:
        """Fetch historical candles.

        Args:
            market: Flint market symbol (e.g. "SOL-PERP").
            start_ts: Start time (unix seconds).
            end_ts: End time (unix seconds).
            resolution: Candle interval ("1m", "5m", "15m", "1h", "4h", "1d").

        Returns:
            List of Candle objects with venue="hyperliquid".
        """
        coin = _FLINT_TO_HL.get(market)
        if coin is None:
            logger.warning("Unknown market for Hyperliquid: %s", market)
            return []

        resolution_s = _INTERVAL_TO_SECONDS.get(resolution, 60)
        all_candles: List[Candle] = []
        cursor_start = start_ts * 1000  # Convert to ms

        for _ in range(200):  # Max pagination iterations
            try:
                resp = self._client.post(self.BASE_URL, json={
                    "type": "candleSnapshot",
                    "req": {
                        "coin": coin,
                        "interval": resolution,
                        "startTime": cursor_start,
                        "endTime": end_ts * 1000,
                    },
                })
                if resp.status_code != 200:
                    logger.warning("Hyperliquid candle API returned %d", resp.status_code)
                    break

                records = resp.json()
                if not isinstance(records, list) or not records:
                    break

                for r in records:
                    ts = r.get("t", 0) // 1000
                    all_candles.append(Candle(
                        ts=ts,
                        open=float(r.get("o", 0)),
                        high=float(r.get("h", 0)),
                        low=float(r.get("l", 0)),
                        close=float(r.get("c", 0)),
                        volume=float(r.get("v", 0)),
                        market=market,
                        resolution_s=resolution_s,
                        venue="hyperliquid",
                    ))

                if len(records) < 5000:
                    break

                # Advance cursor past last candle
                last_t = max(r.get("t", 0) for r in records)
                if last_t <= cursor_start:
                    break
                cursor_start = last_t + 1
                time.sleep(0.2)

            except Exception as e:
                logger.error("Hyperliquid candle fetch error: %s", e)
                break

        all_candles.sort(key=lambda c: c.ts)
        return all_candles

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hyperliquid_candles.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/providers/hyperliquid_candles.py tests/test_hyperliquid_candles.py
git commit -m "feat: add HyperliquidCandleProvider for historical backtest data"
```

---

### Task 8: Download Pipeline Integration

**Files:**
- Modify: `flint/api/routes/data.py`
- Test: Run existing data download tests + manual verification

- [ ] **Step 1: Read the download function to understand integration point**

Read `flint/api/routes/data.py` around the `_download_range` function (line ~638) to see where to add the Hyperliquid provider.

- [ ] **Step 2: Add Hyperliquid to download pipeline**

In `flint/api/routes/data.py`, inside `_download_range()`, after the existing Drift provider fallback chain, add a Hyperliquid fallback:

```python
    # Try Hyperliquid (if market is a known Hyperliquid market)
    try:
        from ...providers.hyperliquid_candles import HyperliquidCandleProvider, _FLINT_TO_HL
        if market in _FLINT_TO_HL:
            provider = HyperliquidCandleProvider()
            try:
                # Convert resolution_s to interval string
                _SEC_TO_INTERVAL = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}
                interval = _SEC_TO_INTERVAL.get(resolution_s, "1h")
                fetched = provider.fetch_candles(market, start_ts, end_ts, resolution=interval)
            finally:
                provider.close()
            if fetched:
                return fetched, None
    except Exception as e:
        errors.append(f"Hyperliquid: {e}")
        logger.warning("Hyperliquid failed for %s: %s", market, e)
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `pytest tests/ -k "data or download" -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add flint/api/routes/data.py
git commit -m "feat: add Hyperliquid to data download pipeline as fallback provider"
```

---

### Task 9: Integration Tests

**Files:**
- Create: `tests/test_hyperliquid_integration.py`

- [ ] **Step 1: Write integration tests**

Create `tests/test_hyperliquid_integration.py`:

```python
"""Integration tests for Hyperliquid — end-to-end flows with mocks."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from flint.models import Candle, Fill, Order, OrderType, OrderState, PositionInfo, Side
from flint.store import FlintStore


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestWsCandleToTick:
    """WS candle close → enqueue → tick → place order → fill."""

    def test_candle_triggers_tick(self):
        from flint.providers.hyperliquid_ws import HyperliquidWebSocketFeed

        closed = []
        feed = HyperliquidWebSocketFeed(
            markets=["SOL-PERP"],
            on_candle_close=lambda c: closed.append(c),
        )

        # Simulate two candle messages (first sets state, second closes first)
        run(feed._handle_message({
            "channel": "candle",
            "data": {"t": 1000000, "T": 1060000, "s": "SOL", "i": "1m",
                     "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "1000.0"},
        }))
        run(feed._handle_message({
            "channel": "candle",
            "data": {"t": 1060000, "T": 1120000, "s": "SOL", "i": "1m",
                     "o": "153.0", "h": "157.0", "l": "152.0", "c": "156.0", "v": "900.0"},
        }))

        assert len(closed) == 1
        candle = closed[0]
        assert candle.venue == "hyperliquid"
        assert candle.market == "SOL-PERP"


class TestOrderFlowEndToEnd:
    """Strategy places order → LiveHyperliquidContext submits → mock fill."""

    def test_market_order_dry_run(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext

        ctx = LiveHyperliquidContext(
            private_key="0x" + "ab" * 32,
            network="testnet",
            dry_run=True,
            initial_capital=10000.0,
        )

        # Set current candle for dry-run price
        ctx._current_candle = Candle(
            ts=1000, open=150.0, high=155.0, low=148.0, close=153.0,
            volume=100.0, market="SOL-PERP", resolution_s=60,
        )

        oid = ctx.market_order("SOL-PERP", Side.LONG, 10.0)
        assert oid != ""

        fills = run(ctx.submit_pending_orders())
        assert len(fills) == 1
        assert fills[0].venue == "hyperliquid"
        assert fills[0].tx_sig == "DRY_RUN"
        assert fills[0].price == 153.0
        assert fills[0].size == 10.0


class TestPositionParsing:
    """Clearinghouse state → PositionInfo list."""

    def test_mixed_positions(self):
        from flint.execution.hyperliquid_live import LiveHyperliquidContext

        ctx = LiveHyperliquidContext(private_key="0x" + "ab" * 32, network="testnet")
        mock_client = MagicMock()
        mock_client.address = "0x1234"
        mock_client.get_clearinghouse_state = AsyncMock(return_value={
            "marginSummary": {"accountValue": "15000.0"},
            "assetPositions": [
                {"position": {"coin": "SOL", "szi": "10.0", "entryPx": "150.0", "unrealizedPnl": "50.0"}},
                {"position": {"coin": "ETH", "szi": "-2.0", "entryPx": "3500.0", "unrealizedPnl": "-30.0"}},
                {"position": {"coin": "BTC", "szi": "0.0", "entryPx": "0", "unrealizedPnl": "0"}},
            ],
        })
        ctx._client = mock_client

        positions = run(ctx._fetch_positions())
        assert len(positions) == 2  # Zero-size BTC skipped
        sol = next(p for p in positions if p.market == "SOL-PERP")
        assert sol.side == Side.LONG
        eth = next(p for p in positions if p.market == "ETH-PERP")
        assert eth.side == Side.SHORT
        assert eth.size == 2.0


class TestStoreIntegration:
    """Verify store persistence works with Hyperliquid data."""

    def test_candle_persists_with_venue(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        candle = Candle(
            ts=1700000000, open=150.0, high=155.0, low=148.0, close=153.0,
            volume=1000.0, market="SOL-PERP", resolution_s=60, venue="hyperliquid",
        )
        store.upsert_candles([candle])
        result = store.query_candles("SOL-PERP", 60, 1699999000, 1700001000)
        assert len(result) >= 1
        store.close()
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_hyperliquid_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass (existing + new)

- [ ] **Step 4: Commit**

```bash
git add tests/test_hyperliquid_integration.py
git commit -m "test: add Hyperliquid integration tests (WS → tick, dry-run, positions, store)"
```

---

### Task 10: ROADMAP Update

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Update ROADMAP.md §2.1, §2.2, §2.3 with implementation notes**

Add "Implemented" sections after each §2 subsection, matching the Phase 1 pattern:

Under §2.1 (after the existing checklist items):

```markdown
**Implemented:**
- [x] `HyperliquidClient` async REST connector with EIP-712 signing (`flint/connectors/hyperliquid.py`)
- [x] Info endpoints: get_meta, get_clearinghouse_state, get_open_orders, get_user_fills, get_candle_snapshot, get_l2_book
- [x] Exchange endpoints: place_order, cancel_order, cancel_all_orders with signed requests
- [x] Market metadata caching (asset indices, tick sizes, lot sizes) from get_meta()
- [x] Precision formatting (format_size, format_price) per asset
- [x] Testnet/mainnet URL + chain ID toggle
- [x] `FLINT_HYPERLIQUID_PRIVATE_KEY` env var authentication (API wallet recommended)
```

Under §2.2:

```markdown
**Implemented:**
- [x] `LiveHyperliquidContext(LiveExecutionContext)` with all 7 abstract methods (`flint/execution/hyperliquid_live.py`)
- [x] Market order simulation via IOC limit with configurable slippage (default 0.3%)
- [x] Position parsing from clearinghouse state (long/short detection, zero filtering)
- [x] Balance extraction from marginSummary.accountValue
- [x] Order status polling: open orders → fills → cancelled fallback
- [x] `HyperliquidWebSocketFeed` with candle, L2 book, and orderUpdates channels (`flint/providers/hyperliquid_ws.py`)
- [x] All safety rails reused (kill switch, risk guards, dry-run mode)
```

Under §2.3:

```markdown
**Implemented:**
- [x] `HyperliquidCandleProvider` for historical candle data (`flint/providers/hyperliquid_candles.py`)
- [x] Pagination support (5000 candles per batch)
- [x] All 6 resolutions: 1m, 5m, 15m, 1h, 4h, 1d
- [x] Integrated into data download pipeline (`flint/api/routes/data.py`)
- [x] 17 markets supported via existing HYPERLIQUID_SYMBOLS mapping
```

- [ ] **Step 2: Verify the ROADMAP looks right**

Read the updated sections and check formatting consistency with Phase 1.

- [ ] **Step 3: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: update ROADMAP §2.1-§2.3 with Hyperliquid implementation notes"
```

---

## Task Dependencies

```
Task 1 (Config) ──────────────────┐
Task 2 (Client Info) ─────────────┤
                                   ├──→ Task 5 (Abstract Methods) ──→ Task 9 (Integration)
Task 3 (Client Signing) ──────────┤                                         │
Task 4 (Context Constructor) ─────┘                                         │
                                                                            ├──→ Task 10 (ROADMAP)
Task 6 (WebSocket Feed) ──────────────────────────────────────────────→ Task 9
Task 7 (Candle Provider) ────────→ Task 8 (Download Pipeline) ────────→ Task 9
```

**Parallelizable:** Tasks 1, 2, 6, 7 can run in parallel (no dependencies between them).
**Sequential:** Task 3 depends on Task 2. Task 5 depends on Tasks 2, 3, 4. Task 8 depends on Task 7. Task 9 depends on Tasks 5, 6, 8. Task 10 is last.
