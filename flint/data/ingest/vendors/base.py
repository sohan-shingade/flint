"""BYO-license vendor lane — interface + local-only backfiller (§9.1, D23).

Paid vendors (Tardis, Crypto Lake, Kaiko, Amberdata) can fill history that
predates our own recorders — Tardis's HL trade-tape + liquidation history from
~2024-10-29, for instance (§9.1). But their ToS **prohibits redistribution**
(Tardis verified, round 2), so vendor bytes must **never enter the shared lake**.

The D23 legal model is enforced structurally here: a ``VendorFetcher`` is driven
by the *user's own* API key (resolved through the ``SecretsPort``, so it is a
per-tenant secret that never crosses the sandbox boundary), and ``VendorBackfiller``
writes only into a **local** cache sink the caller supplies. There is no code path
from this module to a lake-write — the sink is the user's machine, full stop.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pyarrow as pa

from ..quality import BackfillResult, check_prewrite
from ...ranges import Kind, TimeRange


class VendorKeyMissing(Exception):
    """The user has not supplied this vendor's BYO API key (D23)."""


# Row-identity tie-breakers per kind, matching the store's per-kind dedupe keys
# (store/durable_cache.py) — tick streams carry many rows per millisecond, so
# ts alone is not their identity (§B2).
_IDENTITY_COLS: dict[Kind, tuple[str, ...]] = {
    Kind.TRADES: ("trade_id",),
    Kind.FUNDING: ("rate_type",),
    Kind.BOOK_DELTA: ("seq",),
}


@runtime_checkable
class VendorFetcher(Protocol):
    """One paid vendor's read surface, driven by the user's own key (D23).

    A fetcher declares which ``(venue, market, kind)`` it can serve and returns
    normalized rows in the frozen ``core.models`` Arrow shapes, so vendor history
    lands byte-compatible with our own recordings in the *local* cache.
    """

    vendor: str

    def supports(self, venue: str, market: str, kind: Kind) -> bool: ...

    def fetch(
        self, venue: str, market: str, kind: Kind, span: TimeRange
    ) -> pa.Table: ...


class LocalCacheSink(Protocol):
    """The user's **local** cache write surface — the only sink the vendor lane targets.

    Matches ``InMemoryCacheSource.store`` / ``DurableCacheSource.store``. The type
    exists to name the D23 invariant at the call site: vendor bytes go to the
    user's machine, never to a shared-lake writer.
    """

    def store(self, venue: str, market: str, kind: Kind, table: pa.Table) -> None: ...


class VendorBackfiller:
    """Fetch vendor history and upsert it into the user's LOCAL cache only (D23).

    Runs the §9.0 pre-write quality bars before writing, exactly like the free
    backfillers, so vendor data is held to the same ordering/duplicate checks.
    The ``sink`` must be a local cache — there is deliberately no lake-write path.
    """

    def __init__(self, fetcher: VendorFetcher, local_cache: LocalCacheSink) -> None:
        self._fetcher = fetcher
        self._cache = local_cache

    def backfill(
        self, venue: str, market: str, kind: Kind, span: TimeRange
    ) -> BackfillResult:
        if not self._fetcher.supports(venue, market, kind):
            return BackfillResult(kind=str(kind), rows_written=0, quality=check_prewrite(pa.table({})))
        table = self._fetcher.fetch(venue, market, kind, span)
        price_col = "price" if kind is Kind.TRADES else None
        quality = check_prewrite(
            table,
            key_cols=_IDENTITY_COLS.get(kind, ()),
            price_col=price_col,
            volume_col=None,
        )
        written = 0
        if quality.ok and table.num_rows:
            self._cache.store(venue, market, kind, table)
            written = table.num_rows
        return BackfillResult(kind=str(kind), rows_written=written, quality=quality)
