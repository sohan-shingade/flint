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
from typing import Dict, List, Optional, Tuple

from ..models import (
    Order, OrderState, OrderType, PositionInfo, Side,
)
from .live_base import LiveExecutionContext

logger = logging.getLogger("flint.hyperliquid_live")

# Flint symbol <-> Hyperliquid coin mapping
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
        self._venue_order_to_asset: Dict[int, int] = {}

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

    # --- Abstract method stubs (implemented in Task 5) ---

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
            order_type = {"trigger": {"triggerPx": price_str, "isMarket": True, "tpsl": "sl"}}
        elif order.order_type == OrderType.TAKE_PROFIT:
            size_str = self._client.format_size(coin, order.size)
            price_str = self._client.format_price(order.price)
            order_type = {"trigger": {"triggerPx": price_str, "isMarket": True, "tpsl": "tp"}}
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
