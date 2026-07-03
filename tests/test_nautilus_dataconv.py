"""Flint models → Nautilus objects — hand-authored conversion fixtures (§A3, D26).

Gated on the ``nautilus`` extra: the whole module skips when ``nautilus_trader`` is
absent, following the repo's optional-dependency test pattern (``importorskip``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("nautilus_trader")

from flint.core.models import Candle, FundingRate, MarkSnapshot, OrderbookSnapshot  # noqa: E402
from flint.engine.fills import TradePrint  # noqa: E402
from flint.engine.nautilus import dataconv, timeconv  # noqa: E402
from flint.engine.nautilus._compat import (  # noqa: E402
    BookAction,
    IndexPriceUpdate,
    InstrumentId,
    MarkPriceUpdate,
    OrderSide,
    Symbol,
    Venue,
)
from flint.venues import HYPERLIQUID  # noqa: E402

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
# An hour-aligned base so the derived funding settlement boundary is exact.
T0 = 1_699_999_200_000


def _iid() -> InstrumentId:
    return InstrumentId(Symbol(MARKET), Venue(VENUE.upper()))


# --- bars --------------------------------------------------------------------


def test_candle_to_bar_labels_at_close_and_preserves_ohlcv():
    candle = Candle(
        ts=T0, open=100.0, high=102.5, low=99.5, close=101.0, volume=12.0,
        market=MARKET, resolution_s=HOUR_S, venue=VENUE,
    )
    bar_type = dataconv.bar_type_for(_iid(), HOUR_S)
    bar = dataconv.candle_to_bar(candle, bar_type, price_precision=2, size_precision=2)
    assert bar.ts_event == timeconv.candle_start_ms_to_bar_close_ns(T0, HOUR_S)
    assert bar.ts_event == bar.ts_init
    assert (str(bar.open), str(bar.high), str(bar.low), str(bar.close)) == (
        "100.00", "102.50", "99.50", "101.00",
    )
    assert str(bar.volume) == "12.00"


def test_bar_type_encodes_resolution_as_external_last():
    bar_type = dataconv.bar_type_for(_iid(), HOUR_S)
    assert str(bar_type).endswith("-1-HOUR-LAST-EXTERNAL")


@pytest.mark.parametrize(
    "resolution_s,suffix",
    [
        (3600, "1-HOUR-LAST-EXTERNAL"),
        (4 * 3600, "4-HOUR-LAST-EXTERNAL"),
        (60, "1-MINUTE-LAST-EXTERNAL"),
        (300, "5-MINUTE-LAST-EXTERNAL"),
        (86_400, "1-DAY-LAST-EXTERNAL"),
        (1, "1-SECOND-LAST-EXTERNAL"),
    ],
)
def test_resolution_to_bar_spec_picks_the_coarsest_unit(resolution_s, suffix):
    bar_type = dataconv.bar_type_for(_iid(), resolution_s)
    assert str(bar_type).endswith(suffix)


# --- marks -------------------------------------------------------------------


def test_mark_snapshot_splits_into_mark_and_index_updates():
    mark = MarkSnapshot(market=MARKET, ts=T0, mark_price=101.0, index_price=100.5, venue=VENUE)
    updates = dataconv.mark_to_updates(mark, _iid(), price_precision=2)
    assert [type(u) for u in updates] == [MarkPriceUpdate, IndexPriceUpdate]
    assert all(u.ts_event == timeconv.ms_to_ns(T0) for u in updates)
    assert str(updates[0].value) == "101.00"
    assert str(updates[1].value) == "100.50"


def test_mark_without_index_emits_only_a_mark_update():
    mark = MarkSnapshot(market=MARKET, ts=T0, mark_price=101.0, index_price=0.0, venue=VENUE)
    updates = dataconv.mark_to_updates(mark, _iid(), price_precision=2)
    assert [type(u) for u in updates] == [MarkPriceUpdate]  # index never invented (D26)


# --- trades ------------------------------------------------------------------


def test_trade_print_to_tick_synthesizes_id_and_no_aggressor():
    tp = TradePrint(price=100.25, size=3.0, ts=T0 + 5000)
    tick = dataconv.trade_to_tick(tp, _iid(), price_precision=2, size_precision=2, seq=7)
    assert str(tick.price) == "100.25"
    assert str(tick.size) == "3.00"
    assert tick.ts_event == timeconv.ms_to_ns(T0 + 5000)
    assert str(tick.trade_id) == f"{T0 + 5000}-7"


# --- funding -----------------------------------------------------------------


def test_predicted_funding_becomes_flint_funding_rate_losslessly():
    fr = FundingRate(
        market=MARKET, ts=T0 + 12 * 60 * 1000, rate_hourly=0.0011, interval_s=HOUR_S,
        price_basis="oracle", rate_type="predicted", venue=VENUE,
    )
    data = dataconv.predicted_funding_to_data(fr, _iid())
    assert data is not None
    assert data.rate_hourly == 0.0011
    assert data.interval_s == HOUR_S
    assert data.price_basis == "oracle"
    assert data.rate_type == "predicted"
    assert data.ts_event == timeconv.ms_to_ns(fr.ts)
    # settlement_ts derived as the end of the interval containing the publish ts.
    assert data.settlement_ts == T0 + HOUR_S * 1000


def test_final_funding_never_reaches_the_stream():
    fr = FundingRate(
        market=MARKET, ts=T0, rate_hourly=0.001, interval_s=HOUR_S,
        price_basis="oracle", rate_type="final", venue=VENUE,
    )
    # Final rows settle through the funding module (N4), never the strategy stream.
    assert dataconv.predicted_funding_to_data(fr, _iid()) is None


# --- books --------------------------------------------------------------------


def test_orderbook_snapshot_becomes_clear_then_one_add_per_level():
    # A recorded snapshot rebuilds the whole L2 book (N8): a CLEAR wipes prior state,
    # then one best-first ADD per bid/ask level reconstructs it at the snapshot ts.
    book = OrderbookSnapshot(
        market=MARKET,
        ts=T0,
        bids=((99.9, 5.0), (99.8, 7.0)),
        asks=((100.0, 3.0),),
        venue=VENUE,
    )
    deltas = dataconv.orderbook_to_deltas(
        book, _iid(), price_precision=2, size_precision=3
    )
    rows = deltas.deltas
    assert rows[0].action == BookAction.CLEAR
    assert [r.action for r in rows[1:]] == [BookAction.ADD] * 3
    reconstructed = [
        (r.order.side, float(r.order.price), float(r.order.size)) for r in rows[1:]
    ]
    assert reconstructed == [
        (OrderSide.BUY, 99.9, 5.0),
        (OrderSide.BUY, 99.8, 7.0),
        (OrderSide.SELL, 100.0, 3.0),
    ]
    # Every row is stamped at the snapshot ts (unix-ms → ns) and shares the book's ts.
    assert {r.ts_event for r in rows} == {T0 * 1_000_000}


# --- instruments -------------------------------------------------------------


def test_build_instrument_from_venue_spec():
    instrument = dataconv.build_instrument(HYPERLIQUID, MARKET, VENUE)
    assert instrument.id.symbol.value == MARKET
    assert instrument.id.venue.value == "HYPERLIQUID"
    assert instrument.base_currency.code == "SOL"
    assert instrument.quote_currency.code == "USDC"
    assert instrument.settlement_currency.code == "USDC"
    assert instrument.size_precision == HYPERLIQUID.size_decimals
    assert str(instrument.maker_fee) == str(HYPERLIQUID.maker_fee_rate)
    assert str(instrument.taker_fee) == str(HYPERLIQUID.taker_fee_rate)
