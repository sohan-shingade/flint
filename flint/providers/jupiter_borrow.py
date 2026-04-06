"""Jupiter Perps borrow rate data collectors.

Three collection strategies:
  - JupiterBorrowCollector  — forward collector; polls custody accounts via Solana RPC
  - DuneBorrowBackfill      — historical backfill via Dune Analytics decoded on-chain data
  - RpcBorrowBackfill       — archival fallback; reads custody accounts at historical slots

Jupiter Perps has no historical borrow rate API, so forward collection must run
continuously to accumulate a local dataset. Dune and archival RPC fills historical gaps.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from ..models import BorrowSnapshot
from .registry import DataProvider, register

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PERPS_PROGRAM_ID = "PERPHjGBqRHArX4DySjwM6UJHiR3sWAatqfdBS2qQJu"
POOL_ACCOUNT = "5BUwFW4nRbftYTDMbgxykoFWqWHPzahFSNAaaaJtVKsq"
PERP_MARKETS = ["SOL-PERP", "ETH-PERP", "BTC-PERP"]

# Custody account addresses per market (mainnet-beta)
_CUSTODY_ADDRESSES: Dict[str, str] = {
    "SOL-PERP": "7xS2gz2bTp3fwCC7knpjKGZZ9xQzCDdMaDEGKR8RkYFz",
    "ETH-PERP": "4qp6Fx6tnZkY5Wropq9wUYgtFxXKwE6viZxFHg3rdAG8",
    "BTC-PERP": "AQCGyheWPLeo6Qp9WpYS9m3Simy7T1K7yCFbOHMXjjQY",
}

_DUNE_API = "https://api.dune.com/api/v1"

# Mapping from Flint market symbol to custody asset name used in Dune queries
_MARKET_CUSTODY_NAMES: Dict[str, str] = {
    "SOL-PERP": "SOL",
    "ETH-PERP": "ETH",
    "BTC-PERP": "wBTC",
}

# Approximate Solana slots per second (used for timestamp→slot binary search)
_SLOTS_PER_SECOND = 2.5


# ---------------------------------------------------------------------------
# Class 1: JupiterBorrowCollector — forward collection via live RPC
# ---------------------------------------------------------------------------

class JupiterBorrowCollector:
    """Polls Jupiter Perps custody accounts via Solana JSON-RPC to collect
    borrow rate snapshots in real time.

    Intended to be called on a schedule (e.g. every 5 minutes).  The result
    list should be persisted to ``jupiter_borrow_rates`` in FlintStore.

    Parameters
    ----------
    rpc_url:
        Solana JSON-RPC endpoint (e.g. https://api.mainnet-beta.solana.com).
    client:
        Optional pre-built ``httpx.Client`` for dependency injection / testing.
    """

    def __init__(self, rpc_url: str, client: Optional[httpx.Client] = None) -> None:
        self._rpc_url = rpc_url
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    # -- public interface -----------------------------------------------------

    @property
    def markets(self) -> List[str]:
        """Return the list of supported perp markets."""
        return PERP_MARKETS

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _dbps_to_hourly(dbps: int) -> float:
        """Convert decimal basis points (dbps) to a fractional hourly rate.

        Jupiter stores ``hourlyFundingDbps`` as integer dbps where
        1 dbps = 0.00001 = 1e-5; however the on-chain encoding uses
        1_000_000 as the divisor so 80 dbps → 80/1_000_000 = 8e-5.
        """
        return dbps / 1_000_000

    def _parse_custody(self, market: str, data: dict, ts: int) -> BorrowSnapshot:
        """Parse a decoded custody account dict into a BorrowSnapshot.

        Parameters
        ----------
        market:
            Market symbol, e.g. ``"SOL-PERP"``.
        data:
            Decoded custody account JSON (from getAccountInfo → parsed value).
        ts:
            Unix timestamp (seconds) to stamp the snapshot with.
        """
        frs = data.get("fundingRateState", {})
        dbps = int(frs.get("hourlyFundingDbps", 0))
        cumulative = float(frs.get("cumulativeInterestRate", 0))

        assets = data.get("assets", {})
        owned = int(assets.get("owned", 0))
        locked = int(assets.get("locked", 0))
        utilization = locked / owned if owned > 0 else 0.0

        return BorrowSnapshot(
            market=market,
            ts=ts,
            rate_hourly=self._dbps_to_hourly(dbps),
            utilization=utilization,
            cumulative_rate=cumulative,
            source="rpc",
        )

    def _rpc_get_account(self, address: str) -> Optional[dict]:
        """Fetch and decode a Solana account via getAccountInfo JSON-RPC.

        Returns the parsed ``value`` dict on success, or ``None`` on any error.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [
                address,
                {"encoding": "jsonParsed", "commitment": "confirmed"},
            ],
        }
        try:
            resp = self._client.post(self._rpc_url, json=payload)
            resp.raise_for_status()
            body = resp.json()
            result = body.get("result", {}) or {}
            value = result.get("value") or {}
            parsed = value.get("data", {})
            if isinstance(parsed, dict):
                return parsed.get("parsed", {}).get("info") or parsed
            return None
        except Exception as exc:
            logger.warning("RPC getAccountInfo failed for %s: %s", address, exc)
            return None

    def collect(self) -> List[BorrowSnapshot]:
        """Collect a borrow rate snapshot for every market right now.

        Markets whose RPC call fails are silently skipped so a single
        unhealthy endpoint doesn't abort the whole collection run.
        """
        ts = int(time.time())
        snapshots: List[BorrowSnapshot] = []
        for market in PERP_MARKETS:
            address = _CUSTODY_ADDRESSES.get(market, "")
            data = self._rpc_get_account(address)
            if data is None:
                logger.warning("Skipping %s — custody account unavailable", market)
                continue
            try:
                snap = self._parse_custody(market, data, ts)
                snapshots.append(snap)
            except Exception as exc:
                logger.warning("Failed to parse custody for %s: %s", market, exc)
        return snapshots

    def close(self) -> None:
        """Release the underlying HTTP client if we own it."""
        if self._owns_client:
            self._client.close()


