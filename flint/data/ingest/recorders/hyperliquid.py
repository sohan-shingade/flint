"""Hyperliquid WS recorders — the now-or-never capture, stand up FIRST (§9.1).

Records the four HL streams with no (or partial) historical source: ``trades``
(HL's now-or-never gap — nothing backfills pre-recording trade prints), ``bbo``
top-of-book (the Tier-2 QUOTES lane, §9.2), ``l2Book`` depth (backfillable from
S3 too, but recorded live for the gap between monthly archive drops), and
``activeAssetCtx`` (live OI + mark + oracle + funding — folded into an OI row
*and* a **predicted** FUNDING row, §6.4/§9.2).

Every frame is parsed by the **shared** ``data.normalize`` (the same parser the
S3 backfiller and the Phase-5 LiveFeed use — §6.7), classified by the sequence
tracker, timestamped into the lag monitor, buffered, and upserted idempotently
keyed ``(venue, market, ts)``. The recorder consumes any ``WsMessageSource``, so
the production async socket and a deterministic replay list are the same object
to it — swapping the live feed in is zero recorder change.

**Coverage is asserted, never inferred, for what the recorder captures (§9.2).**
When a ``ledger_of`` factory is wired, the recorder tracks one open coverage
window per ``(market, kind)`` stream and asserts it into the
:class:`~flint.data.store.coverage.CoverageLedger` with provenance
``"recorder"``:

* a window opens at ``session_start_ts`` (or the stream's first event) and its
  high-water mark advances with every event;
* ``flush()`` asserts ``[window_start, newest_event_ts)`` and continues the
  window from ``newest_event_ts`` — half-open at the newest event because at
  millisecond granularity a later event in the same ms could still be dropped;
* a ``SequenceTracker`` **gap** or an explicit :meth:`mark_disconnect`
  (reconnect) closes the window at the last good event; the next event reopens
  it at its own timestamp — the missed span is *never* claimed covered.

This is what makes recorded ticks count: tick-kind coverage is ledger-only
(§9.2 / D1), so a recorded span without an assertion contributes nothing to
``available()`` no matter how many rows landed.

No socket is opened here: ``WsMessageSource`` is the injectable seam (mirrors
``ingest/transport.py``; the live socket lives in ``recorders/ws.py``), and
tests drive it with ``ReplayWsSource`` over recorded frame fragments (D26 —
recorded fragments / unit inputs, never generated data).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa

from flint.core.models import OrderbookSnapshot
from flint.core.time import bar_end, bar_start

from ...normalize import (
    VENUE_HYPERLIQUID,
    AssetContext,
    FundingObservation,
    QuoteTick,
    TradePrint,
    books_to_arrow,
    contexts_to_arrow,
    fundings_to_arrow,
    normalize_active_asset_ctx,
    normalize_bbo,
    normalize_l2book,
    normalize_trades,
    quotes_to_arrow,
    trades_to_arrow,
)
from ...ranges import Kind, TimeRange
from ...store.coverage import FUNDING_PREDICTED_VARIANT
from ...store.coverage import CoverageLedger
from ..backfillers.hyperliquid import UpsertSink
from .monitor import LagMonitor, SeqMode, SequenceTracker

# HL WS channel -> the primary Kind it records. ``activeAssetCtx`` additionally
# folds a predicted FUNDING row out of the same frame (§9.2).
_CHANNEL_KIND = {
    "trades": Kind.TRADES,
    "bbo": Kind.QUOTES,
    "l2Book": Kind.DEPTH,
    "activeAssetCtx": Kind.OI,
}

# HL funding settles hourly; the predicted rate captured mid-hour settles at
# the next hour boundary (§6.4).
_FUNDING_INTERVAL_S = 3600

# A factory yielding the asserted-coverage ledger of one (venue, market, kind)
# stream directory — ``DurableCacheSource.coverage_ledger`` in production.
LedgerFactory = Callable[[str, str, Kind], CoverageLedger]


def next_hour_boundary(ts_ms: int) -> int:
    """The first hour boundary strictly after ``ts_ms`` (unix ms, UTC).

    A predicted rate captured exactly on the boundary settles at the *next*
    one — the settlement at ``ts_ms`` itself has already happened.
    """
    return bar_end(bar_start(ts_ms, _FUNDING_INTERVAL_S), _FUNDING_INTERVAL_S)


class WsMessageSource(ABC):
    """A stream of ``(channel, data)`` frames — the injectable WS seam.

    Production is a socket wrapper (``recorders/ws.py``, same seam); tests use
    ``ReplayWsSource``.
    """

    @abstractmethod
    def messages(self) -> Iterator[tuple[str, Any]]:
        """Yield ``(channel, data)`` frames until the stream ends."""
        ...


class ReplayWsSource(WsMessageSource):
    """A deterministic ``WsMessageSource`` over a fixed list of frames."""

    def __init__(self, frames: list[tuple[str, Any]]) -> None:
        self._frames = list(frames)

    def messages(self) -> Iterator[tuple[str, Any]]:
        yield from self._frames


@dataclass
class RecorderStats:
    """What a recorder run captured — rows persisted per kind + anomaly counts."""

    rows_written: dict[Kind, int] = field(default_factory=dict)
    seq_gaps: int = 0
    seq_regressions: int = 0
    seq_duplicates: int = 0

    def _add(self, kind: Kind, n: int) -> None:
        self.rows_written[kind] = self.rows_written.get(kind, 0) + n


class HyperliquidRecorder:
    """Capture HL trades + bbo + l2Book + activeAssetCtx into the store (§9.1).

    ``clock`` supplies the receipt time for ``activeAssetCtx`` frames (which carry
    no event timestamp) and drives the lag monitor. Buffered frames are flushed to
    the sink in batches; the sink's upsert keyed ``(venue, market, ts)`` makes a
    re-recorded or replayed frame a no-op.

    ``ledger_of`` (optional) wires the CoverageLedger hook described in the
    module docstring; ``session_start_ts`` anchors the first coverage window of
    every stream at the subscribe time, so a quiet market between subscribe and
    its first event is covered *data*, not a hole (§9.2).

    **Predicted-funding dedupe policy:** ``activeAssetCtx`` arrives on every
    block (sub-second), so a FUNDING row per frame would be thousands of
    near-identical rows an hour. The recorder emits a predicted row only when
    the rate *changes* (plus the first frame per market per session); the OI
    fold still lands every frame, so the full observation cadence is preserved
    there while FUNDING carries the information-bearing rate path.
    """

    def __init__(
        self,
        sink: UpsertSink,
        *,
        clock: Callable[[], int],
        venue: str = VENUE_HYPERLIQUID,
        ledger_of: LedgerFactory | None = None,
        session_start_ts: int | None = None,
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._venue = venue
        self.lag = LagMonitor(clock)
        # trades carry a strictly-increasing global tid; bbo/book/ctx carry event
        # (or receipt) timestamps that may repeat, so non-decreasing.
        self._seq_trades = SequenceTracker(SeqMode.STRICTLY_INCREASING)
        self._seq_time = SequenceTracker(SeqMode.NON_DECREASING)
        # Buffers keyed by (market, kind) so a flush upserts per stream.
        self._trades: dict[str, list[TradePrint]] = {}
        self._quotes: dict[str, list[QuoteTick]] = {}
        self._books: dict[str, list[OrderbookSnapshot]] = {}
        self._contexts: dict[str, list[AssetContext]] = {}
        self._fundings: dict[str, list[FundingObservation]] = {}
        # Change-only predicted-funding dedupe: last emitted rate per market.
        self._last_predicted: dict[str, float] = {}
        # Coverage windows (see module docstring): per (market, kind) stream,
        # the open window start + its newest observed event ts.
        self._ledger_of = ledger_of
        self._session_start_ts = session_start_ts
        self._cov_open: dict[tuple[str, Kind], int] = {}
        self._cov_newest: dict[tuple[str, Kind], int] = {}
        # Streams that already opened their first window: only that first one
        # is anchored at session_start_ts; a post-gap reopen starts at the
        # recovery event (the missed span stays uncovered).
        self._cov_started: set[tuple[str, Kind]] = set()

    # --- per-frame handling ------------------------------------------------

    def process(self, channel: str, data: Any) -> None:
        """Normalize + track one WS frame and buffer it (no write yet)."""
        if channel == "trades":
            self._on_trades(data)
        elif channel == "bbo":
            self._on_bbo(data)
        elif channel == "l2Book":
            self._on_book(data)
        elif channel == "activeAssetCtx":
            self._on_ctx(data)
        # Unknown channels are ignored — a recorder never guesses at a shape.

    def _on_trades(self, data: Any) -> None:
        for p in normalize_trades(data):
            res = self._seq_trades.observe((p.market, Kind.TRADES), p.trade_id)
            self.lag.record(self._venue, p.market, Kind.TRADES, p.ts)
            self._trades.setdefault(p.market, []).append(p)
            self._observe_coverage(p.market, Kind.TRADES, p.ts, res.status)

    def _on_bbo(self, data: Any) -> None:
        quote = normalize_bbo(data)
        res = self._seq_time.observe((quote.market, Kind.QUOTES), quote.ts)
        self.lag.record(self._venue, quote.market, Kind.QUOTES, quote.ts)
        self._quotes.setdefault(quote.market, []).append(quote)
        self._observe_coverage(quote.market, Kind.QUOTES, quote.ts, res.status)

    def _on_book(self, data: Any) -> None:
        book = normalize_l2book(data)
        res = self._seq_time.observe((book.market, Kind.DEPTH), book.ts)
        self.lag.record(self._venue, book.market, Kind.DEPTH, book.ts)
        self._books.setdefault(book.market, []).append(book)
        self._observe_coverage(book.market, Kind.DEPTH, book.ts, res.status)

    def _on_ctx(self, data: Any) -> None:
        ctx = normalize_active_asset_ctx(data, recv_ts=self._clock())
        res = self._seq_time.observe((ctx.market, Kind.OI), ctx.ts)
        self.lag.record(self._venue, ctx.market, Kind.OI, ctx.ts)
        self._contexts.setdefault(ctx.market, []).append(ctx)
        self._observe_coverage(ctx.market, Kind.OI, ctx.ts, res.status)
        # Predicted-funding fold (§9.2): emit on rate change only (see class
        # docstring). ts = capture clock; settlement = the upcoming hour
        # boundary; the HL rate is already an hourly rate, priced off oracle.
        last = self._last_predicted.get(ctx.market)
        if last is None or last != ctx.funding_hourly:
            self._last_predicted[ctx.market] = ctx.funding_hourly
            self._fundings.setdefault(ctx.market, []).append(
                FundingObservation(
                    ts=ctx.ts,
                    market=ctx.market,
                    venue=self._venue,
                    rate_hourly=ctx.funding_hourly,
                    interval_s=_FUNDING_INTERVAL_S,
                    price_basis="oracle",
                    rate_type="predicted",
                    settlement_ts=next_hour_boundary(ctx.ts),
                )
            )
        # FUNDING coverage advances with every ctx frame — watching an
        # unchanged rate is observation, not absence (the change-only dedupe
        # thins rows, never coverage).
        self._observe_coverage(ctx.market, Kind.FUNDING, ctx.ts, res.status)

    # --- coverage windows (§9.2) --------------------------------------------

    def _observe_coverage(
        self, market: str, kind: Kind, ts: int, seq_status: str
    ) -> None:
        """Advance the stream's coverage window; a sequence gap splits it."""
        if self._ledger_of is None:
            return
        key = (market, kind)
        if seq_status == "gap" and key in self._cov_open:
            # Close at the last good event; the missed span stays uncovered.
            self._assert_window(key)
            del self._cov_open[key]
            del self._cov_newest[key]
        if key not in self._cov_open:
            if key not in self._cov_started and self._session_start_ts is not None:
                # First window of the session: covered since subscribe time —
                # a quiet stream before its first event is data, not a gap.
                start = min(self._session_start_ts, ts)
            else:
                start = ts
            self._cov_open[key] = start
            self._cov_started.add(key)
        newest = self._cov_newest.get(key)
        if newest is None or ts > newest:
            self._cov_newest[key] = ts

    def _assert_window(self, key: tuple[str, Kind]) -> None:
        """Persist ``[window_start, newest_event)`` for one stream, if non-empty."""
        assert self._ledger_of is not None
        start = self._cov_open[key]
        newest = self._cov_newest.get(key)
        if newest is None or newest <= start:
            return
        market, kind = key
        ledger = self._ledger_of(self._venue, market, kind)
        # The recorder's FUNDING rows are the ctx fold's *predicted* rates
        # (rate_type="predicted") — real observation, but not settled history.
        # Assert them under the predicted variant so watching the rate path
        # never satisfies the final-series funding hard gate (§6.4).
        variant = FUNDING_PREDICTED_VARIANT if kind is Kind.FUNDING else ""
        ledger.assert_covered(TimeRange(start, newest), "recorder", variant=variant)

    def mark_disconnect(self) -> None:
        """Close every open coverage window at its last good event (reconnect).

        The span between this call and the next observed event is *not*
        covered; each stream's window reopens at its first post-recovery
        event. A no-op when no window is open (fresh recorder, first connect).
        """
        for key in list(self._cov_open):
            self._assert_window(key)
        self._cov_open.clear()
        self._cov_newest.clear()

    # --- persistence -------------------------------------------------------

    def flush(self) -> dict[Kind, int]:
        """Upsert every buffered stream to the sink; return rows written per kind.

        Also asserts each stream's coverage window up to its newest event and
        continues the window from there (§9.2) — so a crash between flushes
        loses at most one flush interval of *coverage*, never a lie.
        """
        written: dict[Kind, int] = {}
        for market, prints in self._trades.items():
            written[Kind.TRADES] = written.get(Kind.TRADES, 0) + self._flush_one(
                market, Kind.TRADES, trades_to_arrow(prints)
            )
        for market, quotes in self._quotes.items():
            written[Kind.QUOTES] = written.get(Kind.QUOTES, 0) + self._flush_one(
                market, Kind.QUOTES, quotes_to_arrow(quotes)
            )
        for market, books in self._books.items():
            written[Kind.DEPTH] = written.get(Kind.DEPTH, 0) + self._flush_one(
                market, Kind.DEPTH, books_to_arrow(books)
            )
        for market, contexts in self._contexts.items():
            written[Kind.OI] = written.get(Kind.OI, 0) + self._flush_one(
                market, Kind.OI, contexts_to_arrow(contexts)
            )
        for market, fundings in self._fundings.items():
            written[Kind.FUNDING] = written.get(Kind.FUNDING, 0) + self._flush_one(
                market, Kind.FUNDING, fundings_to_arrow(fundings)
            )
        self._trades.clear()
        self._quotes.clear()
        self._books.clear()
        self._contexts.clear()
        self._fundings.clear()
        if self._ledger_of is not None:
            for key in list(self._cov_open):
                self._assert_window(key)
                newest = self._cov_newest.get(key)
                if newest is not None and newest > self._cov_open[key]:
                    self._cov_open[key] = newest  # window continues seamlessly
        return {k: v for k, v in written.items() if v}

    def close_session(self) -> dict[Kind, int]:
        """Graceful shutdown: final flush + close every coverage window.

        Returns what the final flush wrote. After this the recorder can be
        reused — the next event opens a fresh window at its own timestamp.
        """
        written = self.flush()
        self.mark_disconnect()
        return written

    def _flush_one(self, market: str, kind: Kind, table: pa.Table) -> int:
        if table.num_rows == 0:
            return 0
        self._sink.store(self._venue, market, kind, table)
        return table.num_rows

    # --- driving a source --------------------------------------------------

    def run(self, source: WsMessageSource, *, flush_every: int = 100) -> RecorderStats:
        """Consume ``source`` to exhaustion, flushing every ``flush_every`` frames."""
        stats = RecorderStats()
        seen = 0
        for channel, data in source.messages():
            self.process(channel, data)
            seen += 1
            if seen % flush_every == 0:
                for kind, n in self.flush().items():
                    stats._add(kind, n)
        for kind, n in self.flush().items():
            stats._add(kind, n)
        stats.seq_gaps = self._seq_trades.gaps + self._seq_time.gaps
        stats.seq_regressions = self._seq_trades.regressions + self._seq_time.regressions
        stats.seq_duplicates = self._seq_trades.duplicates + self._seq_time.duplicates
        return stats
