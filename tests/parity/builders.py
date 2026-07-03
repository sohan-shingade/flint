"""Hand-authored feed + strategy builders shared by the parity goldens (D26).

These are the proven fixture builders from the per-phase parity tests
(``test_nautilus_bar_lane`` / ``_funding`` / ``_liquidation``), consolidated
here so ``tests/parity/`` is self-contained and survives a refactor of those
tests. Every feed is hand-authored unit input; nothing is generated (D26).

All builders share one clock: hourly bars on the ``T0`` base, the Hyperliquid
venue, and the ``close_derived`` mark policy (the bar lane synthesises marks
from candle closes unless a feed carries recorded marks).
"""

from __future__ import annotations

from flint.core.models import (
    Candle,
    FundingRate,
    MarkSnapshot,
    OrderbookSnapshot,
    Signal,
)
from flint.engine.api import EngineFeed, EngineRunSpec
from flint.engine.fills import TradePrint
from flint.engine.loop import EngineConfig
from flint.venues import HYPERLIQUID

VENUE = "hyperliquid"
SOL = "SOL-PERP"
ETH = "ETH-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_040_000


def bar_index(candle: Candle) -> int:
    return round((candle.ts - T0) / HOUR_MS)


# --- feed pieces -------------------------------------------------------------


def candle(
    i: int, o: float, h: float, low: float, cl: float, market: str = SOL, *, volume: float = 1000.0
) -> Candle:
    return Candle(
        ts=T0 + i * HOUR_MS,
        open=o,
        high=h,
        low=low,
        close=cl,
        volume=volume,
        market=market,
        resolution_s=HOUR_S,
        venue=VENUE,
    )


def flat(i: int, market: str = SOL, px: float = 100.0) -> Candle:
    """A benign bar pinned around ``px`` — no liquidation pressure."""
    return candle(i, px, px + 0.1, px - 0.1, px, market)


def mark(ts: int, mark_price: float, index_price: float, market: str = SOL) -> MarkSnapshot:
    return MarkSnapshot(
        market=market, ts=ts, mark_price=mark_price, index_price=index_price, venue=VENUE
    )


def final(ts: int, rate_hourly: float, market: str = SOL) -> FundingRate:
    return FundingRate(
        market=market,
        ts=ts,
        rate_hourly=rate_hourly,
        interval_s=HOUR_S,
        price_basis="oracle",
        rate_type="final",
        venue=VENUE,
    )


def predicted(ts: int, rate_hourly: float, market: str = SOL) -> FundingRate:
    return FundingRate(
        market=market,
        ts=ts,
        rate_hourly=rate_hourly,
        interval_s=HOUR_S,
        price_basis="oracle",
        rate_type="predicted",
        venue=VENUE,
    )


def book_open() -> dict:
    """A recorded book + trade at bar 1 (the T+1 fill bar) so a long fills at a
    clean 100.0 (Tier A) — the §6.4/§6.5 worked examples need entry 100.00."""
    book = OrderbookSnapshot(SOL, T0 + HOUR_MS, ((99.9, 20.0),), ((100.0, 20.0),), VENUE)
    return {"books": {SOL: [book]}, "trades": {SOL: [TradePrint(100.0, 1.0, T0 + HOUR_MS)]}}


def spec(initial_capital: str = "100000", *, mark_policy: str = "close_derived") -> EngineRunSpec:
    return EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        initial_capital=initial_capital,
        fund_venue=VENUE,
        mark_policy=mark_policy,
    )


# --- strategies (on_candle(candle, ctx) seam) --------------------------------


class OpenLongAt0:
    """Long ``size`` SOL at bar 0 (fills bar 1 open), then hold."""

    def __init__(self, size: float = 10.0, margin_mode: str = "cross") -> None:
        self._size = size
        self._mode = margin_mode

    def on_candle(self, candle, ctx):
        if candle.market == SOL and bar_index(candle) == 0:
            return [Signal.long(SOL, VENUE, size=self._size, margin_mode=self._mode)]
        return []


