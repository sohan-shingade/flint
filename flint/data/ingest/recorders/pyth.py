"""Pyth Hermes oracle poller — the shared index/oracle feed (§9.1 class 5).

Polls Pyth's Hermes "latest price" endpoint for a configured set of price feeds
and normalizes each to an ``OraclePrice`` (value, confidence, publish time). It
is gated behind the ``SecretsPort`` key: **Pyth requires an API key from
2026-07-31**, so this ships now and stays inert until the key exists — with no
key, ``poll`` returns nothing rather than hitting an endpoint that will start
rejecting anonymous traffic. When the key is present it is sent as a bearer
header. No socket is opened in tests (the ``HttpTransport`` seam is faked).

**Persistence is deferred pending a Kind decision (team-lead).** Oracle/index
prices have no ``Kind`` today (mark/oracle are folded into ``Kind.OI`` rows per
the 2.4 ruling, and Pyth is a *cross-venue* index source, not a per-venue OI
row). So this worker fetches + normalizes + tracks lag; wiring the shared oracle
table lands with the Data API (2.7) or a dedicated ``Kind.MARK``/oracle addition.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import pyarrow as pa

from flint.ports import SecretsPort, TenantContext

from ...ranges import Kind
from ..transport import HttpTransport
from .monitor import LagMonitor

HERMES_BASE = "https://hermes.pyth.network"
PYTH_KEY_NAME = "PYTH_API_KEY"
VENUE_PYTH = "pyth"

ORACLE_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("market", pa.string()),
        ("venue", pa.string()),
        ("price", pa.float64()),
        ("confidence", pa.float64()),
        ("feed_id", pa.string()),
    ]
)


@dataclass(frozen=True)
class OraclePrice:
    """One Pyth price observation, decoded from its ``expo``-scaled integer form."""

    ts: int
    market: str
    venue: str
    price: float
    confidence: float
    feed_id: str


def _scaled(raw: str, expo: int) -> float:
    """Decode Pyth's integer-mantissa price: ``int(raw) * 10**expo``."""
    return int(raw) * (10.0 ** expo)


class PythOraclePoller:
    """Fetch + normalize Pyth Hermes latest prices, gated behind the API key."""

    def __init__(
        self,
        transport: HttpTransport,
        secrets: SecretsPort,
        tenant: TenantContext,
        *,
        feed_ids: Mapping[str, str],
        clock: Callable[[], int],
        base_url: str = HERMES_BASE,
        key_name: str = PYTH_KEY_NAME,
    ) -> None:
        self._transport = transport
        self._secrets = secrets
        self._tenant = tenant
        # market -> feed id, plus the reverse for decoding responses.
        self._feed_ids = dict(feed_ids)
        self._market_of = {fid: mkt for mkt, fid in feed_ids.items()}
        self._clock = clock
        self._base_url = base_url.rstrip("/")
        self._key_name = key_name
        self.lag = LagMonitor(clock)

    @property
    def enabled(self) -> bool:
        """True once the Pyth key exists (required from 2026-07-31)."""
        return self._secrets.get_secret(self._tenant, self._key_name) is not None

    def poll(self) -> list[OraclePrice]:
        """Fetch the latest price for every configured feed; inert without a key."""
        key = self._secrets.get_secret(self._tenant, self._key_name)
        if key is None:
            return []
        payload = self._transport.get_json(
            f"{self._base_url}/v2/updates/price/latest",
            params={"ids[]": list(self._feed_ids.values())},
            headers={"Authorization": f"Bearer {key}"},
        )
        prices: list[OraclePrice] = []
        for entry in (payload or {}).get("parsed", []):
            feed_id = str(entry.get("id", ""))
            market = self._market_of.get(feed_id)
            if market is None:
                continue  # a feed we didn't ask for — never guess a mapping
            p = entry["price"]
            expo = int(p["expo"])
            ts = int(p["publish_time"]) * 1000
            prices.append(
                OraclePrice(
                    ts=ts,
                    market=market,
                    venue=VENUE_PYTH,
                    price=_scaled(str(p["price"]), expo),
                    confidence=_scaled(str(p["conf"]), expo),
                    feed_id=feed_id,
                )
            )
            self.lag.record(VENUE_PYTH, market, Kind.OI, ts)
        return prices

    @staticmethod
    def to_arrow(prices: list[OraclePrice]) -> pa.Table:
        return pa.Table.from_pylist(
            [
                {
                    "ts": p.ts,
                    "market": p.market,
                    "venue": p.venue,
                    "price": p.price,
                    "confidence": p.confidence,
                    "feed_id": p.feed_id,
                }
                for p in prices
            ],
            schema=ORACLE_SCHEMA,
        )
