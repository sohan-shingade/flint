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
        raise NotImplementedError("Implemented in Task 5")

    async def _cancel_order(self, venue_order_id: int) -> bool:
        raise NotImplementedError("Implemented in Task 5")

    async def _fetch_positions(self) -> List[PositionInfo]:
        raise NotImplementedError("Implemented in Task 5")

    async def _fetch_balance(self) -> float:
        raise NotImplementedError("Implemented in Task 5")

    async def _poll_order_status(self, venue_order_id: int) -> OrderState:
        raise NotImplementedError("Implemented in Task 5")
