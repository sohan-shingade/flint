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
import math
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
    """Async HTTP client for Hyperliquid REST API."""

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
        return self._account.address

    # --- Info endpoints ---

    async def get_meta(self) -> dict:
        resp = await self._http.post(f"{self._base_url}/info", json={"type": "meta"})
        resp.raise_for_status()
        return resp.json()

    async def get_clearinghouse_state(self, address: str) -> dict:
        resp = await self._http.post(
            f"{self._base_url}/info",
            json={"type": "clearinghouseState", "user": address},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_open_orders(self, address: str) -> list:
        resp = await self._http.post(
            f"{self._base_url}/info",
            json={"type": "openOrders", "user": address},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_user_fills(self, address: str, start_time: Optional[int] = None) -> list:
        payload: dict = {"type": "userFills", "user": address}
        if start_time is not None:
            payload["startTime"] = start_time
        resp = await self._http.post(f"{self._base_url}/info", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def get_candle_snapshot(self, coin: str, interval: str, start: int, end: int) -> list:
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
        resp = await self._http.post(
            f"{self._base_url}/info",
            json={"type": "l2Book", "coin": coin},
        )
        resp.raise_for_status()
        return resp.json()

    # --- Helpers ---

    def _build_asset_maps(self, meta: dict) -> None:
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
        info = self._asset_info.get(coin, {})
        decimals = info.get("szDecimals", 2)
        return f"{size:.{decimals}f}"

    def format_price(self, price: float) -> str:
        if price == 0:
            return "0"
        if price >= 1:
            int_digits = int(math.log10(price)) + 1
            decimals = max(0, 6 - int_digits)
        else:
            decimals = 6
        formatted = f"{price:.{decimals}f}"
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted

    # --- EIP-712 Signing ---

    def _sign_action(self, action: dict) -> tuple:
        """Sign an exchange action using EIP-712 phantom agent approach.
        Returns (signature_dict, nonce).
        """
        nonce = int(time.time() * 1000)
        action_bytes = self._action_hash(action, nonce)

        # Phantom agent typed data
        source = "a" if self._network == "mainnet" else "b"
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
                "source": source,
                "connectionId": action_bytes,
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
        """Compute action hash for signing."""
        import msgpack
        import hashlib
        data = msgpack.packb(action, use_bin_type=True)
        combined = data + nonce.to_bytes(8, "big") + b"\x00"
        return hashlib.sha256(combined).digest()

    # --- Exchange endpoints (write, signed) ---

    async def place_order(self, asset: int, is_buy: bool, size: str, price: str,
                          order_type: dict, reduce_only: bool = False) -> dict:
        action = {
            "type": "order",
            "orders": [{"a": asset, "b": is_buy, "p": price, "s": size, "r": reduce_only, "t": order_type}],
            "grouping": "na",
        }
        return await self._exchange_request(action)

    async def cancel_order(self, asset: int, oid: int) -> dict:
        action = {"type": "cancel", "cancels": [{"a": asset, "o": oid}]}
        return await self._exchange_request(action)

    async def cancel_all_orders(self, asset: Optional[int] = None) -> dict:
        if asset is not None:
            action = {"type": "cancel", "cancels": [{"a": asset, "o": -1}]}
        else:
            action = {"type": "cancelByCloid", "cancels": []}
        return await self._exchange_request(action)

    async def _exchange_request(self, action: dict) -> dict:
        signature, nonce = self._sign_action(action)
        payload = {"action": action, "nonce": nonce, "signature": signature}
        resp = await self._http.post(f"{self._base_url}/exchange", json=payload)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def parse_order_id(response: dict) -> Optional[int]:
        """Extract order ID from a place_order response."""
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

    async def close(self) -> None:
        await self._http.aclose()
