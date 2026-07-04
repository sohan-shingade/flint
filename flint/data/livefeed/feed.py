"""The LiveFeed — venue WS in, closed bars out, gap-replay across reconnects (§6.7).

This is the paper/live counterpart of historical replay: instead of reading the
lake, the feed subscribes the venue WebSocket, normalizes each frame through the
**shared** ``data.normalize`` parsers (one parser per HL message, never two —
§6.7), and aggregates closed bars for the engine. The lake is off the hot path;
it serves only reconnect gap replay.

A session is a sequence of ``connect(source)`` calls, one per WS connection. The
feed carries a forward-only cursor (the last bar start it emitted) across them,
so a disconnect is detected as a jump in the first post-reconnect event's bar:

* the partial bar open at the disconnect is discarded (a fresh aggregator per
  connect) — its authoritative form comes back through the lake;
* the missed bars are fetched and replayed as ``gap-replay`` bars *before* the
  live stream resumes, so the engine sees one contiguous, time-ordered stream;
* what the lake lacked is recorded as a degraded :class:`~flint.data.livefeed.gap.GapRecovery`
  — never skipped, never forward-filled (§6.7 / D26).

The feed emits :class:`~flint.data.livefeed.aggregator.LiveBar`s and does **no**
engine wiring itself — the runner (slice 5.6) is the surface that owns the
engine, strategy adapter, and order state. :func:`assemble_engine_inputs` turns
an emitted bar stream into the per-market feeds a run consumes, so paper trades run
the identical Nautilus bar lane as backtest (the parity promise). T+1
execution and the latency model are the engine's and are untouched here: a bar's
``ts`` is its venue-event bar start, so ``close_event_ts + latency`` eligibility
falls out of the same code the backtest uses.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from flint.core.models import (
    Candle,
    FundingRate,
    MarkSnapshot,
    OrderbookSnapshot,
)
from flint.core.time import bar_end, bar_start
from flint.data.ingest.recorders import HyperliquidRecorder, WsMessageSource
from flint.data.normalize import (
    VENUE_HYPERLIQUID,
    TradePrint,
    normalize_active_asset_ctx,
    normalize_l2book,
    normalize_trades,
)
from flint.engine.context import OpenInterestSnapshot

from .aggregator import CandleAggregator, LiveBar
from .clock import PaperClock
from .gap import GapRecovery, GapSource

# What a paper session's recorder tee captures by default (§9.2): the tick lane
# (trades + bbo) and the ctx fold (OI + predicted funding). ``l2Book`` is off by
# default — depth frames dwarf everything else in volume and are S3-backfillable;
# opt in via ``record_channels`` for true book capture.
DEFAULT_RECORD_CHANNELS = frozenset({"trades", "bbo", "activeAssetCtx"})


@dataclass
class EngineInputs:
    """The per-market feeds a bar stream lowers to, ready for the engine (§6.7).

    Mirrors :class:`~flint.engine.api.EngineFeed`'s slots so the paper runner
    assembles the exact feed a backtest runs through the engine seam — which is
    what makes paper/live provably the same loop.
    """

    candles: list[Candle] = field(default_factory=list)
    marks: dict[str, list[MarkSnapshot]] = field(default_factory=dict)
    funding: dict[str, list[FundingRate]] = field(default_factory=dict)
    oi: dict[str, list[OpenInterestSnapshot]] = field(default_factory=dict)
    books: dict[str, list[OrderbookSnapshot]] = field(default_factory=dict)
    trades: dict[str, list[TradePrint]] = field(default_factory=dict)


def assemble_engine_inputs(bars: Iterable[LiveBar]) -> EngineInputs:
    """Lower an emitted :class:`LiveBar` stream into :class:`EngineInputs`."""
    out = EngineInputs()
    for bar in bars:
        candle = bar.candle
        market = candle.market
        out.candles.append(candle)
        if bar.marks:
            out.marks.setdefault(market, []).extend(bar.marks)
        if bar.funding:
            out.funding.setdefault(market, []).extend(bar.funding)
        if bar.oi:
            out.oi.setdefault(market, []).extend(bar.oi)
        if bar.books:
            out.books.setdefault(market, []).extend(bar.books)
        if bar.trades:
            out.trades.setdefault(market, []).extend(bar.trades)
    return out


class LiveFeed:
    """Subscribes a venue WS and emits closed bars with reconnect gap replay (§6.7)."""

    def __init__(
        self,
        market: str,
        *,
        venue: str = VENUE_HYPERLIQUID,
        resolution_s: int,
        gap_source: GapSource | None = None,
        funding_interval_s: int = 3600,
        resume_bar_start: int | None = None,
        recorder_sink: HyperliquidRecorder | None = None,
        record_channels: frozenset[str] = DEFAULT_RECORD_CHANNELS,
    ) -> None:
        if resolution_s <= 0:
            raise ValueError(f"resolution_s must be positive, got {resolution_s}")
        self._market = market
        self._venue = venue
        self._resolution_s = resolution_s
        self._res_ms = resolution_s * 1000
        self._gap_source = gap_source
        self._funding_interval_s = funding_interval_s
        self.clock = PaperClock(resolution_s)
        # ``resume_bar_start`` seeds the cursor on a session restart (§6.7): the
        # first post-restart connection is then a *reconnect*, so the bars missed
        # while the process was down are gap-replayed from the lake, and any
        # already-processed bar the new stream re-sends is de-duplicated.
        self._last_emitted_bar_start: int | None = resume_bar_start
        self._agg: CandleAggregator | None = None
        self.recoveries: list[GapRecovery] = []
        # Optional recorder tee (§9.2): the same frames the feed consumes flow
        # into the durable cache, so a paper session passively builds tick
        # history. Strictly optional — without a sink nothing here changes.
        self._recorder = recorder_sink
        self._record_channels = record_channels
        self._connected_once = False

    # -- public API ------------------------------------------------------------

    def connect(self, source: WsMessageSource) -> list[LiveBar]:
        """Process one WS connection; return the bars it closed, in time order.

        On a reconnect (this is not the first connection and the first event
        jumps past the next expected bar) the recovered ``gap-replay`` bars are
        returned first, then the live bars — one contiguous, ascending stream.
        """
        frames = list(source.messages())
        emitted: list[LiveBar] = []

        if self._recorder is not None:
            # A second connection means the previous one dropped: close the
            # recorder's coverage windows at the last good event so the offline
            # span is never claimed covered (§9.2), then tee this connection's
            # frames. The recorder buffers; flushing here persists rows +
            # asserts coverage once per connection.
            if self._connected_once:
                self._recorder.mark_disconnect()
            for channel, data in frames:
                if channel in self._record_channels:
                    self._recorder.process(channel, data)
            self._recorder.flush()
        self._connected_once = True

        first_ts = self._first_event_ts(frames)
        if first_ts is not None and self._last_emitted_bar_start is not None:
            expected_next = self._last_emitted_bar_start + self._res_ms
            first_bar = bar_start(first_ts, self._resolution_s)
            if first_bar > expected_next:
                emitted.extend(self._replay_gap(expected_next, first_bar))

        emitted.extend(self._consume_live(frames))
        return emitted

    def close_session(self) -> list[LiveBar]:
        """Flush the final open bar on a graceful (non-disconnect) session end.

        Used only when the caller knows the stream ended cleanly; a disconnect
        must *not* call this (the partial bar is authoritative from the lake).
        """
        if self._recorder is not None:
            # Graceful end: final recorder flush + coverage windows closed at
            # the last observed event (§9.2).
            self._recorder.close_session()
        if self._agg is None:
            return []
        return self._accept(self._agg.flush())

    # -- live consumption ------------------------------------------------------

    def _consume_live(self, frames: list[tuple[str, object]]) -> list[LiveBar]:
        agg = CandleAggregator(
            self._market,
            self._venue,
            self._resolution_s,
            funding_interval_s=self._funding_interval_s,
        )
        self._agg = agg
        emitted: list[LiveBar] = []
        for channel, data in frames:
            if channel == "trades":
                for p in normalize_trades(data, self._market):
                    self.clock.observe(p.ts)
                    emitted.extend(self._accept(agg.add_trade(p)))
            elif channel == "l2Book":
                book = normalize_l2book(data, self._market)
                self.clock.observe(book.ts)
                emitted.extend(self._accept(agg.add_book(book)))
            elif channel == "activeAssetCtx":
                now = self.clock.now
                if now is None:
                    # No venue time seen yet: a ctx frame carries none of its own,
                    # so it cannot be stamped without the wall clock (§6.7 forbids
                    # that). Drop it until a trade/book anchors venue time (D26).
                    continue
                ctx = normalize_active_asset_ctx(data, self._market, recv_ts=now)
                emitted.extend(self._accept(agg.add_context(ctx)))
            # unknown channels are ignored — the recorders own their own set.
        return emitted

    def _first_event_ts(self, frames: list[tuple[str, object]]) -> int | None:
        """First venue-reported event ts in ``frames`` (from a trade or book)."""
        for channel, data in frames:
            if channel == "trades":
                prints = normalize_trades(data, self._market)
                if prints:
                    return prints[0].ts
            elif channel == "l2Book":
                return normalize_l2book(data, self._market).ts
        return None

    # -- reconnect gap replay --------------------------------------------------

    def _replay_gap(self, start_ms: int, end_ms: int) -> list[LiveBar]:
        """Fetch bars in ``[start, end)`` from the lake and rebuild them (§6.7)."""
        bars_expected = max(0, (end_ms - start_ms) // self._res_ms)
        data = (
            self._gap_source.fetch(
                self._venue, self._market, start_ms, end_ms, self._resolution_s
            )
            if self._gap_source is not None
            else None
        )
        recovered: list[LiveBar] = []
        if data is not None:
            for candle in sorted(data.candles, key=lambda c: c.ts):
                if not (start_ms <= candle.ts < end_ms):
                    continue
                lo, hi = candle.ts, bar_end(candle.ts, self._resolution_s)
                recovered.append(
                    LiveBar(
                        candle=candle,
                        marks=tuple(m for m in data.marks if lo <= m.ts < hi),
                        oi=tuple(o for o in data.oi if lo <= o.ts < hi),
                        funding=tuple(f for f in data.funding if lo <= f.ts < hi),
                        books=tuple(b for b in data.books if lo <= b.ts < hi),
                        origin="gap-replay",
                    )
                )
        emitted = self._accept(recovered)
        self.recoveries.append(
            GapRecovery(
                start_ms=start_ms,
                end_ms=end_ms,
                resolution_s=self._resolution_s,
                bars_expected=bars_expected,
                bars_recovered=len(emitted),
            )
        )
        return emitted

    # -- cursor / dedup --------------------------------------------------------

    def _accept(self, bars: list[LiveBar]) -> list[LiveBar]:
        """Keep only strictly-new bars (start past the cursor) and advance it.

        This is the feed's half of the no-double-fill guarantee: a bar is emitted
        to the engine exactly once, so overlapping gap data or a replayed frame
        cannot re-drive a bar. The engine's ``client_order_id`` ledger (§6.2) is
        the execution-level half.
        """
        out: list[LiveBar] = []
        for bar in bars:
            start = bar.candle.ts
            if self._last_emitted_bar_start is not None and start <= self._last_emitted_bar_start:
                continue
            self._last_emitted_bar_start = start
            out.append(bar)
        return out