# ---------------------------------------------------------------------------
# Class 2: DuneBorrowBackfill — historical backfill via Dune Analytics
# ---------------------------------------------------------------------------

class DuneBorrowBackfill:
    """Queries Dune Analytics for decoded Jupiter Perps custody state history.

    Dune community queries expose ``hourlyFundingDbps``, ``cumulativeInterestRate``,
    and utilization per block time.  This class wraps those queries so historical
    gaps can be filled without running an archival RPC node.

    Requires a Dune API key (free tier: 1000 credits / month).

    Parameters
    ----------
    api_key:
        Dune Analytics API key (``FLINT_DUNE_API_KEY`` in ``.env``).
    client:
        Optional pre-built ``httpx.Client`` for testing.
    """

    def __init__(self, api_key: str, client: Optional[httpx.Client] = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(
            timeout=120,  # Dune queries can be slow
            headers={"X-Dune-API-Key": api_key} if api_key else {},
        )
        self._owns_client = client is None

    # -- public interface -----------------------------------------------------

    def is_available(self) -> bool:
        """Return True when a Dune API key is configured."""
        return bool(self._api_key)

    def _build_query(self, market: str, start_ts: int, end_ts: int) -> str:
        """Build a Dune SQL query string for a given market and time range.

        Parameters
        ----------
        market:
            Market symbol, e.g. ``"SOL-PERP"``.
        start_ts, end_ts:
            Unix timestamps (seconds) bounding the query window.
        """
        custody_name = _MARKET_CUSTODY_NAMES.get(market, market.replace("-PERP", ""))
        return (
            f"SELECT "
            f"  '{market}' AS market, "
            f"  block_time, "
            f"  hourly_funding_dbps, "
            f"  cumulative_interest_rate, "
            f"  CAST(locked_amount AS DOUBLE) / NULLIF(owned_amount, 0) AS utilization "
            f"FROM jupiter_perps.custody_state "
            f"WHERE custody_name = '{custody_name}' "
            f"  AND block_time >= from_unixtime({start_ts}) "
            f"  AND block_time <= from_unixtime({end_ts}) "
            f"ORDER BY block_time ASC"
        )

    def _parse_response(self, response: dict) -> List[BorrowSnapshot]:
        """Convert a raw Dune API response dict into BorrowSnapshot objects."""
        rows = response.get("result", {}).get("rows", [])
        snapshots: List[BorrowSnapshot] = []
        for row in rows:
            try:
                block_time_str = row["block_time"]
                # Parse ISO-8601 timestamp (Dune always returns UTC)
                dt = datetime.fromisoformat(block_time_str.replace("Z", "+00:00"))
                ts = int(dt.timestamp())

                dbps = int(row.get("hourly_funding_dbps", 0))
                rate_hourly = dbps / 1_000_000

                snapshots.append(
                    BorrowSnapshot(
                        market=row["market"],
                        ts=ts,
                        rate_hourly=rate_hourly,
                        utilization=float(row.get("utilization", 0.0) or 0.0),
                        cumulative_rate=float(row.get("cumulative_interest_rate", 0)),
                        source="dune",
                    )
                )
            except Exception as exc:
                logger.warning("Failed to parse Dune row %s: %s", row, exc)
        return snapshots

    def fetch(self, market: str, start_ts: int, end_ts: int) -> List[BorrowSnapshot]:
        """Execute a Dune query and return parsed BorrowSnapshots.

        This uses Dune's ``/query/execute`` + ``/execution/{id}/results``
        polling pattern.  Returns an empty list if the API is unavailable
        or the query fails.

        Parameters
        ----------
        market:
            Market symbol, e.g. ``"SOL-PERP"``.
        start_ts, end_ts:
            Unix timestamp bounds for the query window.
        """
        if not self.is_available():
            logger.warning("DuneBorrowBackfill: no API key configured")
            return []

        sql = self._build_query(market, start_ts, end_ts)
        headers = {"X-Dune-Api-Key": self._api_key}
        try:
            # Submit query execution via SQL API
            exec_resp = self._client.post(
                f"{_DUNE_API}/sql/execute",
                headers=headers,
                json={"sql": sql, "performance": "medium"},
            )
            exec_resp.raise_for_status()
            execution_id = exec_resp.json().get("execution_id", "")
            if not execution_id:
                logger.warning("Dune: no execution_id in response")
                return []

            # Poll for results
            for _ in range(30):
                result_resp = self._client.get(
                    f"{_DUNE_API}/execution/{execution_id}/results",
                    headers=headers,
                )
                result_resp.raise_for_status()
                body = result_resp.json()
                state = body.get("state", "")
                if state == "QUERY_STATE_COMPLETED":
                    return self._parse_response(body)
                if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
                    logger.warning("Dune query failed: %s", state)
                    return []
                time.sleep(2)

            logger.warning("Dune query timed out for market=%s", market)
            return []

        except Exception as exc:
            logger.warning("DuneBorrowBackfill.fetch error: %s", exc)
            return []

    def close(self) -> None:
        """Release the underlying HTTP client if we own it."""
        if self._owns_client:
            self._client.close()


# ---------------------------------------------------------------------------
# Class 2b: DuneVolumeBackfill — historical Jupiter Perps volume via Dune
# ---------------------------------------------------------------------------

class DuneVolumeBackfill:
    """Queries Dune Analytics for hourly Jupiter Perps trade volume.

    Returns Candle objects with volume data (OHLC set to 0 — volume-only).
    Store with ``venue="jupiter"`` alongside CEX volume venues.

    Requires a Dune API key (free tier: 1000 credits / month).
    """

    def __init__(self, api_key: str, client: Optional[httpx.Client] = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(
            timeout=120,
            headers={"X-Dune-API-Key": api_key} if api_key else {},
        )
        self._owns_client = client is None

    def is_available(self) -> bool:
        return bool(self._api_key)

    def _build_query(self, market: str, start_ts: int, end_ts: int) -> str:
        custody_name = _MARKET_CUSTODY_NAMES.get(market, market.replace("-PERP", ""))
        # NOTE: jupiter_perps.trades is not yet decoded on Dune as of 2026-04.
        # This query will fail until the Dune community adds Jupiter Perps decoding.
        # Alternative: use Helius Enhanced Transactions API for historical trade parsing.
        return (
            f"SELECT "
            f"  '{market}' AS market, "
            f"  date_trunc('hour', block_time) AS hour, "
            f"  SUM(size_usd) AS volume_usd, "
            f"  COUNT(*) AS trade_count, "
            f"  MIN(price) AS low_price, "
            f"  MAX(price) AS high_price, "
            f"  (array_agg(price ORDER BY block_time ASC))[1] AS open_price, "
            f"  (array_agg(price ORDER BY block_time DESC))[1] AS close_price "
            f"FROM jupiter_perps.trades "
            f"WHERE market = '{custody_name}' "
            f"  AND block_time >= from_unixtime({start_ts}) "
            f"  AND block_time <= from_unixtime({end_ts}) "
            f"GROUP BY 1, 2 "
            f"ORDER BY 2 ASC"
        )

    def fetch(self, market: str, start_ts: int, end_ts: int) -> list:
        """Execute Dune query and return Candle objects with Jupiter volume.

        Returns list of Candle objects with venue="jupiter".
        OHLC prices are from Jupiter's own trade data (may differ from oracle).
        """
        from ..models import Candle

        if not self.is_available():
            logger.warning("DuneVolumeBackfill: no API key configured")
            return []

        sql = self._build_query(market, start_ts, end_ts)
        headers = {"X-Dune-Api-Key": self._api_key}
        try:
            exec_resp = self._client.post(
                f"{_DUNE_API}/sql/execute",
                headers=headers,
                json={"sql": sql, "performance": "medium"},
            )
            exec_resp.raise_for_status()
            execution_id = exec_resp.json().get("execution_id", "")
            if not execution_id:
                return []

            for _ in range(30):
                result_resp = self._client.get(
                    f"{_DUNE_API}/execution/{execution_id}/results",
                    headers=headers,
                )
                result_resp.raise_for_status()
                body = result_resp.json()
                state = body.get("state", "")
                if state == "QUERY_STATE_COMPLETED":
                    return self._parse_volume(body, market)
                if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
                    logger.warning("Dune volume query failed: %s", state)
                    return []
                time.sleep(2)

            logger.warning("Dune volume query timed out for %s", market)
            return []

        except Exception as exc:
            logger.warning("DuneVolumeBackfill.fetch error: %s", exc)
            return []

    def _parse_volume(self, response: dict, market: str) -> list:
        from ..models import Candle

        rows = response.get("result", {}).get("rows", [])
        candles = []
        for row in rows:
            try:
                hour_str = row["hour"]
                dt = datetime.fromisoformat(hour_str.replace("Z", "+00:00"))
                ts = int(dt.timestamp())

                volume = float(row.get("volume_usd", 0))
                open_p = float(row.get("open_price", 0) or 0)
                high_p = float(row.get("high_price", 0) or 0)
                low_p = float(row.get("low_price", 0) or 0)
                close_p = float(row.get("close_price", 0) or 0)

                candles.append(Candle(
                    ts=ts,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    volume=volume,
                    market=market,
                    resolution_s=3600,
                    venue="jupiter",
                ))
            except Exception as exc:
                logger.warning("Failed to parse Dune volume row: %s", exc)
        return candles

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


# ---------------------------------------------------------------------------
# Class 3: RpcBorrowBackfill — archival RPC slot-based backfill
# ---------------------------------------------------------------------------

class RpcBorrowBackfill:
    """Reads Jupiter Perps custody accounts at historical Solana slots.

    Uses ``getBlockTime`` to map slot→timestamp and a binary search to map
    timestamp→slot, then calls ``getAccountInfo`` with ``minContextSlot`` to
    read the account state at that point in history.

    Requires an archival RPC node (standard public endpoints only retain
    ~2 days of state).  Triton, Helius, QuikNode etc. offer archival access.

    Parameters
    ----------
    rpc_url:
        Solana JSON-RPC endpoint with archival access.
    client:
        Optional pre-built ``httpx.Client`` for testing.
    """

    def __init__(self, rpc_url: str, client: Optional[httpx.Client] = None) -> None:
        self._rpc_url = rpc_url
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    # -- public interface -----------------------------------------------------

    def is_available(self) -> bool:
        """Return True when an RPC URL is configured."""
        return bool(self._rpc_url)

    # -- low-level RPC helpers ------------------------------------------------

    def _rpc_call(self, method: str, params: list) -> Optional[dict]:
        """Issue a raw JSON-RPC call and return the parsed body, or None on error."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            resp = self._client.post(self._rpc_url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.debug("RPC %s failed: %s", method, exc)
            return None

    def _get_block_time(self, slot: int) -> Optional[int]:
        """Return the Unix timestamp for a given slot, or None on error."""
        body = self._rpc_call("getBlockTime", [slot])
        if body is None:
            return None
        result = body.get("result")
        if result is None or isinstance(result, dict):
            return None
        return int(result)

    def _get_account_at_slot(self, address: str, slot: int) -> Optional[dict]:
        """Fetch and decode a custody account as it was at *slot*.

        Returns the parsed info dict, or None if unavailable.
        """
        body = self._rpc_call(
            "getAccountInfo",
            [
                address,
                {
                    "encoding": "jsonParsed",
                    "commitment": "confirmed",
                    "minContextSlot": slot,
                },
            ],
        )
        if body is None:
            return None
        try:
            value = (body.get("result") or {}).get("value") or {}
            parsed = value.get("data", {})
            if isinstance(parsed, dict):
                return parsed.get("parsed", {}).get("info") or parsed
            return None
        except Exception as exc:
            logger.debug("Failed to decode account at slot %d: %s", slot, exc)
            return None

    def _slot_for_timestamp(self, target_ts: int, hint_slot: int = 200_000_000) -> int:
        """Binary-search for the slot whose block time is closest to *target_ts*.

        Uses the hint_slot as the upper bound starting point and Solana's
        ~400 ms/slot cadence to estimate bounds, then refines with getBlockTime
        calls.

        Parameters
        ----------
        target_ts:
            Unix timestamp (seconds) we want to map to a slot.
        hint_slot:
            A recent slot to anchor the search (defaults to ~200M, circa 2024).
        """
        # Estimate a starting slot based on slot cadence
        hint_ts = self._get_block_time(hint_slot)
        if hint_ts is None:
            # Fall back: rough estimate using 400ms/slot from genesis (~0)
            estimated_delta = target_ts  # seconds from genesis
            return max(0, int(estimated_delta * _SLOTS_PER_SECOND))

        delta_s = target_ts - hint_ts
        estimated_slot = max(0, hint_slot + int(delta_s * _SLOTS_PER_SECOND))

        # Binary search: narrow to within ~10 slots (~4 seconds)
        lo = max(0, estimated_slot - 5000)
        hi = estimated_slot + 5000
        best_slot = estimated_slot

        for _ in range(20):  # at most 20 RPC round-trips
            if hi - lo <= 10:
                break
            mid = (lo + hi) // 2
            mid_ts = self._get_block_time(mid)
            if mid_ts is None:
                # slot skipped; nudge toward center
                lo = mid + 1
                continue
            if mid_ts < target_ts:
                lo = mid + 1
                best_slot = mid
            elif mid_ts > target_ts:
                hi = mid - 1
            else:
                return mid  # exact match

        return best_slot

    # -- public fetch ---------------------------------------------------------

    def fetch(
        self,
        market: str,
        custody_address: str,
        timestamps: List[int],
    ) -> List[BorrowSnapshot]:
        """Fetch borrow snapshots for *market* at each timestamp in *timestamps*.

        Parameters
        ----------
        market:
            Market symbol, e.g. ``"SOL-PERP"``.
        custody_address:
            On-chain custody account public key for this market.
        timestamps:
            List of Unix timestamps (seconds) to read historical state at.
        """
        snapshots: List[BorrowSnapshot] = []
        hint_slot = 200_000_000  # refresh per call as we scan forward

        for ts in timestamps:
            try:
                slot = self._slot_for_timestamp(ts, hint_slot=hint_slot)
                # Update hint so subsequent searches start closer to target
                hint_slot = slot
                data = self._get_account_at_slot(custody_address, slot)
                if data is None:
                    logger.debug("No account data at slot %d for %s", slot, market)
                    continue

                frs = data.get("fundingRateState", {})
                dbps = int(frs.get("hourlyFundingDbps", 0))
                cumulative = float(frs.get("cumulativeInterestRate", 0))
                assets = data.get("assets", {})
                owned = int(assets.get("owned", 0))
                locked = int(assets.get("locked", 0))
                utilization = locked / owned if owned > 0 else 0.0

                snapshots.append(
                    BorrowSnapshot(
                        market=market,
                        ts=ts,
                        rate_hourly=dbps / 1_000_000,
                        utilization=utilization,
                        cumulative_rate=cumulative,
                        source="rpc",
                    )
                )
            except Exception as exc:
                logger.warning("RpcBorrowBackfill: error at ts=%d for %s: %s", ts, market, exc)

        return snapshots

    def close(self) -> None:
        """Release the underlying HTTP client if we own it."""
        if self._owns_client:
            self._client.close()


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------

@register
class JupiterBorrowProvider(DataProvider):
    """Registry entry for Jupiter Perps borrow rate data.

    Instantiate JupiterBorrowCollector / DuneBorrowBackfill / RpcBorrowBackfill
    directly — this class exists solely to make the provider discoverable via
    the registry (e.g. ``flint.yaml`` ``providers.jupiter_borrow.enabled``).
    """

    name = "jupiter_borrow"
    requires_api_key = False

    def supported_data_types(self) -> List[str]:
        return ["borrow"]

    def close(self) -> None:
        pass
