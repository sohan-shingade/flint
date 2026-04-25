"""Tests for per-venue fill model dispatch: registry + engine integration."""
from __future__ import annotations


from flint.execution.fill_registry import create_fill_model, create_venue_fill_models
from flint.execution.fill_drift import DriftFillModel
from flint.execution.fill_hyperliquid import HyperliquidFillModel
from flint.execution.fill_jupiter import JupiterFillModel
from flint.execution.fill_cex import BinanceFillModel, OkxFillModel, BybitFillModel
from flint.execution.fill_models import FillPipeline, FillModel
from flint.execution.backtest_context import BacktestContext
from flint.backtest.engine import BacktestEngine
from flint.models import Candle, Order, Side, Fill


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

def test_create_fill_model_by_venue():
    assert isinstance(create_fill_model("drift"), DriftFillModel)
    assert isinstance(create_fill_model("hyperliquid"), HyperliquidFillModel)
    assert isinstance(create_fill_model("jupiter"), JupiterFillModel)
    assert isinstance(create_fill_model("binance"), BinanceFillModel)
    assert isinstance(create_fill_model("okx"), OkxFillModel)
    assert isinstance(create_fill_model("bybit"), BybitFillModel)


def test_create_fill_model_unknown_venue():
    model = create_fill_model("unknown")
    assert isinstance(model, FillPipeline)


def test_create_fill_model_default_venue():
    """The 'default' venue is not registered — should fall back to FillPipeline."""
    model = create_fill_model("default")
    assert isinstance(model, FillPipeline)


def test_create_venue_fill_models():
    models = create_venue_fill_models(["drift", "hyperliquid", "jupiter"])
    assert len(models) == 3
    assert isinstance(models["drift"], DriftFillModel)
    assert isinstance(models["hyperliquid"], HyperliquidFillModel)
    assert isinstance(models["jupiter"], JupiterFillModel)


def test_create_venue_fill_models_empty():
    models = create_venue_fill_models([])
    assert models == {}


def test_create_fill_model_kwargs_passed():
    """Keyword args should be forwarded to the fill model constructor."""
    model = create_fill_model("drift", seed=42)
    assert isinstance(model, DriftFillModel)
    # Two models with the same seed should produce the same first random value
    model2 = create_fill_model("drift", seed=42)
    assert model._rng.random() == model2._rng.random()


# ---------------------------------------------------------------------------
# BacktestContext per-venue dispatch tests
# ---------------------------------------------------------------------------

def _make_candle(ts=1000, close=100.0, market="SOL-PERP"):
    return Candle(
        ts=ts, open=100.0, high=105.0, low=95.0, close=close,
        volume=1000.0, market=market, resolution_s=3600,
    )


class _TrackingFillModel(FillModel):
    """A fill model that records calls and returns deterministic fills."""

    def __init__(self, venue_tag: str, price_offset: float = 0.0):
        self.venue_tag = venue_tag
        self.price_offset = price_offset
        self.fill_market_calls: list = []
        self.fill_limit_calls: list = []

    def fill_market(self, order: Order, candle: Candle):
        self.fill_market_calls.append((order, candle))
        return Fill(
            market=order.market,
            side=order.side,
            price=candle.close + self.price_offset,
            size=order.size,
            fee=0.0,
            ts=candle.ts,
            order_id=order.order_id,
            venue=self.venue_tag,
        )

    def fill_limit(self, order: Order, candle: Candle):
        self.fill_limit_calls.append((order, candle))
        if order.side == Side.LONG and candle.low <= order.price:
            return Fill(
                market=order.market, side=order.side, price=order.price,
                size=order.size, fee=0.0, ts=candle.ts, order_id=order.order_id,
                venue=self.venue_tag,
            )
        if order.side == Side.SHORT and candle.high >= order.price:
            return Fill(
                market=order.market, side=order.side, price=order.price,
                size=order.size, fee=0.0, ts=candle.ts, order_id=order.order_id,
                venue=self.venue_tag,
            )
        return None


def test_context_dispatches_market_order_to_venue_model():
    """Market orders with a venue should route to that venue's fill model."""
    drift_model = _TrackingFillModel("drift", price_offset=0.5)
    default_model = _TrackingFillModel("default", price_offset=0.0)

    ctx = BacktestContext(
        initial_capital=10_000.0,
        fill_model=default_model,
        venue_fill_models={"drift": drift_model},
    )

    candle = _make_candle()
    ctx.set_candle(candle)
    ctx.market_order("SOL-PERP", Side.LONG, 1.0, venue="drift")
    fills = ctx.process_market_orders(candle)

    assert len(fills) == 1
    assert len(drift_model.fill_market_calls) == 1
    assert len(default_model.fill_market_calls) == 0
    # Price should include drift_model's offset
    assert fills[0].price == candle.close + 0.5


def test_context_falls_back_to_default_model():
    """Orders with unknown venue should use the default fill model."""
    drift_model = _TrackingFillModel("drift")
    default_model = _TrackingFillModel("default")

    ctx = BacktestContext(
        initial_capital=10_000.0,
        fill_model=default_model,
        venue_fill_models={"drift": drift_model},
    )

    candle = _make_candle()
    ctx.set_candle(candle)
    ctx.market_order("SOL-PERP", Side.LONG, 1.0, venue="default")
    fills = ctx.process_market_orders(candle)

    assert len(fills) == 1
    assert len(default_model.fill_market_calls) == 1
    assert len(drift_model.fill_market_calls) == 0


