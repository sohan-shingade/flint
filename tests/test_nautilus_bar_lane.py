"""N3 gate — unmodified Flint bar strategies on the Nautilus core, byte-for-byte (§A7).

The headline is parity: a Flint strategy driven through ``engine_for("nautilus")``
produces the *same event log* (ORDER_PLACED / FILL / EQUITY / ORDER_REJECTED /
ORDER_CANCELLED) as the legacy bar engine did on the same feed — byte-identical to
the **frozen legacy golden** (``tests/parity/goldens/barlane_*.json``; the legacy
engine was deleted in N10). Flint's own fill models
price every fill (Tier-C parametric and Tier-A/B book-walk); the Nautilus core only
holds the account and positions so the run-end reconciliation can cross-check them.

All fixtures are hand-authored (D26). Gated on the ``nautilus`` extra.
"""

from __future__ import annotations

import pytest

pytest.importorskip("nautilus_trader")

from flint.adapters import InMemoryUserData  # noqa: E402
from flint.core.models import (  # noqa: E402
    Candle,
    MarkSnapshot,
    OrderbookSnapshot,
    Signal,
)
from flint.engine.api import EngineFeed, EngineRunSpec  # noqa: E402
from flint.engine.fills import TradePrint  # noqa: E402
from flint.engine.loop import EngineConfig  # noqa: E402
from flint.engine.portfolio import EventLog  # noqa: E402
from flint.engine.portfolio.events import FILL, ORDER_REJECTED  # noqa: E402
from flint.engine.select import engine_for  # noqa: E402
from flint.engine.tearsheet import (  # noqa: E402
    InvariantError,
    build_tearsheet,
    check_invariants,
)
from flint.ports import TenantContext  # noqa: E402
from flint.strategy.base import EngineStrategy  # noqa: E402
from flint.strategy.templates.technical import MaCrossStrategy  # noqa: E402
from flint.venues import HYPERLIQUID  # noqa: E402
from parity.golden_store import assert_matches_golden  # noqa: E402

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_040_000
CAP = "100000"
N = 8


def _bar_index(candle: Candle) -> int:
    return round((candle.ts - T0) / HOUR_MS)


# --- hand-authored feeds -----------------------------------------------------


def _base_candles(*, zero_vol_at: int | None = None) -> list[Candle]:
    out = []
    for i in range(N):
        vol = 0.0 if i == zero_vol_at else 1000.0
        out.append(
            Candle(
                ts=T0 + i * HOUR_MS,
                open=100.0 + i,
                high=101.0 + i,
                low=99.0 + i,
                close=100.5 + i,
                volume=vol,
                market=MARKET,
                resolution_s=HOUR_S,
                venue=VENUE,
            )
        )
    return out


def _marks() -> dict[str, list[MarkSnapshot]]:
    return {
        MARKET: [
            MarkSnapshot(
                market=MARKET,
                ts=T0 + i * HOUR_MS,
                mark_price=100.5 + i,
                index_price=100.5 + i,
                venue=VENUE,
            )
            for i in range(N)
        ]
    }


def _feed_ohlcv(zero_vol_at: int | None = None) -> EngineFeed:
    return EngineFeed(candles=_base_candles(zero_vol_at=zero_vol_at), marks=_marks())


def _feed_with_book(*, far_asks: bool = False) -> EngineFeed:
    """A feed carrying a recorded book + trades so fills reach Tier A/B."""
    books = {
        MARKET: [
            OrderbookSnapshot(
                market=MARKET,
                venue=VENUE,
                ts=T0 + i * HOUR_MS + 100,
                bids=[(100.0 + i, 50.0), (99.9 + i, 100.0)],
                # far_asks pushes the 2nd level outside the ±1% oracle band so a
                # large taker clips (partial + oracle_band_clipped flag).
                asks=[(100.2 + i, 50.0), ((110.0 + i) if far_asks else (100.4 + i), 100.0)],
            )
            for i in range(N)
        ]
    }
    trades = {
        MARKET: [
            TradePrint(price=100.1 + i, size=5.0, ts=T0 + i * HOUR_MS + 200)
            for i in range(N)
        ]
    }
    return EngineFeed(candles=_base_candles(), marks=_marks(), books=books, trades=trades)


def _spec() -> EngineRunSpec:
    return EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        initial_capital=CAP,
        fund_venue=VENUE,
        mark_policy="close_derived",
    )


# --- hand-authored strategies (on_candle(candle, ctx) seam) ------------------


class _LongThenClose:
    def on_candle(self, candle, ctx):
        i = _bar_index(candle)
        if i == 1:
            return [Signal.long(MARKET, VENUE, size=10.0)]
        if i == 4:
            return [Signal.close(MARKET, VENUE)]
        return []


class _OpenLong:
    def on_candle(self, candle, ctx):
        return [Signal.long(MARKET, VENUE, size=10.0)] if _bar_index(candle) == 1 else []


class _UsdLong:
    def on_candle(self, candle, ctx):
        return (
            [Signal.long(MARKET, VENUE, size_usd=5000.0)]
            if _bar_index(candle) == 1
            else []
        )


