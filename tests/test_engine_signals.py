"""Pure §8.1 signal routing — the logic N1 extracted from the loop into signals.py.

These retarget the loop's Signal→Order conversion rules onto the now-pure
:func:`route_signals` / :func:`materialize_usd_intent` (§6.0, D29), asserting the
same §8.1 contract the loop enforced: loud validation, close→reduce-only-full-size,
size_usd deferral, and lot rounding with an explicit residual. Hand-authored inputs
only (D26).
"""

from __future__ import annotations

import pytest

from flint.core.models import OrderType, Position, Side, Signal, TimeInForce
from flint.engine.signals import (
    OrderRequest,
    SignalValidationError,
    _UsdIntent,
    materialize_usd_intent,
    round_lot,
    route_signals,
)

VENUE = "hyperliquid"
MARKET = "SOL-PERP"


def _no_positions(venue: str, market: str) -> Position | None:
    return None


def _long_10() -> Position:
    return Position(
        market=MARKET,
        venue=VENUE,
        side=Side.LONG,
        size=10.0,
        entry_price=100.0,
        margin_mode="isolated",
    )


# --- validation is loud, never a merge (§8.1 rule 4/5) --------------------


def test_duplicate_signal_in_one_bar_raises():
    signals = [
        Signal.long(MARKET, VENUE, size=1.0),
        Signal.long(MARKET, VENUE, size=2.0),  # same (venue, market, action)
    ]
    with pytest.raises(SignalValidationError):
        route_signals(signals, VENUE, _no_positions)


def test_open_with_no_sizing_raises():
    signals = [Signal(market=MARKET, venue=VENUE, action="long")]
    with pytest.raises(SignalValidationError):
        route_signals(signals, VENUE, _no_positions)


def test_open_with_both_sizings_raises():
    signals = [Signal.long(MARKET, VENUE, size=1.0, size_usd=100.0)]
    with pytest.raises(SignalValidationError):
        route_signals(signals, VENUE, _no_positions)


# --- close → reduce-only full-size opposite market (§8.1 rule 3) -----------


def test_close_with_no_position_is_a_noop():
    decisions = route_signals([Signal.close(MARKET, VENUE)], VENUE, _no_positions)
    assert decisions == []


def test_close_maps_to_reduce_only_full_size_opposite_market():
    pos = _long_10()
    (decision,) = route_signals(
        [Signal.close(MARKET, VENUE)], VENUE, lambda v, m: pos
    )
    assert isinstance(decision, OrderRequest)
    assert decision.side is Side.SHORT  # opposite of the long
    assert decision.size == 10.0  # full size
    assert decision.type is OrderType.MARKET
    assert decision.tif is TimeInForce.IOC
    assert decision.reduce_only is True
    assert decision.margin_mode == "isolated"  # inherits the position's mode
    assert decision.price == 0.0


# --- size (base) routes immediately; size_usd defers (§8.1 rule 1) ---------


def test_base_market_order_routes_immediately():
    (decision,) = route_signals(
        [Signal.long(MARKET, VENUE, size=3.0)], VENUE, _no_positions
    )
    assert isinstance(decision, OrderRequest)
    assert decision.type is OrderType.MARKET
    assert decision.side is Side.LONG
    assert decision.size == 3.0
    assert decision.tif is TimeInForce.IOC
    assert decision.reduce_only is False


def test_base_limit_order_carries_type_price_and_passes_tif_through():
    # A Signal always carries an explicit tif (default IOC), so ``sig.tif or ...``
    # yields the signal's tif — exactly as the legacy loop did. The GTC fallback is
    # only reached when tif is None (see the size_usd materialization test).
    (decision,) = route_signals(
        [Signal.short(MARKET, VENUE, size=3.0, limit_price=105.0)],
        VENUE,
        _no_positions,
    )
    assert decision.type is OrderType.LIMIT
    assert decision.price == 105.0
    assert decision.tif is TimeInForce.IOC  # the signal's default, passed through


def test_size_usd_defers_as_a_usd_intent():
    (decision,) = route_signals(
        [Signal.long(MARKET, VENUE, size_usd=5000.0)], VENUE, _no_positions
    )
    assert isinstance(decision, _UsdIntent)
    assert decision.size_usd == 5000.0
    assert decision.side is Side.LONG


def test_signal_order_is_preserved_and_default_venue_applies():
    # Signals with an empty venue fall back to default_venue; the returned order
    # preserves the input order (so a caller assigns coids exactly as before).
    signals = [
        Signal.long(MARKET, "", size=1.0),
        Signal.short("ETH-PERP", "", size_usd=10.0),
    ]
    first, second = route_signals(signals, VENUE, _no_positions)
    assert isinstance(first, OrderRequest) and first.venue == VENUE
    assert isinstance(second, _UsdIntent) and second.venue == VENUE


# --- lot rounding + size_usd materialization at the execution bar's open ---


def test_round_lot_floors_and_returns_the_sub_lot_residual():
    lot, residual = round_lot(47.619, size_decimals=2)
    assert lot == 47.61  # floored, never grown
    assert residual == pytest.approx(0.009, abs=1e-9)


def test_materialize_usd_intent_sizes_at_the_open_not_grown():
    intent = _UsdIntent(
        market=MARKET,
        venue=VENUE,
        side=Side.LONG,
        size_usd=5000.0,
        limit_price=0.0,
        tif=None,
        margin_mode="cross",
    )
    request, residual = materialize_usd_intent(intent, open_price=105.0, size_decimals=2)
    assert request.size == 47.61  # floor(5000/105, 2dp)
    assert request.type is OrderType.MARKET
    assert residual == pytest.approx(5000 / 105 - 47.61, abs=1e-9)


def test_materialize_usd_intent_that_rounds_below_one_lot_yields_zero_size():
    intent = _UsdIntent(
        market=MARKET,
        venue=VENUE,
        side=Side.LONG,
        size_usd=1.0,
        limit_price=0.0,
        tif=None,
        margin_mode="cross",
    )
    request, _ = materialize_usd_intent(intent, open_price=105.0, size_decimals=2)
    assert request.size == 0.0  # caller rejects a zero-size intent (no size)


def test_materialize_usd_intent_limit_rests_as_gtc():
    intent = _UsdIntent(
        market=MARKET,
        venue=VENUE,
        side=Side.LONG,
        size_usd=5000.0,
        limit_price=104.0,
        tif=None,
        margin_mode="cross",
    )
    request, _ = materialize_usd_intent(intent, open_price=105.0, size_decimals=2)
    assert request.type is OrderType.LIMIT
    assert request.price == 104.0
    assert request.tif is TimeInForce.GTC