class OpenLongAt1:
    """Long 10 at bar 1 (fills bar 2 open), hold — a position spans settlements."""

    def on_candle(self, candle, ctx):
        return [Signal.long(SOL, VENUE, size=10.0)] if bar_index(candle) == 1 else []


class FlipMidway:
    """Long 10 at bar 1, flip to short 10 at bar 3 — settlements straddle the flip."""

    def on_candle(self, candle, ctx):
        i = bar_index(candle)
        if i == 1:
            return [Signal.long(SOL, VENUE, size=10.0)]
        if i == 3:
            return [Signal.short(SOL, VENUE, size=20.0)]  # net -10 (flip to short 10)
        return []


class LongThenClose:
    """Long 10 at bar 1 (fills bar 2), close at bar 4 — exercises T+1 entry and exit."""

    def on_candle(self, candle, ctx):
        i = bar_index(candle)
        if i == 1:
            return [Signal.long(SOL, VENUE, size=10.0)]
        if i == 4:
            return [Signal.close(SOL, VENUE)]
        return []


class UsdLong:
    """A ``size_usd`` order — exercises notional→size materialization + residual."""

    def on_candle(self, candle, ctx):
        return [Signal.long(SOL, VENUE, size_usd=5000.0)] if bar_index(candle) == 1 else []


class BigTaker:
    """A taker larger than the in-band depth — exercises the oracle-band clip."""

    def on_candle(self, candle, ctx):
        return [Signal.long(SOL, VENUE, size=80.0)] if bar_index(candle) == 1 else []


class OpenSolAndEth:
    """Open a cross SOL long and a cross ETH long, each on its market's first bar."""

    def __init__(self, sol_at: int = 0, eth_at: int = 2) -> None:
        self._sol_at = sol_at
        self._eth_at = eth_at

    def on_candle(self, candle, ctx):
        if bar_index(candle) == self._sol_at and candle.market == SOL:
            return [Signal.long(SOL, VENUE, size=10.0)]
        if bar_index(candle) == self._eth_at and candle.market == ETH:
            return [Signal.long(ETH, VENUE, size=1.0)]
        return []


# --- N-bar OHLCV feeds (bar-lane goldens: T+1, size_usd, clip, reject) --------


def ohlcv_candles(n: int = 8, *, zero_vol_at: int | None = None) -> list[Candle]:
    out = []
    for i in range(n):
        vol = 0.0 if i == zero_vol_at else 1000.0
        out.append(candle(i, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, SOL, volume=vol))
    return out


def ohlcv_marks(n: int = 8) -> dict[str, list[MarkSnapshot]]:
    return {SOL: [mark(T0 + i * HOUR_MS, 100.5 + i, 100.5 + i) for i in range(n)]}


def feed_ohlcv(zero_vol_at: int | None = None) -> EngineFeed:
    return EngineFeed(candles=ohlcv_candles(zero_vol_at=zero_vol_at), marks=ohlcv_marks())


def feed_with_book(*, far_asks: bool = False) -> EngineFeed:
    """A feed carrying a recorded book + trades so fills reach Tier A/B."""
    n = 8
    books = {
        SOL: [
            OrderbookSnapshot(
                market=SOL,
                venue=VENUE,
                ts=T0 + i * HOUR_MS + 100,
                bids=[(100.0 + i, 50.0), (99.9 + i, 100.0)],
                asks=[(100.2 + i, 50.0), ((110.0 + i) if far_asks else (100.4 + i), 100.0)],
            )
            for i in range(n)
        ]
    }
    trades = {SOL: [TradePrint(price=100.1 + i, size=5.0, ts=T0 + i * HOUR_MS + 200) for i in range(n)]}
    return EngineFeed(candles=ohlcv_candles(), marks=ohlcv_marks(), books=books, trades=trades)


# --- funding feeds (§6.4) ----------------------------------------------------


def _funding_candles(n: int = 6) -> list[Candle]:
    return [candle(i, 100.0, 100.1, 99.9, 100.0, SOL) for i in range(n)]


def _funding_marks(n: int = 6) -> dict[str, list[MarkSnapshot]]:
    return {SOL: [mark(T0 + i * HOUR_MS, 100.0, 100.0) for i in range(n)]}


