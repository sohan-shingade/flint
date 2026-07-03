"""Tardis vendor adapter — the CSV datasets API, BYO-license (§9.2, D23).

Tardis is the vendor that can seed HL tick history from before our own
recorders existed (exchange floor 2024-10-29, per-symbol floors from the
exchanges endpoint). This adapter speaks the **CSV datasets API** (one
pre-normalized gzipped CSV per UTC day per symbol — day granularity matches
the store partitions and the :class:`~flint.data.store.coverage.CoverageLedger`),
*not* the replay API. First-of-month day files are free without a key
(verified live), which is where the D26 real-fragment fixtures under
``tests/fixtures/tardis/`` come from.

The D23 legal model is structural: the key is the *user's own*
``TARDIS_API_KEY`` resolved through the ``SecretsPort``, and vendor bytes only
ever land in the user's **local** cache (the fetcher's ``sink`` — there is no
lake-write path here).

Layers, outermost first:

* :class:`TardisVendorSource` — a chain ``DataSource``; ``available()`` is the
  entitlement gate (no key → no advertised coverage), ``fetch()`` delegates.
* :class:`TardisFetcher` — the D23 ``VendorFetcher`` shape; iterates UTC days,
  skips ledger-covered days (incremental resume by construction), persists
  whole landed days through its ``sink`` and *then* asserts them covered
  (source ``"tardis"``) — zero-row days included: a quiet market is data.
* :class:`TardisCsvClient` — pure: URL building, gunzip, CSV→Arrow against the
  frozen ``normalize`` schemas, µs→ms.
* :class:`TardisBytesTransport` / :class:`HttpxBytesTransport` — the network
  seam; tests inject recorded gzip bytes, production retries 429/5xx with
  exponential backoff and maps 404 → ``None`` (no file that day).

Timestamp semantics (§6.4, §9.2): ``derivative_ticker`` rows are stamped with
``local_timestamp`` (capture time — the "known at" moment); ``trades`` /
``quotes`` / book rows keep the exchange ``timestamp``.

``trade_id`` decision (D2): HL trade ids are numeric and comfortably int64
(real 2026-06-01 sample: max ≈ 1.13e15 « 2**63), so ``id`` is parsed straight
to int64. A non-numeric or overflowing id fails the CSV parse *loudly*
(``ArrowInvalid``) instead of being silently hashed — revisit with a string
column if a future venue ships non-numeric ids.
"""

from __future__ import annotations

import gzip
import io
import json
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa

from ...normalize import (
    BOOK_DELTA_SCHEMA,
    DEPTH_SCHEMA,
    FUNDING_SCHEMA,
    OI_SCHEMA,
    QUOTES_SCHEMA,
    TRADES_SCHEMA,
)
from ...ranges import Kind, RangeSet, TimeRange
from ...sources import DataSource
from ...store.coverage import FUNDING_PREDICTED_VARIANT, CoverageLedger
from ....ports.secrets import SecretsPort
from ....ports.tenant import TenantContext
from ..backfillers.hyperliquid import coin_of
from .base import LocalCacheSink, VendorKeyMissing

TARDIS_API_BASE = "https://api.tardis.dev/v1"
TARDIS_DATASETS_BASE = "https://datasets.tardis.dev/v1"
_TARDIS_KEY_NAME = "TARDIS_API_KEY"

_DAY_MS = 86_400_000
_META_TTL_MS = 24 * 3_600_000  # exchanges-endpoint response cached 24h on disk

