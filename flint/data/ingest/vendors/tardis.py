"""Tardis BYO-license fetcher — stub (§9.1, D23).

Tardis is the one vendor that can seed HL's trade-tape + liquidation history from
before our own recorders existed (~2024-10-29, §9.1). Its ToS forbids
redistribution, so this runs on the **user's own key** and its output is only
ever written to the **user's local cache** (see ``VendorBackfiller``, D23).

This is a stub: the real Tardis client streams gzipped CSV replay files, which
land with the venue subscription. Here the network is behind an injectable
``VendorTransport`` seam (JSON fragments in tests), the key is resolved through
the ``SecretsPort`` (a per-tenant secret, never crossing the sandbox — §8.3), and
trades are normalized into the frozen ``TRADES_SCHEMA`` so vendor-seeded history
is byte-compatible with our own recordings. Wiring the production CSV/gzip codec
is a follow-up; the seam and the D23 local-only guarantee are what land now.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

import pyarrow as pa

from ...normalize import TRADES_SCHEMA
from ...ranges import Kind, TimeRange
from ....ports.secrets import SecretsPort
from ....ports.tenant import TenantContext

from .base import VendorKeyMissing

TARDIS_API_BASE = "https://api.tardis.dev/v1"
_TARDIS_KEY_NAME = "TARDIS_API_KEY"


class VendorTransport(Protocol):
    """The vendor network seam — a keyed JSON GET (tests inject recorded fragments)."""

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any: ...


def _side_of(raw: str) -> str:
    return "buy" if str(raw).lower().startswith("b") else "sell"


class TardisFetcher:
    """A ``VendorFetcher`` for Tardis trade history, keyed by the user's BYO key."""

    vendor = "tardis"

    def __init__(
        self,
        transport: VendorTransport,
        secrets: SecretsPort,
        tenant: TenantContext,
        *,
        base_url: str = TARDIS_API_BASE,
    ) -> None:
        self._transport = transport
        self._secrets = secrets
        self._tenant = tenant
        self._base = base_url.rstrip("/")

    def supports(self, venue: str, market: str, kind: Kind) -> bool:
        return kind is Kind.TRADES

    def _key(self) -> str:
        key = self._secrets.get_secret(self._tenant, _TARDIS_KEY_NAME)
        if not key:
            raise VendorKeyMissing(
                "Tardis is a BYO-license vendor (D23): set the TARDIS_API_KEY "
                "secret to fetch its history into your local cache."
            )
        return key

    def fetch(
        self, venue: str, market: str, kind: Kind, span: TimeRange
    ) -> pa.Table:
        if kind is not Kind.TRADES or span.is_empty:
            return TRADES_SCHEMA.empty_table()
        key = self._key()
        page = (
            self._transport.get_json(
                f"{self._base}/data-feeds/{venue}",
                params={
                    "symbols": market,
                    "from": span.start_ms,
                    "to": span.end_ms,
                    "type": "trade",
                },
                headers={"Authorization": f"Bearer {key}"},
            )
            or []
        )
        rows = []
        for rec in page:
            # Tardis timestamps are microseconds; normalize to ms.
            ts = int(rec["timestamp"]) // 1000
            if not (span.start_ms <= ts < span.end_ms):
                continue
            rows.append(
                {
                    "ts": ts,
                    "market": market,
                    "venue": venue,
                    "price": float(rec["price"]),
                    "size": float(rec["amount"]),
                    "side": _side_of(rec.get("side", "")),
                    "trade_id": int(rec.get("id", 0)),
                }
            )
        rows.sort(key=lambda r: r["ts"])
        return pa.Table.from_pylist(rows, schema=TRADES_SCHEMA)
