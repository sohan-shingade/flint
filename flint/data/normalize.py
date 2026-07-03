"""Shared market-data normalization — one parser per HL message shape (§6.7, §9.1).

This module is the **single implementation** of "parse Hyperliquid's ``l2Book`` /
``trades`` / ``activeAssetCtx`` message" (§6.7's explicit rule: one parser, not
two). Three consumers import it:

* the **WS recorders** (``ingest/recorders/``) — normalize a live frame, then
  persist it keyed ``(venue, market, ts)``;
* the **S3 backfiller** (``ingest/backfillers/hyperliquid.py``) — the archive's
  ``l2Book`` / ``asset_ctxs`` records are the same shapes, so it calls the same
  parsers instead of maintaining its own;
* the **Phase-5 LiveFeed** (``data/livefeed/``, not yet built) — subscribes the
  same venue WebSockets and emits normalized candles/marks/funding/book updates
  to the engine using exactly these functions and record types.

The canonical Arrow schemas for each persisted kind live here too, so a recorder
frame and a backfilled archive row for the same ``(venue, market, kind)`` are
byte-compatible in the store and merge without a schema clash.

Normalization never invents data (D26): a field the venue did not send is not
fabricated. ``activeAssetCtx`` frames carry no event timestamp, so the caller
supplies the receipt time (``recv_ts``) — a recorded observation time, not a
synthesized market value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from flint.core.models import OrderbookSnapshot

VENUE_HYPERLIQUID = "hyperliquid"


def market_of_coin(coin: str) -> str:
    """Map an HL coin id to a Flint market symbol: ``SOL`` -> ``SOL-PERP``."""
    return coin if coin.endswith("-PERP") else f"{coin}-PERP"


# --- normalized data-layer records ------------------------------------------
# OrderbookSnapshot is a frozen core model and is reused directly. Trade prints
# and asset-context snapshots have no core model (they are data-layer capture
# artifacts, not engine domain types), so they live here.


@dataclass(frozen=True)
class TradePrint:
    """One executed trade — the now-or-never capture that lifts fills to Tier A."""

    ts: int
    market: str
    venue: str
    price: float
    size: float
    side: str  # "buy" (taker lifted the ask) or "sell" (taker hit the bid)
    trade_id: int


@dataclass(frozen=True)
class QuoteTick:
    """One top-of-book change (HL ``bbo`` frame) — the Tier-2 QUOTES row.

    HL sends a ``bbo`` frame only when the best bid/offer changes on a block;
    either side may be ``null`` when that side of the book is empty, so the
    price/size fields are ``None`` then — an absent quote is recorded as
    absent, never fabricated (D26).
    """

    ts: int
    market: str
    venue: str
    bid_px: float | None
    bid_sz: float | None
    ask_px: float | None
    ask_sz: float | None


@dataclass(frozen=True)
class FundingObservation:
    """One FUNDING row as captured — matches ``FUNDING_SCHEMA`` field-for-field.

    The recorder emits these for the **predicted** rate carried by
    ``activeAssetCtx`` (``rate_type="predicted"``, ``ts`` = capture time,
    ``settlement_ts`` = the upcoming hour boundary). Final settled rows come
    from the REST/vendor ingesters, not from here.
    """

    ts: int
    market: str
    venue: str
    rate_hourly: float
    interval_s: int
    price_basis: str
    rate_type: str
    settlement_ts: int | None = None


@dataclass(frozen=True)
class AssetContext:
    """A perp's live context snapshot: OI + the three-price inputs + funding.

    Bundles what HL's ``activeAssetCtx`` (live) and ``asset_ctxs`` (archive) both
    carry. Persisted as an ``OI`` row (mark/oracle folded in per the 2.4 ruling);
    ``mid_price`` is retained on the record but not in the OI Arrow schema.
    """

    ts: int
    market: str
    venue: str
    open_interest: float
    mark_price: float
    index_price: float  # the oracle/index price
    funding_hourly: float
    mid_price: float = 0.0


# --- canonical Arrow schemas (must match across recorder + backfiller) -------

_LEVEL_TYPE = pa.list_(pa.float64())  # one [px, sz] pair

DEPTH_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("market", pa.string()),
        ("venue", pa.string()),
        ("bids", pa.list_(_LEVEL_TYPE)),  # best-first [[px, sz], ...]
        ("asks", pa.list_(_LEVEL_TYPE)),
    ]
)

OI_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("market", pa.string()),
        ("venue", pa.string()),
        ("oi", pa.float64()),
        ("mark_price", pa.float64()),
        ("index_price", pa.float64()),
        ("funding_hourly", pa.float64()),
    ]
)

# Funding is normalized to a comparable **hourly** rate regardless of the venue's
# native settlement cadence (HL hourly, CEX 8h/4h/1h), keeping the native cadence
# in ``interval_s`` and how the rate was priced/settled in ``price_basis`` /
# ``rate_type`` (§10). This schema is the single source shared by the HL REST
# provider (2.4) and the CEX/CCXT funding ingestion (2.6) so cross-venue funding
# rows merge in the store without a schema clash.
FUNDING_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("rate_hourly", pa.float64()),
        ("interval_s", pa.int64()),
        ("price_basis", pa.string()),
        ("rate_type", pa.string()),
        ("venue", pa.string()),
        ("market", pa.string()),
        # When the rate settles (or settled): a final row settles *at* its own
        # ``ts``; a predicted row carries the upcoming settlement time when the
        # source supplies one, else null. Nullable — never fabricated (D26).
        # Appended last so lake-v1 files migrated on read (store/layout.py)
        # match this exact column order.
        ("settlement_ts", pa.int64()),
    ]
)

TRADES_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("market", pa.string()),
        ("venue", pa.string()),
        ("price", pa.float64()),
        ("size", pa.float64()),
        ("side", pa.string()),
        ("trade_id", pa.int64()),
    ]
)

# Top-of-book quotes (Tier-2 tick lane): one row per BBO change.
QUOTES_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("market", pa.string()),
        ("venue", pa.string()),
        ("bid_px", pa.float64()),
        ("bid_sz", pa.float64()),
        ("ask_px", pa.float64()),
        ("ask_sz", pa.float64()),
    ]
)

# L2 incremental book updates (Tier-3 book replay): flat per-level rows matching
# Tardis ``incremental_book_L2``. ``sz == 0`` deletes the level; ``seq`` breaks
# ``ts`` ties (the tie-break column of the total-ordering guarantee);
# ``local_ts`` is the capture/receipt time; ``is_snapshot`` marks rows that
# belong to a full-book snapshot rather than an incremental update.
BOOK_DELTA_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("local_ts", pa.int64()),
        ("seq", pa.int64()),
        ("market", pa.string()),
        ("venue", pa.string()),
        ("side", pa.string()),  # "bid" or "ask"
        ("px", pa.float64()),
        ("sz", pa.float64()),
        ("is_snapshot", pa.bool_()),
    ]
)


# --- parsers: raw HL payload -> normalized record ---------------------------


def _side_of(raw: str) -> str:
    """HL trade side: ``B`` = taker bought (lifted ask), ``A`` = taker sold."""
    return "buy" if raw == "B" else "sell"


def normalize_trades(data: Any, market: str | None = None) -> list[TradePrint]:
    """Normalize an HL ``trades`` frame (a list of trade dicts) to prints.

    Each dict is ``{coin, side, px, sz, time, hash, tid}``; ``market`` overrides
    the per-trade coin mapping when the caller already knows the subscription.
    """
    prints: list[TradePrint] = []
    for t in data or []:
        mkt = market or market_of_coin(str(t["coin"]))
        prints.append(
            TradePrint(
                ts=int(t["time"]),
                market=mkt,
                venue=VENUE_HYPERLIQUID,
                price=float(t["px"]),
                size=float(t["sz"]),
                side=_side_of(str(t.get("side", ""))),
                trade_id=int(t["tid"]),
            )
        )
    return prints


def _levels(side: Any) -> tuple[tuple[float, float], ...]:
    out: list[tuple[float, float]] = []
    for lvl in side or []:
        out.append((float(lvl["px"]), float(lvl["sz"])))
    return tuple(out)


def normalize_l2book(data: Any, market: str | None = None) -> OrderbookSnapshot:
    """Normalize an HL ``l2Book`` frame to a core ``OrderbookSnapshot``.

    ``data`` is ``{coin, time, levels: [[bids], [asks]]}`` where each level is
    ``{px, sz, n}``. Identical shape in the live WS frame and the S3 archive line.
    """
    levels = data.get("levels") or [[], []]
    return OrderbookSnapshot(
        market=market or market_of_coin(str(data["coin"])),
        ts=int(data["time"]),
        bids=_levels(levels[0]) if len(levels) > 0 else (),
        asks=_levels(levels[1]) if len(levels) > 1 else (),
        venue=VENUE_HYPERLIQUID,
    )


def _quote_side(level: Any) -> tuple[float | None, float | None]:
    """One ``bbo`` side: a ``{px, sz, n}`` level, or ``None`` for an empty side."""
    if level is None:
        return None, None
    return float(level["px"]), float(level["sz"])


def normalize_bbo(data: Any, market: str | None = None) -> QuoteTick:
    """Normalize an HL ``bbo`` frame to a :class:`QuoteTick`.

    ``data`` is ``{coin, time, bbo: [bid_level | null, ask_level | null]}``
    where each level is ``{px, sz, n}`` (HL sends the frame only when the BBO
    changes on a block). A ``null`` side stays ``None`` — never fabricated.
    """
    levels = data.get("bbo") or [None, None]
    bid_px, bid_sz = _quote_side(levels[0] if len(levels) > 0 else None)
    ask_px, ask_sz = _quote_side(levels[1] if len(levels) > 1 else None)
    return QuoteTick(
        ts=int(data["time"]),
        market=market or market_of_coin(str(data["coin"])),
        venue=VENUE_HYPERLIQUID,
        bid_px=bid_px,
        bid_sz=bid_sz,
        ask_px=ask_px,
        ask_sz=ask_sz,
    )


def normalize_active_asset_ctx(
    data: Any, market: str | None = None, *, recv_ts: int
) -> AssetContext:
    """Normalize an HL ``activeAssetCtx`` frame (``{coin, ctx}``) to a context.

    The frame carries no event timestamp, so ``recv_ts`` (the recorder's receipt
    time) stamps the observation — a recorded fact, never a synthesized value.
    """
    ctx = data.get("ctx") or {}
    return AssetContext(
        ts=recv_ts,
        market=market or market_of_coin(str(data["coin"])),
        venue=VENUE_HYPERLIQUID,
        open_interest=float(ctx.get("openInterest", 0.0)),
        mark_price=float(ctx.get("markPx", 0.0)),
        index_price=float(ctx.get("oraclePx", 0.0)),
        funding_hourly=float(ctx.get("funding", 0.0)),
        mid_price=float(ctx.get("midPx", 0.0)),
    )


def normalize_asset_ctx_record(rec: Any, market: str | None = None) -> AssetContext:
    """Normalize a flat S3 ``asset_ctxs`` record (carries its own ``time``)."""
    return AssetContext(
        ts=int(rec["time"]),
        market=market or market_of_coin(str(rec["coin"])),
        venue=VENUE_HYPERLIQUID,
        open_interest=float(rec.get("openInterest", 0.0)),
        mark_price=float(rec.get("markPx", 0.0)),
        index_price=float(rec.get("oraclePx", 0.0)),
        funding_hourly=float(rec.get("funding", 0.0)),
        mid_price=float(rec.get("midPx", 0.0)),
    )


# --- normalized record -> Arrow (canonical, kind-keyed) ---------------------


def trades_to_arrow(prints: list[TradePrint]) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "ts": p.ts,
                "market": p.market,
                "venue": p.venue,
                "price": p.price,
                "size": p.size,
                "side": p.side,
                "trade_id": p.trade_id,
            }
            for p in prints
        ],
        schema=TRADES_SCHEMA,
    )


def books_to_arrow(books: list[OrderbookSnapshot]) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "ts": b.ts,
                "market": b.market,
                "venue": b.venue,
                "bids": [[px, sz] for px, sz in b.bids],
                "asks": [[px, sz] for px, sz in b.asks],
            }
            for b in books
        ],
        schema=DEPTH_SCHEMA,
    )


def quotes_to_arrow(quotes: list[QuoteTick]) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "ts": q.ts,
                "market": q.market,
                "venue": q.venue,
                "bid_px": q.bid_px,
                "bid_sz": q.bid_sz,
                "ask_px": q.ask_px,
                "ask_sz": q.ask_sz,
            }
            for q in quotes
        ],
        schema=QUOTES_SCHEMA,
    )


def fundings_to_arrow(rows: list[FundingObservation]) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "ts": f.ts,
                "rate_hourly": f.rate_hourly,
                "interval_s": f.interval_s,
                "price_basis": f.price_basis,
                "rate_type": f.rate_type,
                "venue": f.venue,
                "market": f.market,
                "settlement_ts": f.settlement_ts,
            }
            for f in rows
        ],
        schema=FUNDING_SCHEMA,
    )


def contexts_to_arrow(contexts: list[AssetContext]) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "ts": c.ts,
                "market": c.market,
                "venue": c.venue,
                "oi": c.open_interest,
                "mark_price": c.mark_price,
                "index_price": c.index_price,
                "funding_hourly": c.funding_hourly,
            }
            for c in contexts
        ],
        schema=OI_SCHEMA,
    )
