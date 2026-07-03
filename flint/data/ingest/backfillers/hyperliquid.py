"""Hyperliquid REST provider + S3 archive backfiller (§9.1, D28).

Two ingestion paths for the one v1 execution venue:

* ``HyperliquidRestProvider`` — the free-venue *fallback* the ``DataManager``
  source chain reaches when the local cache and the Flint Data API both miss
  (§9). It fetches **candles** and **funding** from HL's ``/info`` endpoint,
  transparently paging around HL's **5000-candle-per-request cap** (§9 / §9.1),
  and normalises each response to the frozen ``core.models`` Arrow shapes. It is
  a ``VenueProvider``, so ``FreeVenueProvider`` composes it into the chain.

* ``HyperliquidS3Backfiller`` — a REST/CSV *backfiller* worker (§9.1 class 2)
  that reads HL's free public S3 archive: ``l2Book`` depth (available from
  ~2023-09) and daily ``asset_ctxs`` (open interest + mark + oracle). It writes
  **idempotent upserts keyed ``(venue, market, ts)``** through an injectable sink
  (the local cache today, the lake later), after running the §9.0 quality bars.

**Mark/oracle coverage is coupled to OI coverage (v1, team-lead ruling).** An
``asset_ctxs`` record is one row bundling ``oi`` + ``mark_price`` + ``index_price``
(oracle) + ``funding_hourly``, persisted under ``Kind.OI`` — so mark and oracle
history exist exactly where OI history does, never independently. If engine wiring
ever needs a standalone mark series at a different cadence, the additive fix is a
dedicated ``Kind.MARK`` (its own rows/schema); nothing here needs rewriting.

Normalization of the ``l2Book`` and ``asset_ctxs`` shapes is delegated to
``data.normalize`` — the one parser shared with the WS recorders and the Phase-5
LiveFeed (§6.7), so an archive row and a live frame land byte-compatible in the
store. Both classes take injectable transports (``transport.py``); no method here
opens a socket on its own, so every test drives them with recorded fragments (D26).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Protocol

import pyarrow as pa

from ..quality import BackfillResult, check_prewrite
from ..transport import HttpTransport, ObjectStore
from ...normalize import (
    FUNDING_SCHEMA,
    books_to_arrow,
    contexts_to_arrow,
    normalize_asset_ctx_record,
    normalize_l2book,
)
from ...ranges import Kind, RangeSet, TimeRange

VENUE = "hyperliquid"
HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HL_ARCHIVE_BASE = "https://hyperliquid-archive.s3.amazonaws.com"

# HL caps a candleSnapshot response at 5000 candles; funding history at 500.
_MAX_CANDLES = 5000
_MAX_FUNDING = 500

# HL's own funding cadence is hourly, priced on the oracle, and the history
# endpoint returns already-settled rates (§2.2). These are data, not magic
# numbers the engine reads — the VenueSpec owns the cap.
_HL_FUNDING_INTERVAL_S = 3600
_HL_FUNDING_PRICE_BASIS = "oracle"
_HL_FUNDING_RATE_TYPE = "final"

# HL perp inception (mainnet) — the conservative coverage floor the fallback
# provider claims for candles/funding. The Flint Data API coverage matrix (2.7)
# is the authority on real per-market coverage; this floor keeps the funding
# hard gate honest (a request before HL existed is a gap, not a silent pass).
HL_INCEPTION_MS = 1_672_531_200_000  # 2023-01-01T00:00:00Z

_INTERVAL_BY_RESOLUTION_S: dict[int, str] = {
    60: "1m",
    180: "3m",
    300: "5m",
    900: "15m",
    1800: "30m",
    3600: "1h",
    7200: "2h",
    14400: "4h",
    28800: "8h",
    43200: "12h",
    86400: "1d",
}


def coin_of(market: str) -> str:
    """Map a Flint market symbol to HL's coin id: ``SOL-PERP`` -> ``SOL``."""
    return market[:-5] if market.endswith("-PERP") else market


def interval_of(resolution_s: int) -> str:
    """Map a bar width in seconds to HL's interval string (e.g. 3600 -> ``1h``)."""
    try:
        return _INTERVAL_BY_RESOLUTION_S[resolution_s]
    except KeyError:
        raise ValueError(f"unsupported HL candle resolution: {resolution_s}s") from None


# --- schemas (stable so concat/dedupe across pages is well-typed) -----------

_CANDLE_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        ("market", pa.string()),
        ("resolution_s", pa.int64()),
        ("venue", pa.string()),
    ]
)

# Funding schema is the canonical one in ``data.normalize`` (shared with the CEX
# funding ingestion, 2.6) so HL + CEX funding rows are store-compatible.
_FUNDING_SCHEMA = FUNDING_SCHEMA

# Depth + OI Arrow schemas are owned by ``data.normalize`` (shared with the WS
# recorders + LiveFeed) so archive rows and live frames are store-compatible.


