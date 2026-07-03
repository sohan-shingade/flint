"""CLOB fill model: tiers, the spread, the queue, the oracle band (§6.3).

The centrepiece is the §6.3 worked example — a 10-SOL market buy walking a real
book to VWAP 100.08 at 8 bps slippage. The rest pin the behaviours naive
backtests get wrong: partials/rejects per TIF, the Hyperliquid oracle band,
Tier-C parametric spread+impact with a zero-volume reject, stale-book degradation,
and queue-position maker fills classified maker vs taker.
"""

from __future__ import annotations

import math
from random import Random

import pytest

from flint.core.models import Order, OrderbookSnapshot, OrderType, Side, TimeInForce
from flint.engine.fills import (
    ClobFillModel,
    FillContext,
    LatencyModel,
    OraclePoolFillModel,
    TradePrint,
    fill_model_for,
)
from flint.venues import HYPERLIQUID, MarketStructure

MODEL = ClobFillModel()
MARKET = "SOL-PERP"
VENUE = "hyperliquid"

# The §6.3 ask book: 4 @ 100.00, 5 @ 100.10, 8 @ 100.30.
ASK_BOOK = OrderbookSnapshot(
    market=MARKET,
    ts=0,
    bids=((99.98, 100.0),),
    asks=((100.00, 4.0), (100.10, 5.0), (100.30, 8.0)),
    venue=VENUE,
)
A_TRADE = (TradePrint(100.0, 1.0, 0),)  # presence → Tier A


def _buy(size, type=OrderType.MARKET, price=0.0, tif=TimeInForce.IOC):
    return Order(market=MARKET, side=Side.LONG, type=type, size=size, price=price,
                 venue=VENUE, tif=tif, client_order_id="c1")


def _sell(size, type=OrderType.MARKET, price=0.0, tif=TimeInForce.IOC):
    return Order(market=MARKET, side=Side.SHORT, type=type, size=size, price=price,
                 venue=VENUE, tif=tif, client_order_id="c2")


def _ctx(**kw):
    base = dict(reference_price=100.0, ts=0, price_sig_figs=5)
    base.update(kw)
    return FillContext(**base)


# --- §6.3 worked example ---------------------------------------------------


def test_section_6_3_golden_market_buy_walks_the_book_to_vwap_100_08():
    result = MODEL.fill(_buy(10.0), _ctx(book=ASK_BOOK, trades=A_TRADE))
    fill = result.fill
    # 4@100.00 + 5@100.10 + 1@100.30 = 1000.80 / 10 = 100.08.
    assert fill.price == pytest.approx(100.08)
    assert fill.size == pytest.approx(10.0)
    assert fill.slippage_bps == pytest.approx(8.0)  # (100.08-100.00)/100.00 * 1e4
    assert fill.is_partial is False
    assert fill.liquidity == "taker"
    assert fill.fidelity_tier == "A"  # book + trades


# --- partial / reject per TIF ----------------------------------------------


def test_market_buy_beyond_depth_partially_fills_under_ioc():
    # Only 17 visible; a size-20 IOC fills 17 and cancels the rest.
    result = MODEL.fill(_buy(20.0, tif=TimeInForce.IOC), _ctx(book=ASK_BOOK))
    assert result.fill.size == pytest.approx(17.0)
    assert result.fill.is_partial is True
    assert result.fill.fidelity_tier == "B"  # book, no trades


def test_fill_or_kill_rejects_when_depth_is_insufficient():
    assert MODEL.fill(_buy(20.0, tif=TimeInForce.FOK), _ctx(book=ASK_BOOK)) is None


# --- Hyperliquid oracle band -----------------------------------------------


def test_oracle_band_makes_depth_beyond_the_band_unavailable():
    # oracle 100.00, band 10 bps → hi 100.10: the 100.30 level is beyond it.
    result = MODEL.fill(
        _buy(10.0), _ctx(book=ASK_BOOK, oracle_price=100.0, oracle_band_bps=10.0)
    )
    assert result.fill.size == pytest.approx(9.0)  # 4 + 5, not the 100.30 level
    assert result.fill.is_partial is True
    assert "oracle_band_clipped" in result.flags


def test_touch_beyond_the_oracle_band_rejects_entirely():
    far_book = OrderbookSnapshot(MARKET, 0, ((99.9, 5.0),), ((100.30, 8.0),), VENUE)
    result = MODEL.fill(
        _buy(1.0), _ctx(book=far_book, oracle_price=100.0, oracle_band_bps=10.0)
    )
    assert result is None


# --- Tier-C parametric model -----------------------------------------------


def test_tier_c_market_buy_applies_half_spread_plus_sqrt_impact():
    # No book → Tier C. notional 100, bar $vol 1e6 → impact 10 bps; + 1 bps
    # half-spread = 11 bps above the open (adverse for a buy).
    result = MODEL.fill(
        _buy(1.0),
        _ctx(bar_dollar_volume=1_000_000.0, half_spread_bps=1.0, impact_k=0.1),
    )
    assert result.fill.price == pytest.approx(100.11)
    assert result.fill.slippage_bps == pytest.approx(11.0)
    assert result.fill.fidelity_tier == "C"
    assert "uncalibrated" in result.flags


