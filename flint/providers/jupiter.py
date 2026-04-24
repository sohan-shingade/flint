"""Jupiter Price & Quote API provider.

- Quote API (v6): https://quote-api.jup.ag/v6/quote
- Swap API: https://quote-api.jup.ag/v6/swap
"""
from __future__ import annotations


# Phase 1 T1.3.a + D-1.3-providers — point-in-time declaration.
# Defaults are conservative — callers should verify against the
# specific source API when using this data in parity/PIT-sensitive
# contexts. Review date: 2026-04-24.
PIT_METADATA = {
    "candle_ts": "bar-close",
    "funding_ts": "exchange-time",
    "orderbook_ts": "exchange-time",
    "oi_ts": "exchange-time",
    "reviewed": "2026-04-24",
}

from typing import Dict, List, Optional

import httpx

_QUOTE_API = "https://quote-api.jup.ag/v6"

# Well-known SPL token mints
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"


class JupiterProvider:
    """Jupiter quote + price provider."""

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 50,
    ) -> Dict:
        """Get a swap quote from Jupiter.

        ``amount`` is in the input token's smallest unit (lamports for SOL,
        i.e. 1 SOL = 1_000_000_000).
        """
        resp = self._client.get(
            f"{_QUOTE_API}/quote",
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "slippageBps": str(slippage_bps),
            },
        )
        resp.raise_for_status()
        return resp.json()

    def get_price_sol_usdc(self, amount_sol: float = 1.0) -> float:
        """Convenience: get SOL price in USDC."""
        lamports = int(amount_sol * 1_000_000_000)
        quote = self.get_quote(SOL_MINT, USDC_MINT, lamports)
        out_amount = int(quote.get("outAmount", 0))
        return out_amount / 1_000_000  # USDC has 6 decimals

    def get_swap_routes(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 50,
    ) -> List[Dict]:
        """Return the list of route plans from a Jupiter quote."""
        quote = self.get_quote(input_mint, output_mint, amount, slippage_bps)
        return quote.get("routePlan", [])
