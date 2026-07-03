"""Unit tests for core/models (§5, §8.1) — field shapes, properties, numeric policy."""

from __future__ import annotations

import dataclasses

import pytest

from flint.core.models import (
    BorrowSnapshot,
    Candle,
    Fill,
    FundingRate,
    MarkSnapshot,
    Order,
    OrderbookSnapshot,
    OrderType,
    Position,
    Side,
    Signal,
    TimeInForce,
)


# ── enums ────────────────────────────────────────────────────────────────────


def test_side_sign_and_opposite():
    assert Side.LONG.sign == 1
    assert Side.SHORT.sign == -1
    assert Side.LONG.opposite is Side.SHORT
    assert Side.SHORT.opposite is Side.LONG


def test_enums_are_str_valued():
    # StrEnum members compare equal to their wire strings (serialize for free).
    assert Side.LONG == "long"
    assert OrderType.MARKET == "market"
    assert OrderType.TAKE_PROFIT == "take_profit"
    assert TimeInForce.IOC == "ioc"
    assert TimeInForce.GTC == "gtc"
    assert TimeInForce.FOK == "fok"


# ── MarkSnapshot.basis_bps ───────────────────────────────────────────────────


def test_basis_bps_premium_and_discount():
    premium = MarkSnapshot("SOL-PERP", 1, mark_price=101.0, index_price=100.0, venue="hl")
    assert premium.basis_bps == pytest.approx(100.0)  # +1% = 100 bps
    discount = MarkSnapshot("SOL-PERP", 1, mark_price=99.5, index_price=100.0, venue="hl")
    assert discount.basis_bps == pytest.approx(-50.0)


def test_basis_bps_guards_nonpositive_index():
    snap = MarkSnapshot("SOL-PERP", 1, mark_price=100.0, index_price=0.0, venue="hl")
    assert snap.basis_bps == 0.0


# ── frozen-ness + timestamps are int (numeric policy) ────────────────────────


@pytest.mark.parametrize(
    "obj",
    [
        Candle(1, 1.0, 2.0, 0.5, 1.5, 10.0, "SOL-PERP", 3600, "hl"),
        MarkSnapshot("SOL-PERP", 1, 100.0, 100.0, "hl"),
        FundingRate("SOL-PERP", 1, 0.00125, 3600, "oracle", "predicted", "hl"),
        OrderbookSnapshot("SOL-PERP", 1, ((100.0, 5.0),), ((101.0, 5.0),), "hl"),
        BorrowSnapshot("SOL-PERP", 1, "jupiter", 0.001, 0.8, 1000.0, 800.0),
        Fill("SOL-PERP", Side.LONG, 100.0, 1.0, 0.05, 1, "cid"),
        Position("SOL-PERP", "hl", Side.LONG, 1.0, 100.0, "cross"),
        Signal.long("SOL-PERP", "hl", size_usd=100.0),
    ],
)
def test_market_and_record_models_are_frozen(obj):
    with pytest.raises(dataclasses.FrozenInstanceError):
        obj.market = "MUTATED"  # type: ignore[misc]


def test_candle_ts_is_int_and_bar_start_convention():
    c = Candle(1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 10.0, "SOL-PERP", 3600, "hl")
    assert isinstance(c.ts, int)  # unix ms, bar START (§5)


# ── Order is mutable with correct defaults; Fill defaults ────────────────────


def test_order_is_mutable_with_defaults():
    o = Order("SOL-PERP", Side.LONG, OrderType.MARKET, size=2.0)
    assert o.tif is TimeInForce.IOC
    assert o.margin_mode == "cross"
    assert o.reduce_only is False
    o.reduce_only = True  # Order advances through a state machine (§6.2) — mutable
    assert o.reduce_only is True


def test_fill_defaults_record_fidelity():
    f = Fill("SOL-PERP", Side.SHORT, 100.0, 1.0, 0.05, 1, "cid")
    assert f.liquidity == "taker"
    assert f.fidelity_tier == "C"
    assert f.is_partial is False


# ── Position math ────────────────────────────────────────────────────────────


def test_position_pnl_long_and_short():
    long = Position("SOL-PERP", "hl", Side.LONG, size=2.0, entry_price=100.0, margin_mode="cross")
    assert long.signed_size == 2.0
    assert long.notional(110.0) == pytest.approx(220.0)
    assert long.unrealized_pnl(110.0) == pytest.approx(20.0)  # (110-100)*2
    assert long.unrealized_pnl(90.0) == pytest.approx(-20.0)

    short = Position("SOL-PERP", "hl", Side.SHORT, size=2.0, entry_price=100.0, margin_mode="cross")
    assert short.signed_size == -2.0
    assert short.unrealized_pnl(90.0) == pytest.approx(20.0)  # short profits when price falls
    assert short.unrealized_pnl(110.0) == pytest.approx(-20.0)


# ── Signal constructors + validation ─────────────────────────────────────────


def test_signal_constructors():
    lng = Signal.long("SOL-PERP", "hl", size_usd=100.0)
    assert lng.action == "long" and lng.size_usd == 100.0 and not lng.is_close
    assert lng.tif is TimeInForce.IOC  # market default

    sht = Signal.short("SOL-PERP", "hl", size=1.5, limit_price=99.0, tif=TimeInForce.GTC)
    assert sht.action == "short" and sht.size == 1.5 and sht.limit_price == 99.0
    assert sht.tif is TimeInForce.GTC

    cls = Signal.close("SOL-PERP", "hl")
    assert cls.action == "close" and cls.is_close
    assert cls.size_usd == 0.0 and cls.size == 0.0


def test_signal_rejects_unknown_action():
    with pytest.raises(ValueError, match="action"):
        Signal("SOL-PERP", "hl", action="sideways")
