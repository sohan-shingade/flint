"""Unit gate for the tick-native strategy surface (§8.6, plan §A7b).

Pure Python, no Nautilus: these pin the ``TickStrategy`` / ``TickEngineStrategy``
contract that the tick lane relies on — which handlers a strategy is deemed to
"want" (so the host subscribes only those streams, §8.6), and how the adapter
normalizes a handler's return, gates a non-executable venue (D28), and flags an
imperative ``submit_order`` for the tearsheet (§8.1). All fixtures hand-authored
(D26); the native-matching goldens live in ``test_nautilus_ticklane.py``.
"""

from __future__ import annotations

import pytest

from flint.core.models import Signal
from flint.strategy.base import IMPERATIVE_ORDERS_NOTE
from flint.strategy.tick import (
    TICK_HANDLERS,
    TickEngineStrategy,
    TickStrategy,
    overridden_handlers,
)

VENUE = "hyperliquid"  # the sole executable venue (EXECUTABLE_VENUES)
OFF_VENUE = "jupiter"  # non-executable in v1 — a signal here is gated to a rejection
MARKET = "SOL-PERP"
NOW = 1_700_000_000_000


class _Ctx:
    """A minimal engine ctx stand-in exposing only what dispatch/handlers touch."""

    def __init__(self, venue: str = VENUE, now: int = NOW) -> None:
        self.default_venue = venue
        self.now = now
        self.submitted: list = []

    def submit_order(self, order):
        self.submitted.append(order)
        return order


# --- overridden_handlers: only the streams a strategy actually wants (§8.6) ----


class _TradesOnly(TickStrategy):
    def on_trade(self, trade, ctx):
        return []


class _TradeAndBook(TickStrategy):
    def on_trade(self, trade, ctx):
        return []

    def on_book(self, delta, ctx):
        return []


class _AllFive(TickStrategy):
    def on_trade(self, t, ctx): return []
    def on_quote(self, q, ctx): return []
    def on_book(self, d, ctx): return []
    def on_funding(self, r, ctx): return []
    def on_candle(self, c, ctx): return []


def test_overridden_handlers_reports_only_the_subclass_overrides():
    assert overridden_handlers(_TradesOnly()) == frozenset({"on_trade"})
    assert overridden_handlers(_TradeAndBook()) == frozenset({"on_trade", "on_book"})
    assert overridden_handlers(_AllFive()) == frozenset(TICK_HANDLERS)


def test_raw_tick_strategy_wants_no_stream():
    # An un-subclassed TickStrategy overrides nothing, so the host subscribes nothing.
    assert overridden_handlers(TickStrategy()) == frozenset()


def test_default_handlers_are_noops_returning_none():
    s = TickStrategy()
    assert s.on_trade(object(), object()) is None
    assert s.on_quote(object(), object()) is None
    assert s.on_book(object(), object()) is None
    assert s.on_funding(object(), object()) is None
    assert s.on_candle(object(), object()) is None


def test_lane_marker_is_tick_on_strategy_and_adapter():
    # The lane marker is what services route on and the engine dispatches on (§19.1).
    assert TickStrategy.lane == "tick"
    assert TickEngineStrategy(_TradesOnly()).lane == "tick"


# --- TickEngineStrategy: normalize + gate + imperative flag (§8.1/D28) ---------


class _ReturnsBareSignal(TickStrategy):
    def on_trade(self, trade, ctx):
        return Signal.long(MARKET, VENUE, size=1.0)


class _ReturnsNone(TickStrategy):
    def on_trade(self, trade, ctx):
        return None


class _ReturnsOffVenue(TickStrategy):
    def on_trade(self, trade, ctx):
        return [Signal.long(MARKET, OFF_VENUE, size=1.0)]


def test_dispatch_wraps_a_bare_signal_in_a_list():
    adapter = TickEngineStrategy(_ReturnsBareSignal())
    out = adapter.dispatch("on_trade", object(), _Ctx())
    assert len(out) == 1 and out[0].venue == VENUE


def test_dispatch_normalizes_none_to_empty():
    adapter = TickEngineStrategy(_ReturnsNone())
    assert adapter.dispatch("on_trade", object(), _Ctx()) == []


def test_dispatch_gates_a_non_executable_venue_to_a_rejection():
    adapter = TickEngineStrategy(_ReturnsOffVenue())
    out = adapter.dispatch("on_trade", object(), _Ctx())
    assert out == []  # the off-venue leg never reaches the fill path
    rejections = adapter.drain_rejections()
    assert len(rejections) == 1
    assert rejections[0].reason == "venue_not_executable"
    # The rejection is timestamped at the event that produced it (ctx.now).
    assert rejections[0].ts == NOW
    # drain clears — a second drain is empty (§19.1 one-shot semantics).
    assert adapter.drain_rejections() == []


class _Imperative(TickStrategy):
    def on_trade(self, trade, ctx):
        ctx.submit_order(object())
        return []


def test_dispatch_flags_imperative_submit_for_the_tearsheet():
    adapter = TickEngineStrategy(_Imperative())
    ctx = _Ctx()
    assert adapter.tearsheet_notes() == []  # nothing imperative yet
    adapter.dispatch("on_trade", object(), ctx)
    assert ctx.submitted, "the imperative submit reached the real ctx"
    assert adapter.used_imperative_orders is True
    assert IMPERATIVE_ORDERS_NOTE in adapter.tearsheet_notes()


def test_bad_return_type_raises_loudly():
    class _Bad(TickStrategy):
        def on_trade(self, trade, ctx):
            return 42

    with pytest.raises(TypeError):
        TickEngineStrategy(_Bad()).dispatch("on_trade", object(), _Ctx())
