"""Hyperliquid public S3 archive orderbook snapshot provider.

Downloads L2 book snapshots from the Hyperliquid public S3 bucket::

    s3://hyperliquid-archive/market_data/<year>/<month>/<day>/<hour>/l2Book/<coin>.lz4

Each file contains newline-delimited JSON records.  Files are
lz4-frame-compressed; the optional dependency ``lz4`` must be
installed (``pip install lz4``).

No authentication is required — the bucket is world-readable via HTTPS.
"""

import json
import logging
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_S3_BASE = "https://hyperliquid-archive.s3.amazonaws.com/market_data"


class HyperliquidOrderbookProvider:
    """Download historical L2 snapshots from Hyperliquid's public S3 archive.

    Parameters
    ----------
    client:
        Optional pre-built ``httpx.Client``.  When *None* (default) the
        provider creates and owns its own client and closes it on
        :meth:`close`.
    """

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(timeout=120)
        self._owns_client = client is None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Always returns *True* — no authentication required."""
        return True

    def close(self) -> None:
        """Close the underlying HTTP client (only when owned by this instance)."""
        if self._owns_client:
            self._client.close()

    def fetch(
        self,
        market: str,
        date: str,
        hours: Optional[List[int]] = None,
    ) -> List[dict]:
        """Fetch L2 snapshots for *market* on *date*.

        Parameters
        ----------
        market:
            Flint market symbol, e.g. ``"SOL-PERP"``.
        date:
            Calendar date in ``YYYY-MM-DD`` format.
        hours:
            Optional list of hours (0–23) to fetch.  Defaults to all 24.

        Returns
        -------
        list of dict
            Each dict has keys: ``venue`` (``"hyperliquid"``), ``market``,
            ``ts`` (unix seconds, 5-min aligned), ``bid_prices``,
            ``bid_sizes``, ``ask_prices``, ``ask_sizes``.
        """
        coin = self._market_to_coin(market)
        parts = date.split("-")
        if len(parts) != 3:
            logger.debug("HyperliquidOrderbook: invalid date format %r", date)
            return []

        year, month, day = parts
        all_snapshots: List[dict] = []

        for hour in hours if hours is not None else range(24):
            url = self._build_url(year, month, day, f"{hour:02d}", coin)
            try:
                resp = self._client.get(url)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()

                try:
                    import lz4.frame

                    data = lz4.frame.decompress(resp.content)
                except ImportError:
                    logger.warning(
                        "HyperliquidOrderbook: lz4 package not installed; "
                        "run `pip install lz4` to enable this provider"
                    )
                    return []

                for line in data.decode("utf-8").strip().split("\n"):
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        ts_ms = rec.get("time", 0)
                        ts_s = self._align_ts(ts_ms // 1000)
                        levels = rec.get("levels", [[], []])
                        bids = levels[0][:20] if levels else []
                        asks = levels[1][:20] if len(levels) > 1 else []
                        all_snapshots.append(
                            {
                                "venue": "hyperliquid",
                                "market": market,
                                "ts": ts_s,
                                "bid_prices": [float(lvl.get("px", 0)) for lvl in bids],
                                "bid_sizes": [float(lvl.get("sz", 0)) for lvl in bids],
                                "ask_prices": [float(lvl.get("px", 0)) for lvl in asks],
                                "ask_sizes": [float(lvl.get("sz", 0)) for lvl in asks],
                            }
                        )
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                        continue

            except Exception:
                continue

        return all_snapshots

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _market_to_coin(market: str) -> str:
        """Extract the base coin from a Flint market symbol.

        Examples
        --------
        >>> HyperliquidOrderbookProvider._market_to_coin("SOL-PERP")
        'SOL'
        """
        return market.split("-")[0]

    def _build_url(self, year: str, month: str, day: str, hour: str, coin: str) -> str:
        """Build the S3 HTTPS URL for a single hourly lz4 file."""
        return f"{_S3_BASE}/{year}/{month}/{day}/{hour}/l2Book/{coin}.lz4"

    @staticmethod
    def _align_ts(ts: int) -> int:
        """Align a unix-second timestamp down to the nearest 5-minute boundary."""
        return ts - (ts % 300)