class UpsertSink(Protocol):
    """The write side a backfiller targets — matches ``InMemoryCacheSource.store``.

    ``store`` merges + de-duplicates by ``ts``, which is exactly the idempotent
    upsert keyed ``(venue, market, ts)`` the §9.1 workers require: re-running a
    backfill over the same range leaves the store unchanged.
    """

    def store(self, venue: str, market: str, kind: Kind, table: pa.Table) -> None: ...


def _dedupe_by_ts(table: pa.Table) -> pa.Table:
    """Sort by ts and drop rows sharing a ts (paging overlaps at boundaries)."""
    import pyarrow.compute as pc

    if table.num_rows < 2 or "ts" not in table.column_names:
        return table.sort_by("ts") if table.num_rows and "ts" in table.column_names else table
    sorted_table = table.sort_by("ts")
    ts = sorted_table.column("ts").combine_chunks()
    keep = pc.not_equal(ts[1:], ts[:-1])
    keep = pa.concat_arrays([pa.array([True]), keep])
    return sorted_table.filter(keep)


class HyperliquidRestProvider:
    """HL free-venue provider for candles + funding — a ``VenueProvider`` (§9.1).

    Resolution is provider-configured (the 2.2 ``DataSource.fetch`` signature is
    kind-based and does not thread a bar width); one ``DataManager`` serves one
    backtest at one candle resolution, so this is set at construction. Threading
    resolution through the chain is a larger, contract-touching change routed via
    team-lead — not needed for the fallback path this slice ships.
    """

    venue = VENUE

    def __init__(
        self,
        transport: HttpTransport,
        *,
        resolution_s: int = 60,
        url: str = HL_INFO_URL,
        coverage_floor_ms: int = HL_INCEPTION_MS,
    ) -> None:
        self._transport = transport
        self._resolution_s = resolution_s
        self._interval = interval_of(resolution_s)
        self._url = url
        self._floor = coverage_floor_ms

    # --- VenueProvider surface --------------------------------------------

    def supports(self, market: str, kind: Kind) -> bool:
        return kind in (Kind.CANDLES, Kind.FUNDING)

    def coverage_floor(self, market: str, kind: Kind) -> int | None:
        return self._floor if self.supports(market, kind) else None

    def fetch_range(self, market: str, kind: Kind, span: TimeRange) -> pa.Table:
        if span.is_empty:
            return pa.table([], schema=self._schema(kind))
        if kind is Kind.CANDLES:
            return self._fetch_candles(market, span)
        if kind is Kind.FUNDING:
            return self._fetch_funding(market, span)
        return pa.table([], schema=self._schema(kind))

    @staticmethod
    def _schema(kind: Kind) -> pa.Schema:
        return _CANDLE_SCHEMA if kind is Kind.CANDLES else _FUNDING_SCHEMA

    # --- candles (5000-candle paging) -------------------------------------

    def _fetch_candles(self, market: str, span: TimeRange) -> pa.Table:
        coin = coin_of(market)
        width_ms = self._resolution_s * 1000
        cursor = span.start_ms
        rows: list[dict[str, Any]] = []
        while cursor < span.end_ms:
            body = {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": self._interval,
                    "startTime": cursor,
                    "endTime": span.end_ms,
                },
            }
            page = self._transport.post_json(self._url, body) or []
            if not page:
                break
            max_t = cursor
            for c in page:
                t = int(c["t"])
                max_t = max(max_t, t)
                if t < span.start_ms or t >= span.end_ms:
                    continue
                rows.append(
                    {
                        "ts": t,
                        "open": float(c["o"]),
                        "high": float(c["h"]),
                        "low": float(c["l"]),
                        "close": float(c["c"]),
                        "volume": float(c["v"]),
                        "market": market,
                        "resolution_s": self._resolution_s,
                        "venue": VENUE,
                    }
                )
            # A short page (fewer than the cap) means no more data to page.
            if len(page) < _MAX_CANDLES:
                break
            # Advance strictly past the last candle; guard against no progress.
            next_cursor = max_t + width_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        table = pa.Table.from_pylist(rows, schema=_CANDLE_SCHEMA)
        return _dedupe_by_ts(table)

    # --- funding (time-paged) ---------------------------------------------

    def _fetch_funding(self, market: str, span: TimeRange) -> pa.Table:
        coin = coin_of(market)
        cursor = span.start_ms
        rows: list[dict[str, Any]] = []
        while cursor < span.end_ms:
            body = {
                "type": "fundingHistory",
                "coin": coin,
                "startTime": cursor,
                "endTime": span.end_ms,
            }
            page = self._transport.post_json(self._url, body) or []
            if not page:
                break
            last_time = cursor
            for f in page:
                t = int(f["time"])
                if t < span.start_ms or t >= span.end_ms:
                    continue
                rows.append(
                    {
                        "ts": t,
                        "rate_hourly": float(f["fundingRate"]),
                        "interval_s": _HL_FUNDING_INTERVAL_S,
                        "price_basis": _HL_FUNDING_PRICE_BASIS,
                        "rate_type": _HL_FUNDING_RATE_TYPE,
                        "venue": VENUE,
                        "market": market,
                    }
                )
                last_time = max(last_time, t)
            next_cursor = last_time + 1
            if len(page) < _MAX_FUNDING or next_cursor <= cursor:
                break
            cursor = next_cursor
        table = pa.Table.from_pylist(rows, schema=_FUNDING_SCHEMA)
        return _dedupe_by_ts(table)


