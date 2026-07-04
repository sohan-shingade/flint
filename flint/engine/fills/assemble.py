"""Fill-context assembly — the one builder the bar lane uses (§6.0, §6.3, D29).

Turning an ``Order`` plus a bar plus the recorded microstructure into the
:class:`FillContext` a :class:`FillModel` consumes is glue, not economics — but it
is glue that *decides which tier a fill achieves* (book as-of the effective time,
staleness, oracle-as-of, queue-ahead) and draws the seeded latency. Extracted from
the legacy loop in N1 (§6.0, D29) as the single context builder; since N10 the
Nautilus bar-lane shim is its only caller (the legacy loop was deleted). It draws
the rng in one place (one latency draw per fill), so a run's determinism is fixed
here — and reproduces the frozen parity goldens the legacy engine recorded.

Pure: no engine state, no I/O. Every input the legacy ``_fill_ctx`` read off
``self`` (the venue spec, latency model, rng, and the recorded book/trade/mark
fixtures) is passed in explicitly, so the same call from either engine yields the
same :class:`FillContext`.
"""

from __future__ import annotations

from random import Random

from flint.core.models import Candle, MarkSnapshot, Order, OrderbookSnapshot, OrderType, Side
from flint.core.time import bar_end, last_before
from flint.venues import VenueSpec

from .base import FillContext, TradePrint
from .latency import LatencyModel


def assemble_fill_context(
    order: Order,
    *,
    reference_price: float,
    candle: Candle,
    market_marks: list[MarkSnapshot],
    spec: VenueSpec,
    latency: LatencyModel,
    rng: Random,
    books: dict[str, list[OrderbookSnapshot]],
    trades: dict[str, list[TradePrint]],
) -> FillContext:
    """Assemble the :class:`FillContext` at the order's effective time (§6.3).

    Effective time = ``candle.ts`` (submit) + one seeded latency draw, so the fill
    sees the market as it had moved, not as the strategy saw it. The book and oracle
    are selected as-of that time; with no recorded book the model runs Tier C. The
    body is lifted verbatim from the legacy engine's ``_fill_ctx`` (§6.3) at the N1
    extraction, so latency is drawn once, in the same place, per fill.
    """
    submit_ts = candle.ts
    effective_ts = submit_ts + int(latency.draw_ms(rng))
    book, stale = _book_as_of(books, order.market, effective_ts, spec)
    end = bar_end(candle.ts, candle.resolution_s)
    return FillContext(
        reference_price=reference_price,
        ts=submit_ts,
        effective_ts=effective_ts,
        taker_fee_rate=spec.taker_fee_rate,
        maker_fee_rate=spec.maker_fee_rate,
        book=book,
        trades=_trades_in_bar(trades, order.market, candle.ts, end),
        oracle_price=_oracle_as_of(market_marks, effective_ts, candle.close),
        oracle_band_bps=spec.oracle_band_bps,
        stale_book=stale,
        queue_ahead=_queue_ahead(book, order),
        bar_dollar_volume=candle.volume * candle.open,
        half_spread_bps=spec.default_half_spread_bps,
        impact_k=spec.tier_c_impact_k,
        price_sig_figs=spec.price_sig_figs,
    )


def _book_as_of(
    books: dict[str, list[OrderbookSnapshot]],
    market: str,
    effective_ts: int,
    spec: VenueSpec,
) -> tuple[OrderbookSnapshot | None, bool]:
    """The book at-or-before ``effective_ts`` and whether it is stale (§6.3)."""
    snap = last_before(books.get(market, []), effective_ts + 1, key=lambda b: b.ts)
    if snap is None:
        return None, False
    stale = (effective_ts - snap.ts) > spec.book_staleness_s * 1000
    return snap, stale


def _trades_in_bar(
    trades: dict[str, list[TradePrint]], market: str, start: int, end: int
) -> tuple[TradePrint, ...]:
    return tuple(t for t in trades.get(market, []) if start <= t.ts < end)


def _oracle_as_of(
    market_marks: list[MarkSnapshot], effective_ts: int, fallback: float
) -> float:
    snap = last_before(market_marks, effective_ts + 1, key=lambda m: m.ts)
    return snap.index_price if snap is not None else fallback


def _queue_ahead(book: OrderbookSnapshot | None, order: Order) -> float:
    """Resting size ahead of a maker limit at its price (Tier-A/B queue)."""
    if book is None or order.type is not OrderType.LIMIT:
        return 0.0
    levels = book.bids if order.side is Side.LONG else book.asks
    return sum(size for price, size in levels if price == order.price)


__all__ = ["assemble_fill_context"]
