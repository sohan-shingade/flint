"""Shared HL normalization: trades / l2Book / activeAssetCtx parsers (2.5, §6.7).

Fixtures are hand-authored fragments matching Hyperliquid's documented WS/archive
message shapes (D26 — recorded fragments / unit inputs, never generated data).
"""

from __future__ import annotations

from flint.core.models import OrderbookSnapshot
from flint.data.normalize import (
    DEPTH_SCHEMA,
    OI_SCHEMA,
    TRADES_SCHEMA,
    AssetContext,
    TradePrint,
    books_to_arrow,
    contexts_to_arrow,
    market_of_coin,
    normalize_active_asset_ctx,
    normalize_asset_ctx_record,
    normalize_l2book,
    normalize_trades,
    trades_to_arrow,
)


def test_market_of_coin_maps_and_is_idempotent():
    assert market_of_coin("SOL") == "SOL-PERP"
    assert market_of_coin("SOL-PERP") == "SOL-PERP"


def test_normalize_trades_maps_side_and_fields():
    frame = [
        {"coin": "SOL", "side": "B", "px": "100.5", "sz": "1.2", "time": 900, "tid": 7},
        {"coin": "SOL", "side": "A", "px": "100.4", "sz": "0.5", "time": 950, "tid": 8},
    ]
    prints = normalize_trades(frame)
    assert prints == [
        TradePrint(900, "SOL-PERP", "hyperliquid", 100.5, 1.2, "buy", 7),
        TradePrint(950, "SOL-PERP", "hyperliquid", 100.4, 0.5, "sell", 8),
    ]


def test_normalize_l2book_to_core_orderbook_snapshot():
    frame = {
        "coin": "SOL",
        "time": 960,
        "levels": [
            [{"px": "100.3", "sz": "5", "n": 2}],
            [{"px": "100.5", "sz": "4", "n": 1}],
        ],
    }
    book = normalize_l2book(frame)
    assert isinstance(book, OrderbookSnapshot)
    assert book.ts == 960
    assert book.market == "SOL-PERP"
    assert book.venue == "hyperliquid"
    assert book.bids == ((100.3, 5.0),)
    assert book.asks == ((100.5, 4.0),)


def test_normalize_active_asset_ctx_stamps_recv_ts():
    # activeAssetCtx carries no event timestamp -> the receipt time stamps it.
    frame = {
        "coin": "SOL",
        "ctx": {
            "openInterest": "1000",
            "markPx": "100.45",
            "oraclePx": "100.4",
            "funding": "0.0000125",
            "midPx": "100.44",
        },
    }
    ctx = normalize_active_asset_ctx(frame, recv_ts=12345)
    assert ctx == AssetContext(
        ts=12345,
        market="SOL-PERP",
        venue="hyperliquid",
        open_interest=1000.0,
        mark_price=100.45,
        index_price=100.4,
        funding_hourly=1.25e-05,
        mid_price=100.44,
    )


def test_normalize_asset_ctx_record_uses_its_own_time():
    rec = {"time": 500, "coin": "ETH", "openInterest": "50", "markPx": "3000",
           "oraclePx": "2999", "funding": "0.00001"}
    ctx = normalize_asset_ctx_record(rec)
    assert ctx.ts == 500
    assert ctx.market == "ETH-PERP"
    assert ctx.index_price == 2999.0


def test_to_arrow_builders_use_canonical_schemas():
    assert trades_to_arrow(normalize_trades(
        [{"coin": "SOL", "side": "B", "px": "1", "sz": "1", "time": 1, "tid": 1}]
    )).schema.equals(TRADES_SCHEMA)
    assert books_to_arrow(
        [normalize_l2book({"coin": "SOL", "time": 1, "levels": [[], []]})]
    ).schema.equals(DEPTH_SCHEMA)
    assert contexts_to_arrow(
        [normalize_asset_ctx_record({"time": 1, "coin": "SOL"})]
    ).schema.equals(OI_SCHEMA)


def test_recorder_and_backfiller_depth_share_a_schema():
    # The whole point of the shared module: a live frame and an archive line for
    # the same kind produce byte-compatible tables that coexist in the store.
    from flint.data.ingest.backfillers import HyperliquidS3Backfiller  # noqa: F401

    live = books_to_arrow([normalize_l2book({"coin": "SOL", "time": 2, "levels": [[], []]})])
    archive = books_to_arrow([normalize_l2book({"coin": "SOL", "time": 1, "levels": [[], []]})])
    # concat only succeeds when schemas match exactly.
    import pyarrow as pa

    merged = pa.concat_tables([archive, live])
    assert merged.num_rows == 2