class _Flip:
    def on_candle(self, candle, ctx):
        i = _bar_index(candle)
        if i == 1:
            return [Signal.long(MARKET, VENUE, size=10.0)]
        if i == 4:
            return [Signal.short(MARKET, VENUE, size=25.0)]
        return []


class _BigTaker:
    """A taker larger than the in-band depth — exercises the oracle-band clip."""

    def on_candle(self, candle, ctx):
        return [Signal.long(MARKET, VENUE, size=80.0)] if _bar_index(candle) == 1 else []


# --- runners -----------------------------------------------------------------


def _run(engine_name: str, strategy, feed: EngineFeed):
    log = EventLog(
        InMemoryUserData(), TenantContext.local(), run_id=f"{engine_name}-{id(strategy)}"
    )
    state = engine_for(engine_name)().run(feed, strategy, event_log=log, spec=_spec())
    return log, state


CASES = {
    "long_then_close": (lambda: _LongThenClose(), _feed_ohlcv),
    "open_long": (lambda: _OpenLong(), _feed_ohlcv),
    "size_usd": (lambda: _UsdLong(), _feed_ohlcv),
    "flip": (lambda: _Flip(), _feed_ohlcv),
    "zero_volume_reject": (lambda: _OpenLong(), lambda: _feed_ohlcv(zero_vol_at=2)),
    "tier_a_book_walk": (lambda: _OpenLong(), _feed_with_book),
    "oracle_band_clip": (lambda: _BigTaker(), lambda: _feed_with_book(far_asks=True)),
    "ma_cross_template": (
        lambda: EngineStrategy(MaCrossStrategy(fast=2, slow=3, ma_type="sma")),
        _feed_ohlcv,
    ),
}


@pytest.mark.parametrize("case", list(CASES))
def test_bar_lane_matches_frozen_golden_byte_for_byte(case):
    make_strategy, make_feed = CASES[case]
    nautilus_log, _ = _run("nautilus", make_strategy(), make_feed())
    assert_matches_golden(nautilus_log, f"barlane_{case}")


def test_zero_volume_and_reject_paths_are_exercised():
    # Guard the fixtures actually produce the behaviours the parity test compares.
    log, _ = _run("nautilus", _OpenLong(), _feed_ohlcv(zero_vol_at=2))
    rejects = [e for e in log.read() if e.kind == ORDER_REJECTED]
    assert [e.payload["reason"] for e in rejects] == ["no_fill"]


def test_oracle_band_clip_flag_is_recorded():
    log, _ = _run("nautilus", _BigTaker(), _feed_with_book(far_asks=True))
    fills = [e for e in log.read() if e.kind == FILL]
    assert fills, "expected a clipped partial fill"
    assert "oracle_band_clipped" in fills[0].payload["flags"]
    assert fills[0].payload["is_partial"] is True


def test_template_run_produces_events_and_folds_clean():
    log, _ = _run(
        "nautilus", EngineStrategy(MaCrossStrategy(fast=2, slow=3, ma_type="sma")), _feed_ohlcv()
    )
    events = log.read()
    assert any(e.kind == FILL for e in events)
    tearsheet = build_tearsheet(events)
    check_invariants(tearsheet, {})  # every order reaches a terminal state (§19.2)


def test_run_end_reconciles_with_an_open_position():
    # An open position at run end exercises the position side/size/entry checks.
    _, state = _run("nautilus", _OpenLong(), _feed_ohlcv())  # raises InvariantError on drift
    pos = state.positions[(VENUE, MARKET)]
    assert pos.size == 10.0
    assert pos.side.value == "long"


def test_fee_is_flints_and_nautilus_agrees():
    # The run reconciles cash against the Nautilus balance, which charges the fee via
    # FlintTagFeeModel — so a clean run proves Nautilus's commission equals Flint's fee.
    log, state = _run("nautilus", _LongThenClose(), _feed_ohlcv())
    fills = [e for e in log.read() if e.kind == FILL]
    assert len(fills) == 2
    assert all(f.payload["fee"] > 0 for f in fills)
    assert state.account(VENUE).fees_paid > 0


def test_reconciliation_trips_on_a_corrupted_shadow_book(monkeypatch):
    # Deliberately corrupt only the shadow book's entry price (Nautilus fills at the
    # real tag price), so shadow and Nautilus disagree at run end and reconcile raises.
    import flint.engine.nautilus.translate as translate

    real = translate.apply_fill_delta

    def corrupt(positions, key, *, side, size, price, margin_mode):
        return real(positions, key, side=side, size=size, price=price + 5.0, margin_mode=margin_mode)

    monkeypatch.setattr(translate, "apply_fill_delta", corrupt)
    with pytest.raises(InvariantError):
        _run("nautilus", _OpenLong(), _feed_ohlcv())


def test_run_is_deterministic():
    log_a, _ = _run("nautilus", _LongThenClose(), _feed_ohlcv())
    log_b, _ = _run("nautilus", _LongThenClose(), _feed_ohlcv())
    assert [e.to_row() for e in log_a.read()] == [e.to_row() for e in log_b.read()]