class HyperliquidS3Backfiller:
    """Backfill HL depth + OI from the free public S3 archive (§9.1 class 2).

    ``l2Book`` snapshots (~2023-09 on) and daily ``asset_ctxs`` (OI/mark/oracle)
    are fetched via an injectable ``ObjectStore``, decoded (the archive is
    newline-delimited JSON, LZ4-compressed in production — ``codec`` selects the
    decompressor; tests pass raw bytes), normalised, checked by the §9.0 pre-write
    bars, and upserted idempotently keyed ``(venue, market, ts)``.
    """

    def __init__(
        self,
        object_store: ObjectStore,
        sink: UpsertSink,
        *,
        codec: str = "raw",
        base_hint: str = HL_ARCHIVE_BASE,
    ) -> None:
        self._store = object_store
        self._sink = sink
        self._codec = codec
        self._base_hint = base_hint

    # --- key layout (HL archive convention; verify at ops time) -----------

    @staticmethod
    def l2book_key(market: str, date: str, hour: int) -> str:
        """Archive key for an hour of ``l2Book`` depth: ``.../{H}/l2Book/{coin}``."""
        return f"market_data/{date}/{hour}/l2Book/{coin_of(market)}.lz4"

    @staticmethod
    def asset_ctxs_key(date: str) -> str:
        """Archive key for a day of ``asset_ctxs`` (all coins in one file)."""
        return f"market_data/{date}/asset_ctxs/asset_ctxs.lz4"

    def _decode_lines(self, raw: bytes | None) -> list[dict[str, Any]]:
        """Decompress (per ``codec``) and parse newline-delimited JSON records."""
        if not raw:
            return []
        if self._codec == "lz4":
            import lz4.frame  # optional dep; only production passes codec="lz4"

            raw = lz4.frame.decompress(raw)
        text = raw.decode("utf-8")
        out: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    # --- l2Book depth ------------------------------------------------------

    def backfill_l2book(
        self, market: str, date: str, hours: Sequence[int]
    ) -> BackfillResult:
        """Backfill ``l2Book`` depth for ``market`` on ``date`` over ``hours``.

        A missing hour object is a coverage gap (recorded in ``gaps``), not an
        error — the archive genuinely lacks some early hours.
        """
        books = []
        missing_hours: list[TimeRange] = []
        for hour in hours:
            recs = self._decode_lines(self._store.get(self.l2book_key(market, date, hour)))
            if not recs:
                base = _hour_ms(date, hour)
                missing_hours.append(TimeRange(base, base + 3_600_000))
                continue
            for r in recs:
                books.append(normalize_l2book(r, market))
        table = _dedupe_by_ts(books_to_arrow(books))
        quality = check_prewrite(table, price_col=None, volume_col=None)
        written = 0
        if quality.ok and table.num_rows:
            self._sink.store(VENUE, market, Kind.DEPTH, table)
            written = table.num_rows
        return BackfillResult(
            kind=str(Kind.DEPTH),
            rows_written=written,
            quality=quality,
            gaps=RangeSet(missing_hours),
        )

    # --- asset_ctxs (OI / mark / oracle) ----------------------------------

    def backfill_asset_ctxs(
        self, markets: Sequence[str], date: str
    ) -> dict[str, BackfillResult]:
        """Backfill daily OI/mark/oracle for ``markets`` from one ``asset_ctxs`` file.

        The daily file bundles every coin, so it is fetched once and split per
        market; each market's rows are upserted under ``Kind.OI`` (the daily
        context carries ``oi`` plus the mark/oracle prices in the same row).
        """
        recs = self._decode_lines(self._store.get(self.asset_ctxs_key(date)))
        by_market: dict[str, list] = {m: [] for m in markets}
        wanted = {coin_of(m): m for m in markets}
        for r in recs:
            market = wanted.get(str(r.get("coin", "")))
            if market is not None:
                by_market[market].append(normalize_asset_ctx_record(r, market))
        results: dict[str, BackfillResult] = {}
        for market, contexts in by_market.items():
            table = _dedupe_by_ts(contexts_to_arrow(contexts))
            quality = check_prewrite(table, price_col="mark_price", volume_col=None)
            written = 0
            if quality.ok and table.num_rows:
                self._sink.store(VENUE, market, Kind.OI, table)
                written = table.num_rows
            results[market] = BackfillResult(
                kind=str(Kind.OI), rows_written=written, quality=quality
            )
        return results


def _hour_ms(date: str, hour: int) -> int:
    """Unix-ms start of ``date`` (``YYYYMMDD``) at ``hour`` UTC."""
    from datetime import UTC, datetime

    y, m, d = int(date[:4]), int(date[4:6]), int(date[6:8])
    return int(datetime(y, m, d, hour, tzinfo=UTC).timestamp() * 1000)
