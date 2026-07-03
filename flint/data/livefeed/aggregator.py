"""Bar aggregation from the normalized WS record stream (§6.7).

The LiveFeed does not re-parse Hyperliquid frames — it feeds this aggregator the
records produced by the **shared** ``data.normalize`` parsers (the same ones the
recorders and the S3 backfiller call). Trade prints drive the OHLCV candle; the
``activeAssetCtx`` snapshot drives the per-bar mark / open-interest / (predicted)
funding sidecars; ``l2Book`` frames attach the depth snapshots that lift fills to
Tier B. A bar closes the moment a venue event ts crosses its boundary.

Two invariants are load-bearing (§6.7):

* **A candle is never fabricated.** A bar with no trade prints produces no
  ``LiveBar`` at all — OHLC is only ever real trade prices (D26). Missing bars
  surface as *absent*, never as a forward-filled flat candle.
* **The close is the crossing, not the wall clock.** ``_roll`` closes the open
  bar only when a record whose ts lands in a *later* bar arrives.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flint.core.models import (
    Candle,
    FundingRate,
    MarkSnapshot,
    OrderbookSnapshot,
)
from flint.core.time import bar_start
from flint.data.normalize import AssetContext, TradePrint
from flint.engine.context import OpenInterestSnapshot


@dataclass(frozen=True)
class LiveBar:
    """One closed bar plus every sidecar observed within it (§6.7).

    ``origin`` is ``"live"`` for a bar built from the WS stream and
    ``"gap-replay"`` for one reconstructed from the lake on reconnect — the
    monitor uses it to show which bars were recovered, and it never changes how
    the engine processes the bar (both go through the identical loop).
    """

    candle: Candle
    marks: tuple[MarkSnapshot, ...] = ()
    oi: tuple[OpenInterestSnapshot, ...] = ()
    funding: tuple[FundingRate, ...] = ()
    books: tuple[OrderbookSnapshot, ...] = ()
    trades: tuple[TradePrint, ...] = ()
    origin: str = "live"


@dataclass
class _OpenBar:
    """Mutable accumulator for the currently-open bar."""

    start: int
    has_trade: bool = False
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    trades: list[TradePrint] = field(default_factory=list)
    books: list[OrderbookSnapshot] = field(default_factory=list)
    marks: list[MarkSnapshot] = field(default_factory=list)
    oi: list[OpenInterestSnapshot] = field(default_factory=list)
    funding: list[FundingRate] = field(default_factory=list)


class CandleAggregator:
    """Aggregates normalized records into closed :class:`LiveBar`s (§6.7).

    One instance per ``(venue, market)`` for one connection: the LiveFeed builds
    a fresh aggregator per connect so a partial bar left open at a disconnect is
    discarded (its authoritative version comes back through gap replay).
    """

    def __init__(
        self,
        market: str,
        venue: str,
        resolution_s: int,
        *,
        funding_interval_s: int = 3600,
    ) -> None:
        if resolution_s <= 0:
            raise ValueError(f"resolution_s must be positive, got {resolution_s}")
        self._market = market
        self._venue = venue
        self._resolution_s = resolution_s
        self._funding_interval_s = funding_interval_s
        self._bar: _OpenBar | None = None

    # -- ingest one normalized record; return any bar(s) it closed -------------

    def add_trade(self, p: TradePrint) -> list[LiveBar]:
        closed = self._roll(p.ts)
        bar = self._bar
        assert bar is not None
        if not bar.has_trade:
            bar.open = bar.high = bar.low = p.price
            bar.has_trade = True
        else:
            bar.high = max(bar.high, p.price)
            bar.low = min(bar.low, p.price)
        bar.close = p.price
        bar.volume += p.size
        bar.trades.append(p)
        return closed

    def add_book(self, book: OrderbookSnapshot) -> list[LiveBar]:
        closed = self._roll(book.ts)
        assert self._bar is not None
        self._bar.books.append(book)
        return closed

    def add_context(self, ctx: AssetContext) -> list[LiveBar]:
        """Fold an ``activeAssetCtx`` snapshot into mark / OI / predicted-funding.

        ``ctx.ts`` is the session clock (the latest venue event ts) the LiveFeed
        stamped on — a recorded observation time, never wall clock (§6.7). The
        funding row is the last *predicted* hourly rate, which is what
        ``ctx.funding_rate`` exposes to the strategy (§8.2); it is not a
        settlement (only ``final`` rows settle, §6.4).
        """
        closed = self._roll(ctx.ts)
        bar = self._bar
        assert bar is not None
        bar.marks.append(
            MarkSnapshot(
                market=ctx.market,
                ts=ctx.ts,
                mark_price=ctx.mark_price,
                index_price=ctx.index_price,
                venue=ctx.venue,
            )
        )
        bar.oi.append(
            OpenInterestSnapshot(
                market=ctx.market,
                ts=ctx.ts,
                open_interest=ctx.open_interest,
                venue=ctx.venue,
            )
        )
        bar.funding.append(
            FundingRate(
                ts=ctx.ts,
                rate_hourly=ctx.funding_hourly,
                interval_s=self._funding_interval_s,
                price_basis="oracle",
                rate_type="predicted",
                venue=ctx.venue,
                market=ctx.market,
            )
        )
        return closed

    # -- bar lifecycle ---------------------------------------------------------

    def _roll(self, ts: int) -> list[LiveBar]:
        """Open the first bar, or close the current one when ``ts`` crosses on."""
        b = bar_start(ts, self._resolution_s)
        if self._bar is None:
            self._bar = _OpenBar(start=b)
            return []
        if b <= self._bar.start:
            # Same bar, or a late/reordered frame clamped into the open bar (the
            # forward-only clock guarantees a closed bar is never reopened).
            return []
        closed = self._finish()
        self._bar = _OpenBar(start=b)
        return [closed] if closed is not None else []

    def flush(self) -> list[LiveBar]:
        """Close the final open bar (graceful session end, not a disconnect)."""
        closed = self._finish()
        self._bar = None
        return [closed] if closed is not None else []

    def _finish(self) -> LiveBar | None:
        bar = self._bar
        if bar is None or not bar.has_trade:
            return None  # no trades -> no candle; OHLC is never invented (D26)
        candle = Candle(
            ts=bar.start,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            market=self._market,
            resolution_s=self._resolution_s,
            venue=self._venue,
        )
        return LiveBar(
            candle=candle,
            marks=tuple(bar.marks),
            oi=tuple(bar.oi),
            funding=tuple(bar.funding),
            books=tuple(bar.books),
            trades=tuple(bar.trades),
            origin="live",
        )
