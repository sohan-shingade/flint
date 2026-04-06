"""Tardis.dev historical L2 orderbook snapshot provider.

Downloads book_snapshot_25 CSV files (gzip-compressed) from
https://datasets.tardis.dev/v1 for CEX perpetual markets (Binance,
OKX, Bybit).  Requires a Tardis API key
(https://tardis.dev/subscription).

Each fetched file covers one calendar day for one exchange/symbol.
Rows are deduplicated and aligned to 5-minute buckets to match the
granularity stored in the Flint DuckDB ``orderbook_snapshots`` table.
"""

import csv
import gzip
import io
import logging
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_DATASETS_URL = "https://datasets.tardis.dev/v1"

# Tardis exchange slugs for the venues Flint cares about
_EXCHANGE_MAP: Dict[str, str] = {
    "binance": "binance-futures",
    "okx": "okx-swap",
    "bybit": "bybit",
}

# Map Flint market names → Tardis instrument symbols per exchange
_SYMBOL_MAP: Dict[str, Dict[str, str]] = {
    "SOL-PERP": {
        "binance-futures": "SOLUSDT",
        "okx-swap": "SOL-USDT-SWAP",
        "bybit": "SOLUSDT",
    },
    "BTC-PERP": {
        "binance-futures": "BTCUSDT",
        "okx-swap": "BTC-USDT-SWAP",
        "bybit": "BTCUSDT",
    },
    "ETH-PERP": {
        "binance-futures": "ETHUSDT",
        "okx-swap": "ETH-USDT-SWAP",
        "bybit": "ETHUSDT",
    },
}


class TardisOrderbookProvider:
    """Download historical L2 snapshots from Tardis.dev datasets API.

    Parameters
    ----------
    api_key:
        Tardis API key.  When empty, :meth:`is_available` returns
        ``False`` and :meth:`fetch` returns an empty list immediately.
    max_gb:
        Soft cap on data volume (currently informational; enforced by
        callers that check response size before storing).
    client:
        Optional pre-built ``httpx.Client``.  When *None* (default) the
        provider creates and owns its own client and closes it on
        :meth:`close`.
    """

    def __init__(
        self,
        api_key: str,
        max_gb: float = 1.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._api_key = api_key
        self._max_gb = max_gb
        self._client = client or httpx.Client(timeout=120)
        self._owns_client = client is None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return *True* iff an API key is configured."""
        return bool(self._api_key)

    def close(self) -> None:
        """Close the underlying HTTP client (only when owned by this instance)."""
        if self._owns_client:
            self._client.close()

    def fetch(self, venue: str, market: str, date: str) -> List[dict]:
        """Fetch L2 snapshots for *venue*/*market* on *date*.

        Parameters
        ----------
        venue:
            Flint venue key: ``"binance"``, ``"okx"``, or ``"bybit"``.
        market:
            Flint market symbol, e.g. ``"SOL-PERP"``.
        date:
            Calendar date in ``YYYY/MM/DD`` format (Tardis path convention).

        Returns
        -------
        list of dict
            Each dict has keys: ``venue``, ``market``, ``ts`` (unix
            seconds, 5-min aligned), ``ask_prices``, ``ask_sizes``,
            ``bid_prices``, ``bid_sizes``.
        """
        if not self.is_available():
            return []

        exchange = _EXCHANGE_MAP.get(venue)
        if not exchange:
            logger.debug("Tardis: unknown venue %r", venue)
            return []

        symbol = _SYMBOL_MAP.get(market, {}).get(exchange)
        if not symbol:
            logger.debug("Tardis: no symbol mapping for market %r on %r", market, exchange)
            return []

        url = f"{_DATASETS_URL}/{exchange}/book_snapshot_25/{date}/{symbol}.csv.gz"
        try:
            resp = self._client.get(
                url,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            resp.raise_for_status()
            csv_text = gzip.decompress(resp.content).decode("utf-8")
            return self._parse_csv(csv_text, venue, market)
        except Exception as exc:
            logger.warning("Tardis fetch failed for %s/%s on %s: %s", venue, market, date, exc)
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _align_ts(ts: int) -> int:
        """Align a unix-second timestamp down to the nearest 5-minute boundary."""
        return ts - (ts % 300)

    def _parse_csv(
        self,
        csv_text: str,
        venue: str,
        market: str,
        num_levels: int = 25,
    ) -> List[dict]:
        """Parse a Tardis book_snapshot_25 CSV into snapshot dicts.

        Parameters
        ----------
        csv_text:
            Decoded CSV content (header row + data rows).
        venue:
            Venue label to embed in each snapshot dict.
        market:
            Market label to embed in each snapshot dict.
        num_levels:
            Maximum number of price levels to read from each row.

        Returns
        -------
        list of dict
            De-duplicated snapshots (one per 5-min bucket, first row wins).
        """
        reader = csv.DictReader(io.StringIO(csv_text))
        snapshots: List[dict] = []
        seen_ts: set = set()

        for row in reader:
            # Tardis timestamps are microseconds
            ts_us = int(row["timestamp"])
            ts_s = self._align_ts(ts_us // 1_000_000)

            # One snapshot per 5-min bucket (first row wins)
            if ts_s in seen_ts:
                continue
            seen_ts.add(ts_s)

            ask_prices: List[float] = []
            ask_sizes: List[float] = []
            bid_prices: List[float] = []
            bid_sizes: List[float] = []

            for i in range(num_levels):
                ap = row.get(f"ask_price_{i}", "")
                aa = row.get(f"ask_amount_{i}", "")
                if ap and ap.strip():
                    ask_prices.append(float(ap))
                    ask_sizes.append(float(aa))

                bp = row.get(f"bid_price_{i}", "")
                ba = row.get(f"bid_amount_{i}", "")
                if bp and bp.strip():
                    bid_prices.append(float(bp))
                    bid_sizes.append(float(ba))

            snapshots.append(
                {
                    "venue": venue,
                    "market": market,
                    "ts": ts_s,
                    "ask_prices": ask_prices,
                    "ask_sizes": ask_sizes,
                    "bid_prices": bid_prices,
                    "bid_sizes": bid_sizes,
                }
            )

        return snapshots