#: Tardis dataset name per Flint kind. ``derivative_ticker`` backs *two* kinds:
#: it splits into FUNDING (predicted rows, on rate change) + OI (every row).
#:
#: LIQUIDATIONS slot (D5 investigation, 2026-07-03): a future ``Kind.
#: LIQUIDATIONS`` would map to the Tardis ``liquidations`` dataset here — but
#: Hyperliquid does not ship it. Verified empirically: the live exchanges
#: endpoint lists no ``liquidations`` dataType for any HL symbol, and the
#: free first-of-month download ``GET datasets.../hyperliquid/liquidations/
#: 2026/06/01/SOL.csv.gz`` returns HTTP 400 (code 300, "Data type
#: 'liquidations' is not supported for 'SOL'") while the identical URL grammar
#: returns 200 for HL ``trades`` and 200 for ``binance-futures``
#: ``liquidations`` — an HL-specific absence, not a URL error. Do not add the
#: Kind until Tardis populates the dataset (HL liq flow would otherwise need
#: reconstruction from trades, which is inference, not capture — D26).
DATASET_BY_KIND: dict[Kind, str] = {
    Kind.TRADES: "trades",
    Kind.QUOTES: "quotes",
    Kind.DEPTH: "book_snapshot_25",
    Kind.BOOK_DELTA: "incremental_book_L2",
    Kind.FUNDING: "derivative_ticker",
    Kind.OI: "derivative_ticker",
}

_SCHEMA_BY_KIND: dict[Kind, pa.Schema] = {
    Kind.TRADES: TRADES_SCHEMA,
    Kind.QUOTES: QUOTES_SCHEMA,
    Kind.DEPTH: DEPTH_SCHEMA,
    Kind.BOOK_DELTA: BOOK_DELTA_SCHEMA,
    Kind.FUNDING: FUNDING_SCHEMA,
    Kind.OI: OI_SCHEMA,
}


class TardisTransportError(Exception):
    """A non-recoverable transport failure (bad status or retries exhausted)."""


