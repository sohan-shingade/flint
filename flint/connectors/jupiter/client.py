"""Jupiter connector — quote and swap via Jupiter's Aggregator API."""
from __future__ import annotations

from typing import Dict, List, Optional

import httpx

from ...models import MarketInfo, Order, OrderbookSnapshot, Side
from ...providers.jupiter import JupiterProvider, SOL_MINT, USDC_MINT
from ..base import BaseConnector

# Jupiter doesn't have a traditional orderbook, but we can use quotes
# to simulate price impact at different sizes.


class JupiterConnector(BaseConnector):
    """Jupiter DEX aggregator connector.

    Provides swap quotes and price discovery. Does not support limit orders
    or streaming (Jupiter is a swap router, not an orderbook exchange).
    """

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._provider = JupiterProvider(client=client)
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    @property
    def protocol(self) -> str:
        return "jupiter"

    async def get_markets(self) -> List[MarketInfo]:
        # Jupiter supports any SPL token pair — return the common ones
        return [
            MarketInfo("SOL/USDC", 0, "", 0.01, 0.01, 0.0, 0.0),
            MarketInfo("SOL/USDT", 0, "", 0.01, 0.01, 0.0, 0.0),
        ]

    async def get_orderbook(self, market: str) -> OrderbookSnapshot:
        raise NotImplementedError("Jupiter is a swap router, not an orderbook exchange.")

    async def get_price(self, market: str) -> float:
        """Get price by executing a small quote.

        ``market`` should be 'SOL/USDC' format.
        """
        return self._provider.get_price_sol_usdc()

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 50,
    ) -> Dict:
        return self._provider.get_quote(input_mint, output_mint, amount, slippage_bps)

    async def get_routes(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
    ) -> List[Dict]:
        return self._provider.get_swap_routes(input_mint, output_mint, amount)

    # -- write operations (require wallet + transaction signing) ---------------

    async def place_order(self, order: Order) -> Order:
        raise NotImplementedError(
            "Jupiter swap execution requires a signed Solana transaction. "
            "Use get_quote() for price discovery in backtesting."
        )

    async def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("Jupiter swaps are atomic — no cancellation.")

    async def get_positions(self) -> List[Dict]:
        raise NotImplementedError("Jupiter is a swap router — positions are in your wallet.")

    async def get_balances(self) -> Dict[str, float]:
        raise NotImplementedError("Read balances via Solana RPC, not Jupiter.")

    async def close(self) -> None:
        self._provider.close()
