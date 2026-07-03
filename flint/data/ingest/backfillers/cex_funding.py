"""Read-only CEX funding (+OI) ingestion via CCXT (§10, D28).

The cross-venue funding & basis lab (§10) is a headline differentiator: it pulls
funding from many venues, normalises everything to a comparable **hourly** rate,
and shows each venue's dislocation from the cross-venue average. In v1 (D28) every
venue except Hyperliquid is **read-only** — a strategy *sees* Binance/Bybit/OKX
funding and trades the HL side of a dislocation; taking the full cross-venue
position unlocks when those venues become executable. This module ships that
read-only lane: a ``VenueProvider`` per CEX that fetches funding-rate history
(and open interest where the venue advertises it) through CCXT.

Design, mirroring the 2.4 HL provider:

* **Injectable transport seam.** CCXT is reached only through the
  ``CcxtExchangeFactory`` Protocol, so every test injects a fake exchange that
  replays recorded response fragments — no ingestion test opens a socket (D26).
* **Lazy ``ccxt`` import.** ``LazyCcxtFactory`` imports ``ccxt`` on first use, so
  importing this module (and running the mocked suite) never needs it installed.
* **VenueProvider surface.** One instance per venue (``CexFundingProvider(venue,
  factory)``) plugs straight into ``FreeVenueProvider`` for ``Kind.FUNDING`` (and
  ``Kind.OI`` where the venue supports it). A ``backfill`` convenience runs the
  §9.0 pre-write bars and upserts idempotently into a sink (the local cache/lake).

**Normalisation to ``rate_hourly`` (§10).** A CEX quotes the rate charged at each
settlement (Binance/Bybit/OKX are 8h by default; some symbols 4h/1h), so the raw
per-interval rate is divided by the interval in hours to a comparable hourly rate.
The native cadence is preserved in ``interval_s`` (derived from the spacing of
consecutive settlements, falling back to the venue default), and ``price_basis`` /
``rate_type`` record that CEX funding is settled (``final``) against ``mark``.

**OI is best-effort and partial (v1).** CCXT's open-interest history carries only
the outstanding amount, not the mark/oracle/funding a full ``asset_ctxs`` row has,
so a CEX ``Kind.OI`` row populates ``oi`` and leaves the price/funding columns at
0.0. It exists to feed the lab's context, not to stand in for an HL context row.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

import pyarrow as pa

from ..quality import BackfillResult, check_prewrite
from ...normalize import FUNDING_SCHEMA, OI_SCHEMA
from ...ranges import Kind, TimeRange

# CEX funding is settled against the mark price at each interval and the history
# endpoint returns already-settled rates (§10). These are venue facts (a VenueSpec
# concern), not magic numbers the engine reads.
_CEX_PRICE_BASIS = "mark"
_CEX_RATE_TYPE = "final"

# Default settlement cadence when a batch is too small to derive it from the data
# (all three v1 CEXes settle every 8h by default; per-symbol 4h/1h is derived).
_DEFAULT_INTERVAL_S = 28_800

# Conservative per-venue history floors (USDT-M perp inception). The Flint Data API
# coverage matrix (2.7) is the authority on real per-market coverage; these floors
# keep the funding hard gate honest — a request before the venue existed is a gap,
# not a silent pass.
CEX_INCEPTION_MS: dict[str, int] = {
    "binance": 1_567_900_800_000,  # 2019-09-08  (USDT-M perps)
    "bybit": 1_583_020_800_000,  # 2020-03-01
    "okx": 1_565_222_400_000,  # 2019-08-08
}


@runtime_checkable
class CcxtExchange(Protocol):
    """The slice of a ``ccxt`` exchange this module drives (funding + OI history).

    Matched by every ``ccxt`` exchange class and by the test fakes. ``has`` is
    CCXT's capability map — read to decide whether the venue serves OI history.
    """

    has: Mapping[str, Any]

    def fetch_funding_rate_history(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
        params: Mapping[str, Any] = ...,
    ) -> list[Mapping[str, Any]]: ...

    def fetch_open_interest_history(
        self,
        symbol: str,
        timeframe: str = ...,
        since: int | None = None,
        limit: int | None = None,
        params: Mapping[str, Any] = ...,
    ) -> list[Mapping[str, Any]]: ...


class CcxtExchangeFactory(Protocol):
    """Builds a ``CcxtExchange`` for a venue id — the injectable transport seam."""

    def create(self, venue: str) -> CcxtExchange: ...


class LazyCcxtFactory:
    """Production factory — constructs ``ccxt.<venue>()`` with ``ccxt`` imported lazily.

    Read-only: only public market-data endpoints are used, so no API keys are set.
    ``enableRateLimit`` is on so a real backfill respects each venue's limits.
    """

    def __init__(self, *, options: Mapping[str, Any] | None = None) -> None:
        self._options = dict(options or {})

    def create(self, venue: str) -> CcxtExchange:
        import ccxt

        try:
            ctor = getattr(ccxt, venue)
        except AttributeError:
            raise ValueError(f"unknown ccxt venue: {venue!r}") from None
        config: dict[str, Any] = {"enableRateLimit": True}
        config.update(self._options)
        return ctor(config)


def cex_symbol(market: str, *, quote: str = "USDT") -> str:
    """Map a Flint market to a CCXT unified swap symbol: ``SOL-PERP`` -> ``SOL/USDT:USDT``."""
    coin = market[:-5] if market.endswith("-PERP") else market
    return f"{coin}/{quote}:{quote}"


def _derive_interval_s(sorted_ts: Sequence[int], default_s: int) -> int:
    """Infer the settlement cadence from the spacing of consecutive settlements.

    The smallest positive gap between settlements is the cadence (larger gaps are
    missed settlements, not a slower cadence). Falls back to ``default_s`` when the
    batch has fewer than two rows to measure.
    """
    best_ms = 0
    for a, b in zip(sorted_ts, sorted_ts[1:]):
        gap = b - a
        if gap > 0 and (best_ms == 0 or gap < best_ms):
            best_ms = gap
    return best_ms // 1000 if best_ms > 0 else default_s


def funding_rate_to_hourly(per_interval_rate: float, interval_s: int) -> float:
    """Divide a per-settlement rate by the interval in hours to a comparable hourly rate."""
    if interval_s <= 0:
        raise ValueError(f"interval_s must be positive, got {interval_s}")
    return per_interval_rate * 3600.0 / interval_s


class CexFundingProvider:
    """Read-only CEX funding (+OI) provider for one venue — a ``VenueProvider`` (§10).

    Construct one per venue (``CexFundingProvider("binance", factory)``); compose
    several into ``FreeVenueProvider`` to feed the funding/basis lab. The CCXT
    exchange is built lazily on first fetch and cached. Read-only: no execution
    surface is exposed here (D28).
    """

    def __init__(
        self,
        venue: str,
        factory: CcxtExchangeFactory,
        *,
        symbol_map: Mapping[str, str] | None = None,
        quote: str = "USDT",
        coverage_floor_ms: int | None = None,
        page_limit: int = 1000,
        oi_timeframe: str = "1h",
    ) -> None:
        self.venue = venue
        self._factory = factory
        self._symbol_map = dict(symbol_map or {})
        self._quote = quote
        self._floor = (
            coverage_floor_ms
            if coverage_floor_ms is not None
            else CEX_INCEPTION_MS.get(venue)
        )
        self._page_limit = page_limit
        self._oi_timeframe = oi_timeframe
        self._exchange: CcxtExchange | None = None

    # --- lazy exchange ----------------------------------------------------

    def _ex(self) -> CcxtExchange:
        if self._exchange is None:
            self._exchange = self._factory.create(self.venue)
        return self._exchange

    def _symbol(self, market: str) -> str:
        return self._symbol_map.get(market) or cex_symbol(market, quote=self._quote)

    def _has_oi_history(self) -> bool:
        return bool(self._ex().has.get("fetchOpenInterestHistory"))

    # --- VenueProvider surface --------------------------------------------

    def supports(self, market: str, kind: Kind) -> bool:
        if kind is Kind.FUNDING:
            return True
        if kind is Kind.OI:
            return self._has_oi_history()
        return False

    def coverage_floor(self, market: str, kind: Kind) -> int | None:
        return self._floor if self.supports(market, kind) else None

    def fetch_range(self, market: str, kind: Kind, span: TimeRange) -> pa.Table:
        if kind is Kind.FUNDING:
            if span.is_empty:
                return FUNDING_SCHEMA.empty_table()
            return self._fetch_funding(market, span)
        if kind is Kind.OI and self._has_oi_history():
            if span.is_empty:
                return OI_SCHEMA.empty_table()
            return self._fetch_oi(market, span)
        return (OI_SCHEMA if kind is Kind.OI else FUNDING_SCHEMA).empty_table()

    # --- funding (time-paged, normalised to hourly) -----------------------

    def _fetch_funding(self, market: str, span: TimeRange) -> pa.Table:
        symbol = self._symbol(market)
        ex = self._ex()
        raw: list[tuple[int, float]] = []
        cursor = span.start_ms
        while cursor < span.end_ms:
            page = ex.fetch_funding_rate_history(symbol, since=cursor, limit=self._page_limit) or []
            last_ts = cursor
            for entry in page:
                ts = entry.get("timestamp")
                rate = entry.get("fundingRate")
                if ts is None or rate is None:
                    continue
                ts = int(ts)
                last_ts = max(last_ts, ts)
                if span.start_ms <= ts < span.end_ms:
                    raw.append((ts, float(rate)))
            next_cursor = last_ts + 1
            if len(page) < self._page_limit or next_cursor <= cursor:
                break
            cursor = next_cursor

        by_ts = {ts: rate for ts, rate in raw}
        ordered_ts = sorted(by_ts)
        interval_s = _derive_interval_s(ordered_ts, _DEFAULT_INTERVAL_S)
        rows = [
            {
                "ts": ts,
                "rate_hourly": funding_rate_to_hourly(by_ts[ts], interval_s),
                "interval_s": interval_s,
                "price_basis": _CEX_PRICE_BASIS,
                "rate_type": _CEX_RATE_TYPE,
                "venue": self.venue,
                "market": market,
                # A final rate settles at its own ts (§6.4).
                "settlement_ts": ts,
            }
            for ts in ordered_ts
        ]
        return pa.Table.from_pylist(rows, schema=FUNDING_SCHEMA)

    # --- open interest (best-effort; only ``oi`` populated) ---------------

    def _fetch_oi(self, market: str, span: TimeRange) -> pa.Table:
        symbol = self._symbol(market)
        ex = self._ex()
        by_ts: dict[int, float] = {}
        cursor = span.start_ms
        while cursor < span.end_ms:
            page = (
                ex.fetch_open_interest_history(
                    symbol, self._oi_timeframe, since=cursor, limit=self._page_limit
                )
                or []
            )
            last_ts = cursor
            for entry in page:
                ts = entry.get("timestamp")
                if ts is None:
                    continue
                ts = int(ts)
                last_ts = max(last_ts, ts)
                if span.start_ms <= ts < span.end_ms:
                    amount = entry.get("openInterestAmount")
                    by_ts[ts] = float(amount) if amount is not None else 0.0
            next_cursor = last_ts + 1
            if len(page) < self._page_limit or next_cursor <= cursor:
                break
            cursor = next_cursor

        rows = [
            {
                "ts": ts,
                "market": market,
                "venue": self.venue,
                "oi": by_ts[ts],
                "mark_price": 0.0,
                "index_price": 0.0,
                "funding_hourly": 0.0,
            }
            for ts in sorted(by_ts)
        ]
        return pa.Table.from_pylist(rows, schema=OI_SCHEMA)

    # --- backfill (fetch -> §9.0 quality bars -> idempotent upsert) --------

    def backfill(
        self, market: str, kind: Kind, span: TimeRange, sink: Any
    ) -> BackfillResult:
        """Fetch ``kind`` over ``span``, run the pre-write bars, upsert into ``sink``.

        ``sink`` is an ``UpsertSink`` (``store(venue, market, kind, table)``); the
        table is keyed by ``ts`` so re-running the same range is a no-op (idempotent).
        Funding/OI carry no candle price or volume column, so only the ordering and
        duplicate-key checks apply (§9.0).
        """
        table = self.fetch_range(market, kind, span)
        quality = check_prewrite(table, price_col=None, volume_col=None)
        written = 0
        if quality.ok and table.num_rows:
            sink.store(self.venue, market, kind, table)
            written = table.num_rows
        return BackfillResult(kind=str(kind), rows_written=written, quality=quality)
