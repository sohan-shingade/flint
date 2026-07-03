"""Aggregator parity — Flint ``CandleAggregator`` ≡ Nautilus internal aggregation (§A7).

The bar lane and the paper lane must agree on what a candle *is*. Paper bars come
from :class:`flint.data.livefeed.aggregator.CandleAggregator`; a tick-lane
``on_candle`` run derives its bars from Nautilus's internal time-bar aggregation.
This pins them equal over the same hand-authored trade fragment (D26).

Two corrections reconcile Nautilus's aggregation to Flint's (documented in
``dataconv``/``timeconv`` and applied by the shim):

1. **Labeling.** Nautilus stamps a bar at its CLOSE boundary; Flint stamps at the
   START. The shim subtracts one bar width
   (``timeconv.bar_close_ns_to_candle_start_ms``) to recover the START.
2. **No fabricated bars.** Nautilus's timer fills trade-less intervals with an
   empty (zero-volume, forward-filled) bar; Flint never fabricates a candle (D26).
   ``time_bars_build_with_no_updates=False`` suppresses those, so the bar *sets*
   match.

The trade fragment is contiguous (every covered minute has a trade) and its final
bar is left open — both aggregators discard the open bar (Flint only emits on
``flush``; Nautilus only when a later crossing closes it), so the emitted sets align.
"""

from __future__ import annotations

import pytest

pytest.importorskip("nautilus_trader")

from flint.data.livefeed.aggregator import CandleAggregator  # noqa: E402
from flint.data.normalize import TradePrint as NormalizedTradePrint  # noqa: E402
from flint.engine.nautilus import timeconv  # noqa: E402
from flint.engine.nautilus._compat import (  # noqa: E402
    USDC,
    AccountType,
    Actor,
    AggregationSource,
    AggressorSide,
    BacktestEngine,
    BacktestEngineConfig,
    BarAggregation,
    BarSpecification,
    BarType,
    CryptoPerpetual,
    Currency,
    InstrumentId,
    LoggingConfig,
    Money,
    OmsType,
    Price,
    PriceType,
    Quantity,
    Symbol,
    TradeId,
    TradeTick,
    Venue,
)
from nautilus_trader.config import DataEngineConfig  # noqa: E402

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
RES_S = 60
SEC_MS = 1000
T0 = 1_700_000_040_000  # minute-aligned base

# Contiguous minutes 0, 1, 2 each carry >=1 print; minute 3 has one print that
# opens (and thus closes minutes 0-2) but is itself left open and discarded.
#   (ts_ms, price, size)
TRADES = [
    (T0 + 1 * SEC_MS, 100.0, 1.0),
    (T0 + 20 * SEC_MS, 102.0, 2.0),
    (T0 + 40 * SEC_MS, 101.0, 1.0),
    (T0 + 60 * SEC_MS + 5 * SEC_MS, 103.0, 1.0),
    (T0 + 60 * SEC_MS + 30 * SEC_MS, 104.0, 3.0),
    (T0 + 120 * SEC_MS + 10 * SEC_MS, 105.0, 2.0),
    (T0 + 120 * SEC_MS + 50 * SEC_MS, 106.0, 1.0),
    (T0 + 180 * SEC_MS + 5 * SEC_MS, 107.0, 1.0),  # opens minute 3 -> discarded
]


def _flint_candles():
    agg = CandleAggregator(MARKET, VENUE, RES_S)
    out = []
    for i, (ts, price, size) in enumerate(TRADES):
        for lb in agg.add_trade(
            NormalizedTradePrint(
                ts=ts, market=MARKET, venue=VENUE, price=price, size=size, side="buy", trade_id=i
            )
        ):
            c = lb.candle
            out.append((c.ts, c.open, c.high, c.low, c.close, c.volume))
    return out


def _nautilus_candles():
    iid = InstrumentId(Symbol(MARKET), Venue(VENUE.upper()))
    instrument = CryptoPerpetual(
        instrument_id=iid, raw_symbol=Symbol(MARKET), base_currency=Currency.from_str("SOL"),
        quote_currency=USDC, settlement_currency=USDC, is_inverse=False,
        price_precision=2, size_precision=2, price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.01"), ts_event=0, ts_init=0,
    )
    bar_type = BarType(
        iid, BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST), AggregationSource.INTERNAL
    )

    class _Capture(Actor):
        def __init__(self) -> None:
            super().__init__()
            self.bars: list = []

        def on_start(self) -> None:
            self.subscribe_bars(bar_type)

        def on_bar(self, bar) -> None:
            self.bars.append(bar)

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id="FLINT-001",
            logging=LoggingConfig(bypass_logging=True),
            # Correction 2: no fabricated bars for trade-less minutes (matches D26).
            data_engine=DataEngineConfig(time_bars_build_with_no_updates=False),
        )
    )
    engine.add_venue(
        venue=Venue(VENUE.upper()), oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
        base_currency=USDC, starting_balances=[Money(100_000, USDC)],
    )
    engine.add_instrument(instrument)
    cap = _Capture()
    engine.add_actor(cap)
    ticks = [
        TradeTick(
            instrument_id=iid, price=Price.from_str(f"{price:.2f}"), size=Quantity.from_str(f"{size:.2f}"),
            aggressor_side=AggressorSide.NO_AGGRESSOR, trade_id=TradeId(f"{ts}-{i}"),
            ts_event=timeconv.ms_to_ns(ts), ts_init=timeconv.ms_to_ns(ts),
        )
        for i, (ts, price, size) in enumerate(TRADES)
    ]
    engine.add_data(ticks, sort=True)
    engine.run()
    out = []
    for bar in cap.bars:
        # Correction 1: CLOSE-boundary label -> START label.
        start_ms = timeconv.bar_close_ns_to_candle_start_ms(bar.ts_event, RES_S)
        out.append(
            (start_ms, float(bar.open), float(bar.high), float(bar.low), float(bar.close), float(bar.volume))
        )
    engine.dispose()
    return out


def test_flint_and_nautilus_aggregation_agree_on_the_same_fragment():
    flint = _flint_candles()
    nautilus = _nautilus_candles()
    # Three contiguous closed bars (minutes 0, 1, 2); minute 3 stays open in both.
    assert [c[0] for c in flint] == [T0, T0 + 60 * SEC_MS, T0 + 120 * SEC_MS]
    assert flint == nautilus
