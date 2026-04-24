"""Helius provider — parsed Solana transactions, whale tracking, liquidation detection.

API docs: https://docs.helius.dev
Free tier: 100k credits/day, no credit card required.
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

import logging
import time
from typing import Dict, List, Optional

import httpx

from ..models import Liquidation, WhaleTransfer
from .registry import DataProvider, register

logger = logging.getLogger(__name__)

_BASE = "https://api.helius.xyz/v0"

DRIFT_PROGRAM = "dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH"

# Minimum transfer amount (USD) to count as "whale"
WHALE_THRESHOLD_USD = 100_000


@register
class HeliusProvider(DataProvider):
    """Fetches parsed transactions, liquidation events, and whale transfers
    from the Helius Enhanced Transactions API."""

    name = "helius"
    requires_api_key = True
    supported_data_types = ["liquidations", "whale_transfers", "transactions"]

    def __init__(
        self,
        api_key: str = "",
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    # -- availability ---------------------------------------------------------

    def is_available(self) -> bool:
        """Provider is available only when an API key is configured."""
        return bool(self._api_key)

    # -- parsed transactions --------------------------------------------------

    def fetch_parsed_transactions(
        self,
        address: str,
        limit: int = 100,
        before: str = "",
    ) -> list:
        """Fetch parsed transactions for an address.

        GET /addresses/{address}/transactions?api-key=KEY&limit=N[&before=SIG]
        """
        params: Dict[str, object] = {
            "api-key": self._api_key,
            "limit": limit,
        }
        if before:
            params["before"] = before

        try:
            resp = self._client.get(
                f"{_BASE}/addresses/{address}/transactions",
                params=params,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Helius API returned %d for %s", resp.status_code, address)
        except Exception as e:
            logger.error("Helius parsed tx error: %s", e)
        return []

    # -- Drift liquidations ---------------------------------------------------

    def fetch_drift_liquidations(
        self,
        start_ts: int,
        end_ts: int,
        market: str = "SOL-PERP",
    ) -> List[Liquidation]:
        """Fetch Drift Protocol liquidation events via parsed history.

        Paginates through Drift program transactions and filters for entries
        whose description contains "liquidat".
        """
        all_txs: list = []
        before = ""
        max_pages = 50

        for _ in range(max_pages):
            txs = self.fetch_parsed_transactions(
                address=DRIFT_PROGRAM,
                limit=100,
                before=before,
            )
            if not txs:
                break
            all_txs.extend(txs)

            # Stop paginating once we've passed the start boundary
            oldest_ts = min(tx.get("timestamp", 0) for tx in txs)
            if oldest_ts < start_ts:
                break

            before = txs[-1].get("signature", "")
            time.sleep(0.1)

        events = self._parse_drift_events(all_txs)
        liquidations = events.get("liquidations", [])
        return [
            liq for liq in liquidations
            if start_ts <= liq.ts <= end_ts
        ]

    # -- whale / token transfers ----------------------------------------------

    def fetch_token_transfers(
        self,
        mint: str,
        start_ts: int,
        end_ts: int,
        min_amount: float = 0,
    ) -> List[WhaleTransfer]:
        """Fetch large token transfers for a specific mint.

        Returns WhaleTransfer objects for each transfer leg (sender gets
        direction="out", receiver gets direction="in").
        """
        transfers: List[WhaleTransfer] = []
        before = ""
        max_pages = 20

        for _ in range(max_pages):
            params: Dict[str, object] = {
                "api-key": self._api_key,
                "limit": 100,
            }
            if before:
                params["before"] = before

            try:
                resp = self._client.get(
                    f"{_BASE}/addresses/{mint}/transactions",
                    params=params,
                )
                if resp.status_code != 200:
                    logger.warning("Helius API returned %d", resp.status_code)
                    break

                txs = resp.json()
                if not txs:
                    break

                for tx in txs:
                    ts = tx.get("timestamp", 0)
                    if ts < start_ts or ts > end_ts:
                        continue

                    for tt in tx.get("tokenTransfers", []):
                        amount = float(tt.get("tokenAmount", 0))
                        if amount < min_amount:
                            continue

                        from_wallet = tt.get("fromUserAccount", "")
                        to_wallet = tt.get("toUserAccount", "")
                        token = tt.get("mint", mint)
                        sig = tx.get("signature", "")

                        if from_wallet:
                            transfers.append(WhaleTransfer(
                                wallet=from_wallet,
                                token_mint=token,
                                amount=amount,
                                ts=ts,
                                direction="out",
                                tx_sig=sig,
                            ))
                        if to_wallet:
                            transfers.append(WhaleTransfer(
                                wallet=to_wallet,
                                token_mint=token,
                                amount=amount,
                                ts=ts,
                                direction="in",
                                tx_sig=sig,
                            ))

                oldest_ts = min(tx.get("timestamp", 0) for tx in txs)
                if oldest_ts < start_ts:
                    break
                before = txs[-1].get("signature", "")
                time.sleep(0.1)

            except Exception as e:
                logger.error("Helius transfer fetch error: %s", e)
                break

        return transfers

    # -- internal parsers -----------------------------------------------------

    def _parse_drift_events(self, txs: list) -> Dict[str, list]:
        """Parse Drift program transactions into structured events.

        Currently detects liquidation events by searching for "liquidat"
        in the transaction description.
        """
        liquidations: List[Liquidation] = []

        for tx in txs:
            desc = (tx.get("description", "") or "").lower()
            ts = tx.get("timestamp", 0)
            sig = tx.get("signature", "")

            if "liquidat" in desc:
                liquidations.append(Liquidation(
                    market="SOL-PERP",  # Drift events don't always specify market
                    ts=ts,
                    side="long" if "long" in desc else "short",
                    size=0.0,  # Would need deeper event parsing
                    price=0.0,
                    slot=0,
                    tx_sig=sig,
                ))

        return {"liquidations": liquidations}

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
