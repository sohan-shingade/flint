"""Tests for CandleAggregator — trade-to-candle conversion."""
import pytest
from flint.models import Candle
from flint.providers.candle_aggregator import CandleAggregator


class TestBarConstruction:
    def test_first_trade_opens_bar(self):
        closed = []
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
        )
        agg.process_trade(price=150.0, size=10.0, ts=1000)
        bar = agg.current_bar()
        assert bar is not None
        assert bar.open == 150.0
        assert bar.high == 150.0
        assert bar.low == 150.0
        assert bar.close == 150.0
        assert bar.volume == 10.0
        assert len(closed) == 0

    def test_updates_ohlcv(self):
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: None,
        )
        agg.process_trade(price=150.0, size=10.0, ts=1020)
        agg.process_trade(price=152.0, size=5.0, ts=1030)
        agg.process_trade(price=148.0, size=3.0, ts=1040)
        agg.process_trade(price=151.0, size=7.0, ts=1050)
        bar = agg.current_bar()
        assert bar.open == 150.0
        assert bar.high == 152.0
        assert bar.low == 148.0
        assert bar.close == 151.0
        assert bar.volume == 25.0

    def test_bar_closes_on_boundary(self):
        closed = []
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
        )
        agg.process_trade(price=150.0, size=10.0, ts=960)
        agg.process_trade(price=152.0, size=5.0, ts=980)
        agg.process_trade(price=153.0, size=8.0, ts=1020)
        assert len(closed) == 1
        assert closed[0].ts == 960
        assert closed[0].open == 150.0
        assert closed[0].close == 152.0
        assert closed[0].volume == 15.0
        assert closed[0].venue == "drift"
        assert closed[0].market == "SOL-PERP"
        assert closed[0].resolution_s == 60
        bar = agg.current_bar()
        assert bar.open == 153.0
        assert bar.volume == 8.0

    def test_multiple_bar_closes(self):
        closed = []
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
        )
        agg.process_trade(price=100.0, size=1.0, ts=0)
        agg.process_trade(price=101.0, size=1.0, ts=30)
        agg.process_trade(price=105.0, size=1.0, ts=120)
        assert len(closed) >= 1
        assert closed[0].ts == 0

    def test_no_trades_no_bar(self):
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: None,
        )
        assert agg.current_bar() is None

    def test_store_persistence(self, tmp_path):
        from flint.store import FlintStore
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        closed = []
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
            store=store,
        )
        agg.process_trade(price=150.0, size=10.0, ts=0)
        agg.process_trade(price=151.0, size=5.0, ts=60)
        assert len(closed) == 1
        candles = store.query_candles("SOL-PERP", 60)
        assert len(candles) == 1
        assert candles[0].close == 150.0
        store.close()

    def test_close_bar_explicitly(self):
        closed = []
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
        )
        agg.process_trade(price=150.0, size=10.0, ts=0)
        candle = agg.close_bar()
        assert candle is not None
        assert candle.close == 150.0
        assert len(closed) == 1
        assert agg.current_bar() is None