class TardisBytesTransport(Protocol):
    """The network seam — a keyed bytes GET; tests inject recorded gzip bytes.

    Returns the response body, or ``None`` on 404 (Tardis has no file for that
    day — e.g. before a symbol's ``availableSince``). Anything else that cannot
    be recovered raises :class:`TardisTransportError`.
    """

    def get_bytes(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> bytes | None: ...


class HttpxBytesTransport:
    """Production :class:`TardisBytesTransport` on httpx, with backoff.

    429 and 5xx responses are retried with exponential backoff
    (``backoff_base_s * 2**attempt``); 404 maps to ``None``; any other non-200
    raises. ``httpx`` is imported lazily so the mocked test suite never needs
    it; ``sleep`` is injectable so backoff is testable without waiting.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        max_attempts: int = 5,
        backoff_base_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._max_attempts = max(1, max_attempts)
        self._backoff_base_s = backoff_base_s
        self._sleep = sleep

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=60.0, follow_redirects=True)
        return self._client

    def get_bytes(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> bytes | None:
        last_status = None
        for attempt in range(self._max_attempts):
            resp = self._http().get(url, headers=dict(headers or {}))
            status = resp.status_code
            if status == 200:
                return resp.content
            if status == 404:
                return None  # no file that day — a fact, not a fault
            if status == 429 or status >= 500:
                last_status = status
                if attempt + 1 < self._max_attempts:
                    self._sleep(self._backoff_base_s * (2**attempt))
                    continue
                break
            raise TardisTransportError(f"GET {url} -> HTTP {status}")
        raise TardisTransportError(
            f"GET {url} -> HTTP {last_status} after {self._max_attempts} attempts"
        )


# --- pure CSV client ---------------------------------------------------------


class TardisCsvClient:
    """Pure Tardis CSV codec: URL grammar + gunzip + CSV→Arrow (µs→ms).

    All Tardis-specific conversion lives here — ``normalize.py`` stays the
    venue-message parser and is not touched (its frozen schemas are the output
    contract).
    """

    @staticmethod
    def dataset_url(
        exchange: str,
        data_type: str,
        day: date,
        symbol: str,
        *,
        base: str = TARDIS_DATASETS_BASE,
    ) -> str:
        """``{base}/{exchange}/{dataType}/{yyyy}/{mm}/{dd}/{symbol}.csv.gz``."""
        return (
            f"{base.rstrip('/')}/{exchange}/{data_type}/"
            f"{day.year:04d}/{day.month:02d}/{day.day:02d}/{symbol}.csv.gz"
        )

    @classmethod
    def parse(
        cls, data_type: str, gz: bytes, *, market: str, venue: str
    ) -> dict[Kind, pa.Table]:
        """Decode one day file into kind-keyed tables in the frozen schemas.

        ``derivative_ticker`` yields both FUNDING and OI; every other dataset
        yields its single kind. A header-only (or empty) file yields empty
        tables — the *caller* decides what that means for coverage (a quiet
        market is data, §9.2).
        """
        raw = gzip.decompress(gz) if gz[:2] == b"\x1f\x8b" else gz
        if data_type == "trades":
            return {Kind.TRADES: cls._parse_trades(raw, market, venue)}
        if data_type == "quotes":
            return {Kind.QUOTES: cls._parse_quotes(raw, market, venue)}
        if data_type == "derivative_ticker":
            funding, oi = cls._parse_derivative_ticker(raw, market, venue)
            return {Kind.FUNDING: funding, Kind.OI: oi}
        if data_type == "incremental_book_L2":
            return {Kind.BOOK_DELTA: cls._parse_book_l2(raw, market, venue)}
        if data_type == "book_snapshot_25":
            return {Kind.DEPTH: cls._parse_book_snapshot(raw, market, venue)}
        raise ValueError(f"unknown Tardis dataset type: {data_type!r}")

    # -- per-dataset decoders (columns verified against real 2026-06-01 files) --

    @staticmethod
    def _read_csv(raw: bytes, column_types: dict[str, pa.DataType]) -> pa.Table | None:
        """CSV bytes → Arrow, or None if the file has no parsable content."""
        import pyarrow.csv as pacsv

        if not raw.strip():
            return None
        try:
            return pacsv.read_csv(
                io.BytesIO(raw),
                convert_options=pacsv.ConvertOptions(column_types=column_types),
            )
        except pa.ArrowInvalid as exc:
            raise ValueError(f"unparsable Tardis CSV: {exc}") from exc

    @staticmethod
    def _us_to_ms(col: pa.ChunkedArray) -> pa.ChunkedArray:
        """Microseconds → milliseconds (integer division; nulls propagate)."""
        import pyarrow.compute as pc

        return pc.divide(col, 1000)

    @staticmethod
    def _const(value: str, n: int) -> pa.Array:
        return pa.array([value] * n, pa.string())

    @classmethod
    def _parse_trades(cls, raw: bytes, market: str, venue: str) -> pa.Table:
        # exchange,symbol,timestamp,local_timestamp,id,side,price,amount
        tab = cls._read_csv(
            raw,
            {
                "timestamp": pa.int64(),
                "local_timestamp": pa.int64(),
                # trade_id decision (module docstring): numeric int64, strict.
                "id": pa.int64(),
                "side": pa.string(),
                "price": pa.float64(),
                "amount": pa.float64(),
            },
        )
        if tab is None or tab.num_rows == 0:
            return TRADES_SCHEMA.empty_table()
        n = tab.num_rows
        return pa.Table.from_arrays(
            [
                cls._us_to_ms(tab["timestamp"]),
                cls._const(market, n),
                cls._const(venue, n),
                tab["price"],
                tab["amount"],
                tab["side"],
                tab["id"],
            ],
            schema=TRADES_SCHEMA,
        )

    @classmethod
    def _parse_quotes(cls, raw: bytes, market: str, venue: str) -> pa.Table:
        # exchange,symbol,timestamp,local_timestamp,ask_amount,ask_price,bid_price,bid_amount
        tab = cls._read_csv(
            raw,
            {
                "timestamp": pa.int64(),
                "local_timestamp": pa.int64(),
                "ask_amount": pa.float64(),
                "ask_price": pa.float64(),
                "bid_price": pa.float64(),
                "bid_amount": pa.float64(),
            },
        )
        if tab is None or tab.num_rows == 0:
            return QUOTES_SCHEMA.empty_table()
        n = tab.num_rows
        return pa.Table.from_arrays(
            [
                cls._us_to_ms(tab["timestamp"]),
                cls._const(market, n),
                cls._const(venue, n),
                tab["bid_price"],
                tab["bid_amount"],
                tab["ask_price"],
                tab["ask_amount"],
            ],
            schema=QUOTES_SCHEMA,
        )

    @classmethod
    def _parse_derivative_ticker(
        cls, raw: bytes, market: str, venue: str
    ) -> tuple[pa.Table, pa.Table]:
        """Split ``derivative_ticker`` into FUNDING (rate changes) + OI (all rows).

        FUNDING semantics (§6.4, §9.2): a row is emitted where the funding rate
        *changes*; ``ts`` is ``local_timestamp`` — the capture / "known at"
        moment; the rate is HL's native hourly rate (``interval_s=3600``,
        ``rate_hourly`` = the raw rate), priced off the oracle
        (``price_basis="oracle"``) and not yet settled (``rate_type="predicted"``).
        ``settlement_ts`` comes from ``funding_timestamp`` when Tardis supplies
        it and is null otherwise (HL files ship it empty — never fabricated,
        D26). OI rows carry every ticker row's open_interest/mark/index fold,
        matching the recorder's ``activeAssetCtx`` shape.
        """
        # exchange,symbol,timestamp,local_timestamp,funding_timestamp,funding_rate,
        # predicted_funding_rate,open_interest,last_price,index_price,mark_price
        tab = cls._read_csv(
            raw,
            {
                "timestamp": pa.int64(),
                "local_timestamp": pa.int64(),
                "funding_timestamp": pa.int64(),
                "funding_rate": pa.float64(),
                "predicted_funding_rate": pa.float64(),
                "open_interest": pa.float64(),
                "index_price": pa.float64(),
                "mark_price": pa.float64(),
            },
        )
        if tab is None or tab.num_rows == 0:
            return FUNDING_SCHEMA.empty_table(), OI_SCHEMA.empty_table()

        local_ts = tab["local_timestamp"].to_pylist()
        settle_ts = tab["funding_timestamp"].to_pylist()
        rates = tab["funding_rate"].to_pylist()
        ois = tab["open_interest"].to_pylist()
        marks = tab["mark_price"].to_pylist()
        indexes = tab["index_price"].to_pylist()

        funding_rows: list[dict[str, Any]] = []
        oi_rows: list[dict[str, Any]] = []
        prev_rate: float | None = None
        for i in range(tab.num_rows):
            ts_ms = local_ts[i] // 1000  # capture time — the "known at" moment
            rate = rates[i]
            if rate is not None and rate != prev_rate:
                funding_rows.append(
                    {
                        "ts": ts_ms,
                        "rate_hourly": rate,  # HL's native rate is hourly
                        "interval_s": 3600,
                        "price_basis": "oracle",
                        "rate_type": "predicted",
                        "venue": venue,
                        "market": market,
                        "settlement_ts": (
                            settle_ts[i] // 1000 if settle_ts[i] is not None else None
                        ),
                    }
                )
                prev_rate = rate
            if ois[i] is not None:
                oi_rows.append(
                    {
                        "ts": ts_ms,
                        "market": market,
                        "venue": venue,
                        "oi": ois[i],
                        "mark_price": marks[i],
                        "index_price": indexes[i],
                        "funding_hourly": rate,
                    }
                )
        return (
            pa.Table.from_pylist(funding_rows, schema=FUNDING_SCHEMA),
            pa.Table.from_pylist(oi_rows, schema=OI_SCHEMA),
        )

    @classmethod
    def _parse_book_l2(cls, raw: bytes, market: str, venue: str) -> pa.Table:
        # exchange,symbol,timestamp,local_timestamp,is_snapshot,side,price,amount
        tab = cls._read_csv(
            raw,
            {
                "timestamp": pa.int64(),
                "local_timestamp": pa.int64(),
                "is_snapshot": pa.bool_(),
                "side": pa.string(),
                "price": pa.float64(),
                "amount": pa.float64(),
            },
        )
        if tab is None or tab.num_rows == 0:
            return BOOK_DELTA_SCHEMA.empty_table()
        n = tab.num_rows
        # ``seq`` is the row's position in the day file — Tardis emits rows in
        # canonical event order, so the position *is* the tie-break within a ts.
        # Ordering metadata about the capture, not a fabricated market value (D26).
        seq = pa.chunked_array([pa.array(range(n), pa.int64())])
        return pa.Table.from_arrays(
            [
                cls._us_to_ms(tab["timestamp"]),
                cls._us_to_ms(tab["local_timestamp"]),
                seq,
                cls._const(market, n),
                cls._const(venue, n),
                tab["side"],
                tab["price"],
                tab["amount"],
                tab["is_snapshot"],
            ],
            schema=BOOK_DELTA_SCHEMA,
        )

    @classmethod
    def _parse_book_snapshot(cls, raw: bytes, market: str, venue: str) -> pa.Table:
        # exchange,symbol,timestamp,local_timestamp,asks[0].price,asks[0].amount,
        # bids[0].price,bids[0].amount, ... 25 levels, best-first.
        tab = cls._read_csv(
            raw, {"timestamp": pa.int64(), "local_timestamp": pa.int64()}
        )
        if tab is None or tab.num_rows == 0:
            return DEPTH_SCHEMA.empty_table()
        names = set(tab.column_names)
        levels = 0
        while f"asks[{levels}].price" in names:
            levels += 1
        ts_ms = cls._us_to_ms(tab["timestamp"]).to_pylist()
        cols = {
            name: tab[name].to_pylist()
            for name in tab.column_names
            if name.startswith(("asks[", "bids["))
        }

        def side_levels(row: int, side: str) -> list[list[float]]:
            out: list[list[float]] = []
            for lvl in range(levels):
                px = cols[f"{side}[{lvl}].price"][row]
                sz = cols[f"{side}[{lvl}].amount"][row]
                if px is None or sz is None:
                    break  # trailing empty levels
                out.append([px, sz])
            return out

        rows = [
            {
                "ts": ts_ms[i],
                "market": market,
                "venue": venue,
                "bids": side_levels(i, "bids"),
                "asks": side_levels(i, "asks"),
            }
            for i in range(tab.num_rows)
        ]
        return pa.Table.from_pylist(rows, schema=DEPTH_SCHEMA)


# --- fetcher (VendorFetcher shape, D23) --------------------------------------


class TardisFetcher:
    """Ledger-driven day-by-day Tardis fetcher, on the user's own key (D23).

    ``fetch`` iterates the UTC days intersecting the span, **skipping days the
    CoverageLedger already covers** (re-running a backfill resumes where it
    stopped), and for each landed day persists the *whole* day through ``sink``
    and then asserts the day covered (``source="tardis"``) — zero-row days
    included. Assertion strictly follows persistence, so the ledger never
    claims rows a crash discarded; without a ``sink`` no coverage is asserted
    (``ledger_root`` requires ``sink``).

    First-of-month day files are free: they are fetched without a key; every
    other day raises :class:`VendorKeyMissing` when the tenant has no
    ``TARDIS_API_KEY``. ``on_day`` is the day-level progress hook a backfill
    job can observe.
    """

    vendor = "tardis"
    supported_kinds = frozenset(DATASET_BY_KIND)

    def __init__(
        self,
        transport: TardisBytesTransport,
        secrets: SecretsPort,
        tenant: TenantContext,
        *,
        exchange: str = "hyperliquid",
        datasets_base: str = TARDIS_DATASETS_BASE,
        api_base: str = TARDIS_API_BASE,
        meta_cache_dir: str | Path | None = None,
        sink: LocalCacheSink | None = None,
        ledger_root: str | Path | None = None,
        now_ms: Callable[[], int] | None = None,
        on_day: Callable[[str, Kind, int], None] | None = None,
    ) -> None:
        if ledger_root is not None and sink is None:
            raise ValueError(
                "ledger_root requires a sink: coverage may only be asserted "
                "for rows that were actually persisted (§9.2)"
            )
        self._transport = transport
        self._secrets = secrets
        self._tenant = tenant
        self._exchange = exchange
        self._datasets_base = datasets_base.rstrip("/")
        self._api_base = api_base.rstrip("/")
        self._meta_cache_dir = Path(meta_cache_dir) if meta_cache_dir else None
        self._sink = sink
        self._ledger_root = Path(ledger_root) if ledger_root else None
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._on_day = on_day
        self._meta_memo: tuple[int, dict[str, Any]] | None = None

    @property
    def exchange(self) -> str:
        return self._exchange

    # -- key handling (D23) ---------------------------------------------------

    def has_key(self) -> bool:
        return bool(self._secrets.get_secret(self._tenant, _TARDIS_KEY_NAME))

    def _headers_for(self, day: date) -> dict[str, str]:
        key = self._secrets.get_secret(self._tenant, _TARDIS_KEY_NAME)
        if key:
            return {"Authorization": f"Bearer {key}"}
        if day.day == 1:
            return {}  # first-of-month datasets are free without a key
        raise VendorKeyMissing(
            "Tardis is a BYO-license vendor (D23): set the TARDIS_API_KEY "
            "secret to fetch its history into your local cache "
            "(first-of-month day files are free without one)."
        )

    # -- exchange metadata: per-symbol availability, 24h disk cache -----------

    def _metadata(self) -> dict[str, Any] | None:
        now = self._now_ms()
        if self._meta_memo is not None and now - self._meta_memo[0] < _META_TTL_MS:
            return self._meta_memo[1]
        path = (
            self._meta_cache_dir / f"exchange_{self._exchange}.json"
            if self._meta_cache_dir
            else None
        )
        stale: dict[str, Any] | None = None
        if path is not None and path.exists():
            try:
                doc = json.loads(path.read_text())
                stale = doc.get("payload")
                if now - int(doc.get("fetched_ms", 0)) < _META_TTL_MS and stale:
                    self._meta_memo = (int(doc["fetched_ms"]), stale)
                    return stale
            except (ValueError, KeyError, OSError):
                stale = None
        try:
            raw = self._transport.get_bytes(
                f"{self._api_base}/exchanges/{self._exchange}"
            )
        except TardisTransportError:
            raw = None
        if raw is None:
            # Offline / vendor down: a stale cache beats no answer; else the
            # fetcher simply declares nothing (fail closed, never fabricate).
            if stale is not None:
                self._meta_memo = (now, stale)
            return stale
        payload = json.loads(raw)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"fetched_ms": now, "payload": payload}))
        self._meta_memo = (now, payload)
        return payload

    def availability(self, market: str, kind: Kind) -> TimeRange | None:
        """The per-symbol ``[availableSince, availableTo)`` window, or None.

        Per-symbol floors, never the exchange-level date — a listing newer than
        the exchange floor must not advertise history it does not have (§9.2).
        """
        if kind not in self.supported_kinds:
            return None
        meta = self._metadata()
        if meta is None:
            return None
        coin = coin_of(market)
        for sym in (meta.get("datasets") or {}).get("symbols", ()):
            if sym.get("id") != coin or sym.get("type") != "perpetual":
                continue
            if DATASET_BY_KIND[kind] not in (sym.get("dataTypes") or ()):
                return None
            since = _iso_ms(sym.get("availableSince"))
            until = _iso_ms(sym.get("availableTo"))
            if since is None:
                return None
            end = until if until is not None else self._now_ms()
            return TimeRange(since, end) if end > since else None
        return None

    # -- VendorFetcher surface -------------------------------------------------

    def supports(self, venue: str, market: str, kind: Kind) -> bool:
        return (
            venue == self._exchange
            and kind in self.supported_kinds
            and self.availability(market, kind) is not None
        )

    def fetch(
        self, venue: str, market: str, kind: Kind, span: TimeRange
    ) -> pa.Table:
        """Rows in ``span`` for ``kind``, landing whole vendor days on the way.

        Only the requested kind is persisted and asserted — a FUNDING fetch
        parses ``derivative_ticker``'s OI half too but discards it, keeping
        every kind's coverage single-owner (the sibling day file is ~small and
        re-downloaded if OI is requested; simplicity over one saved GET).
        """
        if venue != self._exchange or kind not in self.supported_kinds or span.is_empty:
            return _SCHEMA_BY_KIND.get(kind, TRADES_SCHEMA).empty_table()
        dataset = DATASET_BY_KIND[kind]
        symbol = coin_of(market)
        ledger = (
            CoverageLedger(self._ledger_dir(venue, market, kind))
            if self._ledger_root is not None
            else None
        )
        covered = ledger.covered() if ledger is not None else RangeSet()

        pieces: list[pa.Table] = []
        for day_start in _utc_days(span):
            day_span = TimeRange(day_start, day_start + _DAY_MS)
            if covered.covers(day_span):
                continue  # incremental resume: this day already landed
            day = datetime.fromtimestamp(day_start / 1000, tz=UTC).date()
            url = TardisCsvClient.dataset_url(
                self._exchange, dataset, day, symbol, base=self._datasets_base
            )
            raw = self._transport.get_bytes(url, headers=self._headers_for(day))
            if raw is None:
                # 404: the vendor has no file for this day (outside its window,
                # or not yet exported). Not covered, not an error — skip.
                continue
            parsed = TardisCsvClient.parse(dataset, raw, market=market, venue=venue)[
                kind
            ]
            if self._sink is not None:
                if parsed.num_rows:
                    # Whole day persisted (even the part outside ``span``) so
                    # the whole-day coverage assertion below is backed by rows.
                    self._sink.store(venue, market, kind, parsed)
                if ledger is not None:
                    # After persistence, never before — and zero-row days too:
                    # a landed day file with no rows is a quiet market (§9.2).
                    # derivative_ticker FUNDING rows are the *predicted* series
                    # (not yet settled), so they assert under the predicted
                    # variant and never satisfy the final-series hard gate.
                    variant = (
                        FUNDING_PREDICTED_VARIANT if kind is Kind.FUNDING else ""
                    )
                    ledger.assert_covered(day_span, "tardis", variant=variant)
            if self._on_day is not None:
                self._on_day(day.isoformat(), kind, parsed.num_rows)
            pieces.append(_slice_half_open(parsed, span))

        pieces = [p for p in pieces if p.num_rows]
        if not pieces:
            return _SCHEMA_BY_KIND[kind].empty_table()
        out = pa.concat_tables(pieces)
        return out.sort_by("ts") if not _ts_sorted(out) else out

    def _ledger_dir(self, venue: str, market: str, kind: Kind) -> Path:
        """``root/kind/venue/market`` — the DurableCacheSource dir grammar."""
        assert self._ledger_root is not None
        return self._ledger_root / kind.value / venue / market


# --- chain DataSource --------------------------------------------------------


class TardisVendorSource(DataSource):
    """The Tardis tier of the source chain — after the free-venue provider.

    ``available()`` is the **entitlement gate** (D23): with no tenant
    ``TARDIS_API_KEY`` it declares nothing, so coverage reflects what the user
    can actually pull. Free first-of-month days are deliberately *not*
    advertised keyless — twelve scattered single days a year would fragment
    every coverage/gate computation for near-zero user value; ``fetch`` still
    serves them when explicitly pulled (the fixtures lane). With a key,
    coverage is the per-symbol ``[availableSince, min(availableTo, now))``
    intersected with the request. ``fetch`` delegates to the fetcher; the
    DataManager's write-through (plus the fetcher's own day-level sink) lands
    every byte in the user's local cache only.
    """

    name = "tardis"

    def __init__(
        self, fetcher: TardisFetcher, *, now_ms: Callable[[], int] | None = None
    ) -> None:
        self._fetcher = fetcher
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    def available(
        self, venue: str, market: str, kind: Kind, want: TimeRange
    ) -> RangeSet:
        if not self._fetcher.has_key():
            return RangeSet()  # coverage reflects entitlement (D23)
        if not self._fetcher.supports(venue, market, kind):
            return RangeSet()
        avail = self._fetcher.availability(market, kind)
        if avail is None:
            return RangeSet()
        end = min(avail.end_ms, self._now_ms())
        if end <= avail.start_ms:
            return RangeSet()
        return RangeSet((TimeRange(avail.start_ms, end),)).intersect(
            RangeSet((want,))
        )

    def fetch(
        self, venue: str, market: str, kind: Kind, span: TimeRange
    ) -> pa.Table:
        return self._fetcher.fetch(venue, market, kind, span)

    def available_funding(
        self, venue: str, market: str, want: TimeRange, *, rate_type: str = "final"
    ) -> RangeSet:
        """Tardis FUNDING is ``derivative_ticker`` — the *predicted* series only.

        It never contributes final-series coverage, so it can never satisfy the
        funding hard gate (§6.4): the settled series comes from the venue's own
        history (HL REST / the hosted lake). Predicted coverage — the true
        rate-path a tick strategy sees — is what this tier adds.
        """
        if rate_type != "predicted":
            return RangeSet()
        return self.available(venue, market, Kind.FUNDING, want)

    def backfill_advert(
        self, venue: str, market: str, kind: Kind
    ) -> dict[str, Any] | None:
        """The vendor-backfill option for a coverage gap — advertised keyless.

        Entitlement gates *coverage* (``available()``), never the advertisement:
        the exchanges metadata endpoint is free, so a keyless user still learns
        exactly what a ``TARDIS_API_KEY`` would unlock (§B7). Returns None when
        the vendor has no dataset for this (market, kind).
        """
        if venue != self._fetcher.exchange:
            return None
        window = self._fetcher.availability(market, kind)
        return {
            "vendor": "tardis",
            "requires_secret": "TARDIS_API_KEY",
            "available": (
                {"start_ms": window.start_ms, "end_ms": window.end_ms}
                if window is not None
                else None
            ),
        }


# --- helpers ------------------------------------------------------------------


def _iso_ms(value: Any) -> int | None:
    """ISO-8601 (``2024-10-29T00:00:00.000Z``) → unix-ms, or None."""
    if not value:
        return None
    return int(datetime.fromisoformat(str(value)).timestamp() * 1000)


def _utc_days(span: TimeRange) -> Iterator[int]:
    """Start-of-day unix-ms for every UTC day intersecting ``span``."""
    day = span.start_ms - (span.start_ms % _DAY_MS)
    while day < span.end_ms:
        yield day
        day += _DAY_MS


def _slice_half_open(table: pa.Table, span: TimeRange) -> pa.Table:
    if table.num_rows == 0:
        return table
    import pyarrow.compute as pc

    ts = table["ts"]
    mask = pc.and_(pc.greater_equal(ts, span.start_ms), pc.less(ts, span.end_ms))
    return table.filter(mask)


def _ts_sorted(table: pa.Table) -> bool:
    import pyarrow.compute as pc

    ts = table["ts"].combine_chunks()
    if len(ts) < 2:
        return True
    return bool(pc.all(pc.greater_equal(ts[1:], ts[:-1])).as_py())
