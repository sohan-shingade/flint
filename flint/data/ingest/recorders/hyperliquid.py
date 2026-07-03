"""Hyperliquid WS recorders — the now-or-never capture, stand up FIRST (§9.1).

Records the three HL streams with no historical source: ``trades`` (HL's
now-or-never gap — nothing backfills pre-recording trade prints), ``l2Book``
depth (backfillable from S3 too, but recorded live for the gap between monthly
archive drops), and ``activeAssetCtx`` (live OI + mark + oracle + funding).

Every frame is parsed by the **shared** ``data.normalize`` (the same parser the
S3 backfiller and the Phase-5 LiveFeed use — §6.7), classified by the sequence
tracker, timestamped into the lag monitor, buffered, and upserted idempotently
keyed ``(venue, market, ts)``. The recorder consumes any ``WsMessageSource``, so
the production async socket and a deterministic replay list are the same object
to it — swapping the live feed in is zero recorder change.

No socket is opened here: ``WsMessageSource`` is the injectable seam (mirrors
``ingest/transport.py``), and tests drive it with ``ReplayWsSource`` over recorded
frame fragments (D26 — recorded fragments / unit inputs, never generated data).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa

from ...normalize import (
    VENUE_HYPERLIQUID,
    AssetContext,
    TradePrint,
    books_to_arrow,
    contexts_to_arrow,
    normalize_active_asset_ctx,
    normalize_l2book,
    normalize_trades,
    trades_to_arrow,
)
from ...ranges import Kind
from ..backfillers.hyperliquid import UpsertSink
from .monitor import LagMonitor, SeqMode, SequenceTracker

# HL WS channel -> the Kind it records.
_CHANNEL_KIND = {
    "trades": Kind.TRADES,
    "l2Book": Kind.DEPTH,
    "activeAssetCtx": Kind.OI,
}


class WsMessageSource(ABC):
    """A stream of ``(channel, data)`` frames — the injectable WS seam.

    Production is an async socket wrapper (lands with the LiveFeed, Phase 5, over
    the same seam); tests use ``ReplayWsSource``.
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
    """Capture HL trades + l2Book + activeAssetCtx into the store (§9.1).

    ``clock`` supplies the receipt time for ``activeAssetCtx`` frames (which carry
    no event timestamp) and drives the lag monitor. Buffered frames are flushed to
    the sink in batches; the sink's upsert keyed ``(venue, market, ts)`` makes a
    re-recorded or replayed frame a no-op.
    """

    def __init__(
        self,
        sink: UpsertSink,
        *,
        clock: Callable[[], int],
        venue: str = VENUE_HYPERLIQUID,
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._venue = venue
        self.lag = LagMonitor(clock)
        # trades carry a strictly-increasing global tid; book/ctx carry event
        # (or receipt) timestamps that may repeat, so non-decreasing.
        self._seq_trades = SequenceTracker(SeqMode.STRICTLY_INCREASING)
        self._seq_time = SequenceTracker(SeqMode.NON_DECREASING)
        # Buffers keyed by (market, kind) so a flush upserts per stream.
        self._trades: dict[str, list[TradePrint]] = {}
        self._books: dict[str, list[Any]] = {}
        self._contexts: dict[str, list[AssetContext]] = {}

    # --- per-frame handling ------------------------------------------------

    def process(self, channel: str, data: Any) -> None:
        """Normalize + track one WS frame and buffer it (no write yet)."""
        if channel == "trades":
            self._on_trades(data)
        elif channel == "l2Book":
            self._on_book(data)
        elif channel == "activeAssetCtx":
            self._on_ctx(data)
        # Unknown channels are ignored — a recorder never guesses at a shape.

    def _on_trades(self, data: Any) -> None:
        for p in normalize_trades(data):
            self._seq_trades.observe((p.market, Kind.TRADES), p.trade_id)
            self.lag.record(self._venue, p.market, Kind.TRADES, p.ts)
            self._trades.setdefault(p.market, []).append(p)

    def _on_book(self, data: Any) -> None:
        book = normalize_l2book(data)
        self._seq_time.observe((book.market, Kind.DEPTH), book.ts)
        self.lag.record(self._venue, book.market, Kind.DEPTH, book.ts)
        self._books.setdefault(book.market, []).append(book)

    def _on_ctx(self, data: Any) -> None:
        ctx = normalize_active_asset_ctx(data, recv_ts=self._clock())
        self._seq_time.observe((ctx.market, Kind.OI), ctx.ts)
        self.lag.record(self._venue, ctx.market, Kind.OI, ctx.ts)
        self._contexts.setdefault(ctx.market, []).append(ctx)

    # --- persistence -------------------------------------------------------

    def flush(self) -> dict[Kind, int]:
        """Upsert every buffered stream to the sink; return rows written per kind."""
        written: dict[Kind, int] = {}
        for market, prints in self._trades.items():
            written[Kind.TRADES] = written.get(Kind.TRADES, 0) + self._flush_one(
                market, Kind.TRADES, trades_to_arrow(prints)
            )
        for market, books in self._books.items():
            written[Kind.DEPTH] = written.get(Kind.DEPTH, 0) + self._flush_one(
                market, Kind.DEPTH, books_to_arrow(books)
            )
        for market, contexts in self._contexts.items():
            written[Kind.OI] = written.get(Kind.OI, 0) + self._flush_one(
                market, Kind.OI, contexts_to_arrow(contexts)
            )
        self._trades.clear()
        self._books.clear()
        self._contexts.clear()
        return {k: v for k, v in written.items() if v}

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