def test_context_no_venue_models_backward_compat():
    """When venue_fill_models is not provided, all orders use the default."""
    default_model = _TrackingFillModel("default")

    ctx = BacktestContext(
        initial_capital=10_000.0,
        fill_model=default_model,
    )

    candle = _make_candle()
    ctx.set_candle(candle)
    ctx.market_order("SOL-PERP", Side.LONG, 1.0, venue="drift")
    fills = ctx.process_market_orders(candle)

    assert len(fills) == 1
    assert len(default_model.fill_market_calls) == 1


def test_context_multiple_venues_same_bar():
    """Multiple orders on different venues in the same bar get dispatched correctly."""
    drift_model = _TrackingFillModel("drift", price_offset=1.0)
    hl_model = _TrackingFillModel("hyperliquid", price_offset=2.0)
    default_model = _TrackingFillModel("default", price_offset=0.0)

    ctx = BacktestContext(
        initial_capital=100_000.0,
        fill_model=default_model,
        venue_fill_models={"drift": drift_model, "hyperliquid": hl_model},
    )

    candle = _make_candle()
    ctx.set_candle(candle)
    ctx.market_order("SOL-PERP", Side.LONG, 1.0, venue="drift")
    ctx.market_order("SOL-PERP", Side.LONG, 1.0, venue="hyperliquid")
    ctx.market_order("SOL-PERP", Side.LONG, 1.0, venue="default")
    fills = ctx.process_market_orders(candle)

    assert len(fills) == 3
    assert len(drift_model.fill_market_calls) == 1
    assert len(hl_model.fill_market_calls) == 1
    assert len(default_model.fill_market_calls) == 1


def test_context_close_all_uses_venue_model():
    """close_all_positions should dispatch through venue fill models too."""
    drift_model = _TrackingFillModel("drift", price_offset=0.5)
    default_model = _TrackingFillModel("default")

    ctx = BacktestContext(
        initial_capital=10_000.0,
        fill_model=default_model,
        venue_fill_models={"drift": drift_model},
    )

    # Open a position on drift
    candle = _make_candle()
    ctx.set_candle(candle)
    ctx.market_order("SOL-PERP", Side.LONG, 1.0, venue="drift")
    ctx.process_market_orders(candle)

    # Force close all
    fills = ctx.close_all_positions(candle)
    assert len(fills) == 1
    # The close order should have been dispatched to drift_model
    assert len(drift_model.fill_market_calls) == 2  # open + close


# ---------------------------------------------------------------------------
# Engine integration tests
# ---------------------------------------------------------------------------

class _DummyStrategy:
    """Minimal strategy for testing engine integration."""
    def reset(self):
        pass

    def on_candle(self, candle, history, ctx=None):
        if ctx and len(history) == 1:
            ctx.market_order("SOL-PERP", Side.LONG, 1.0, venue="drift")
        return None

    def parameters(self):
        return {}


def test_engine_passes_venue_fill_models_to_context():
    """Engine should forward venue_fill_models to BacktestContext."""
    drift_model = _TrackingFillModel("drift")

    engine = BacktestEngine(
        strategy=_DummyStrategy(),
        initial_capital=10_000.0,
        venue_fill_models={"drift": drift_model},
    )

    candles = [_make_candle(ts=1000 + i * 3600) for i in range(3)]
    engine.run(candles)

    # The drift model should have been used for the venue="drift" order
    assert len(drift_model.fill_market_calls) >= 1


def test_engine_feeds_orderbook_to_venue_models():
    """Engine should call set_orderbook on all venue fill models that support it."""

    class _BookAwareFill(_TrackingFillModel):
        def __init__(self, venue_tag):
            super().__init__(venue_tag)
            self.orderbooks_received = []

        def set_orderbook(self, book):
            self.orderbooks_received.append(book)

    drift_model = _BookAwareFill("drift")
    engine = BacktestEngine(
        strategy=_DummyStrategy(),
        initial_capital=10_000.0,
        venue_fill_models={"drift": drift_model},
    )

    candles = [_make_candle(ts=1000 + i * 3600) for i in range(3)]
    engine.run(candles)

    # set_orderbook should have been called once per candle
    assert len(drift_model.orderbooks_received) == len(candles)


def test_engine_feeds_candle_buffer_to_jupiter_model():
    """Engine should call set_candle_buffer on JupiterFillModel."""

    class _BufferAwareFill(_TrackingFillModel):
        def __init__(self, venue_tag):
            super().__init__(venue_tag)
            self.buffers_received = []

        def set_candle_buffer(self, candles):
            self.buffers_received.append(list(candles))

    jupiter_model = _BufferAwareFill("jupiter")
    engine = BacktestEngine(
        strategy=_DummyStrategy(),
        initial_capital=10_000.0,
        venue_fill_models={"jupiter": jupiter_model},
    )

    candles = [_make_candle(ts=1000 + i * 3600) for i in range(5)]
    engine.run(candles)

    # set_candle_buffer should have been called once per candle
    assert len(jupiter_model.buffers_received) == len(candles)
    # The buffer should shrink as we advance through candles
    assert len(jupiter_model.buffers_received[0]) >= len(jupiter_model.buffers_received[-1])


def test_engine_backward_compat_no_venue_models():
    """Without venue_fill_models, engine should work exactly as before."""
    engine = BacktestEngine(
        strategy=_DummyStrategy(),
        initial_capital=10_000.0,
    )

    candles = [_make_candle(ts=1000 + i * 3600) for i in range(3)]
    result = engine.run(candles)
    # Should complete without errors
    assert result.total_trades >= 0