def feed_6_4() -> EngineFeed:
    """The §6.4 golden: one final settlement of +0.01% on a held long 10 @ oracle 100."""
    return EngineFeed(
        candles=_funding_candles(),
        marks=_funding_marks(),
        funding={SOL: [final(T0 + 3 * HOUR_MS, 0.0001)]},
    )


def feed_divergence() -> EngineFeed:
    """Predicted rows diverge materially from the final that settles (§6.4 contract)."""
    return EngineFeed(
        candles=_funding_candles(),
        marks=_funding_marks(),
        funding={
            SOL: [
                predicted(T0 + 0 * HOUR_MS, 0.05),
                predicted(T0 + 1 * HOUR_MS, 0.05),
                predicted(T0 + 2 * HOUR_MS, 0.05),
                final(T0 + 3 * HOUR_MS, 0.0001),  # the only row that moves money
            ]
        },
    )


def feed_multi_flip() -> EngineFeed:
    """Two settlements straddling a long→short flip — charge the position then open."""
    return EngineFeed(
        candles=_funding_candles(),
        marks=_funding_marks(),
        funding={
            SOL: [
                final(T0 + 2 * HOUR_MS, 0.001),  # position is long 10 here
                final(T0 + 4 * HOUR_MS, 0.001),  # position is short 10 after the flip
            ]
        },
    )


# --- liquidation feeds (§6.5) ------------------------------------------------


def feed_6_5() -> EngineFeed:
    """§6.5 worked example: long 10 @ 100.00 (Tier-A book), ≈$50 margin, dives to 97."""
    candles = [flat(0), flat(1), candle(2, 100.0, 100.1, 97.0, 97.0), flat(3, px=97.0)]
    return EngineFeed(
        candles=candles,
        marks={SOL: [mark(T0 + 2 * HOUR_MS + 60_000, 97.0, 97.0)]},
        funding={},
        **book_open(),
    )


def feed_backstop() -> EngineFeed:
    """A gap far past maintenance (entry 100, dive to 95 on ≈$30) → backstop + shortfall."""
    candles = [flat(0), flat(1), candle(2, 100.0, 100.1, 95.0, 95.0), flat(3, px=95.0)]
    return EngineFeed(
        candles=candles,
        marks={SOL: [mark(T0 + 2 * HOUR_MS + 60_000, 95.0, 95.0)]},
        funding={},
        **book_open(),
    )


def feed_tier_c() -> EngineFeed:
    """Tier-C (no marks): the dive is evaluated on the candle low, flagged ambiguous."""
    candles = [flat(0), flat(1), candle(2, 100.0, 100.1, 96.0, 100.0), flat(3)]
    return EngineFeed(candles=candles, marks={}, funding={})


def feed_funding_saves() -> EngineFeed:
    """§6.1 golden: the step-(4) dive would liquidate, but a step-(3) credit rescues it."""
    candles = [flat(0), flat(1), candle(2, 100.0, 100.1, 97.0, 100.0), flat(3)]
    return EngineFeed(
        candles=candles,
        marks={SOL: [mark(T0 + 2 * HOUR_MS + 60_000, 97.0, 100.0)]},
        funding={SOL: [final(T0 + 2 * HOUR_MS + 60_000, -0.03)]},
        **book_open(),
    )


def feed_cross_pool() -> EngineFeed:
    """Two cross legs share one pool; SOL craters, the larger loss closes first (§6.5)."""
    candles = [
        flat(0, SOL),
        flat(1, SOL),
        flat(2, ETH),
        flat(3, ETH),
        candle(4, 100.0, 100.1, 98.0, 98.0, SOL),
        flat(5, SOL, px=98.0),
    ]
    return EngineFeed(
        candles=candles,
        marks={
            ETH: [mark(T0 + 3 * HOUR_MS + 60_000, 100.0, 100.0, ETH)],
            SOL: [mark(T0 + 4 * HOUR_MS + 60_000, 98.0, 98.0, SOL)],
        },
        funding={},
    )