def test_tier_c_rejects_a_zero_volume_bar():
    # No liquidity evidence to invent from (D26).
    assert MODEL.fill(_buy(1.0), _ctx(bar_dollar_volume=0.0)) is None


def test_stale_book_degrades_to_tier_c_and_is_flagged():
    result = MODEL.fill(
        _buy(1.0),
        _ctx(book=ASK_BOOK, stale_book=True, bar_dollar_volume=1_000_000.0),
    )
    assert result.fill.fidelity_tier == "C"
    assert "stale_book" in result.flags


# --- maker queue position ---------------------------------------------------

# A book where a sell limit at 100.00 rests (does not cross the 99.90 bid).
MAKER_BOOK = OrderbookSnapshot(MARKET, 0, ((99.90, 50.0),), ((100.10, 50.0),), VENUE)


def test_tier_a_maker_fills_only_after_the_queue_ahead_clears():
    # Resting sell 10 @ 100.00, 5 ahead in queue; 8 trades print at/through 100.00
    # → 3 clear past the queue → 3 fill as MAKER.
    order = _sell(10.0, type=OrderType.LIMIT, price=100.00, tif=TimeInForce.GTC)
    trades = (TradePrint(100.00, 8.0, 1),)
    result = MODEL.fill(order, _ctx(book=MAKER_BOOK, trades=trades, queue_ahead=5.0))
    assert result.fill.size == pytest.approx(3.0)
    assert result.fill.liquidity == "maker"
    assert result.fill.fidelity_tier == "A"
    assert result.fill.is_partial is True


def test_tier_b_maker_uses_a_flagged_probabilistic_proxy():
    order = _sell(10.0, type=OrderType.LIMIT, price=100.00, tif=TimeInForce.GTC)
    result = MODEL.fill(
        order, _ctx(book=MAKER_BOOK, queue_ahead=5.0, volume_at_price=8.0)
    )
    assert result.fill.size == pytest.approx(3.0)
    assert result.fill.liquidity == "maker"
    assert result.fill.fidelity_tier == "B"
    assert "estimated" in result.flags


def test_maker_that_has_not_cleared_its_queue_does_not_fill():
    order = _sell(10.0, type=OrderType.LIMIT, price=100.00, tif=TimeInForce.GTC)
    trades = (TradePrint(100.00, 3.0, 1),)  # < queue_ahead
    assert MODEL.fill(order, _ctx(book=MAKER_BOOK, trades=trades, queue_ahead=5.0)) is None


def test_a_limit_that_crosses_on_arrival_is_a_taker():
    # Buy limit at 101 with best ask 100.00 → crosses → taker (walks the book).
    order = _buy(4.0, type=OrderType.LIMIT, price=101.0, tif=TimeInForce.GTC)
    result = MODEL.fill(order, _ctx(book=ASK_BOOK, trades=A_TRADE))
    assert result.fill.liquidity == "taker"
    assert result.fill.price == pytest.approx(100.00)  # filled at the 4@100.00 level


# --- selection + latency + oracle-pool stub --------------------------------


def test_fill_model_is_chosen_by_market_structure():
    assert isinstance(fill_model_for(MarketStructure.CLOB), ClobFillModel)
    assert isinstance(fill_model_for(MarketStructure.ORACLE_POOL), OraclePoolFillModel)


def test_oracle_pool_fills_are_not_faked_before_jupiter_lands():
    with pytest.raises(NotImplementedError):
        OraclePoolFillModel().fill(_buy(1.0), _ctx())


def test_latency_draws_are_deterministic_for_a_seed():
    model = LatencyModel(HYPERLIQUID.latency)
    a = [model.draw_ms(Random(7)) for _ in range(1)]
    b = [model.draw_ms(Random(7)) for _ in range(1)]
    assert a == b  # same seed → identical draw


def test_latency_median_and_p95_match_the_profile():
    model = LatencyModel(HYPERLIQUID.latency)
    rng = Random(0)
    draws = sorted(model.draw_ms(rng) for _ in range(20000))
    median = draws[len(draws) // 2]
    p95 = draws[int(len(draws) * 0.95)]
    assert median == pytest.approx(250.0, rel=0.05)
    assert p95 == pytest.approx(600.0, rel=0.08)


def test_price_rounds_to_five_significant_figures():
    # A VWAP with more than 5 sig figs is rounded (HL price rule).
    book = OrderbookSnapshot(MARKET, 0, ((99.9, 5.0),), ((100.123456, 5.0),), VENUE)
    result = MODEL.fill(_buy(1.0), _ctx(book=book, price_sig_figs=5))
    assert result.fill.price == pytest.approx(100.12)
    assert math.isclose(result.fill.price, 100.12, abs_tol=1e-9)
