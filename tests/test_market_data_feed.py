"""D-2.1.b Step 7 — MarketDataFeed unit tests."""
from __future__ import annotations

from dataclasses import dataclass

from flint.execution.market_data_feed import MarketDataFeed
from flint.models import Candle


@dataclass
class _StubOrderbook:
    market: str
    ts: int
    bids: tuple = ()
    asks: tuple = ()


@dataclass
class _StubOI:
    market: str
    ts: int
    long_oi: float
    short_oi: float


def _candle(ts=1700000000, market="SOL-PERP", close=100.0) -> Candle:
    return Candle(
        ts=ts, open=close, high=close + 1, low=close - 1, close=close,
        volume=1000.0, market=market, resolution_s=3600,
    )


class TestMarketHistories:
    def test_empty_returns_empty(self):
        f = MarketDataFeed()
        assert f.candles("SOL-PERP") == []
        assert f.markets() == []

    def test_set_and_iterate(self):
        f = MarketDataFeed()
        f.set_histories({
            "SOL-PERP": [_candle(ts=i) for i in range(10)],
            "BTC-PERP": [_candle(ts=i, market="BTC-PERP") for i in range(5)],
        })
        assert sorted(f.markets()) == ["BTC-PERP", "SOL-PERP"]
        assert len(f.candles("SOL-PERP", lookback=3)) == 3
        # the last 3 by ts
        assert [c.ts for c in f.candles("SOL-PERP", lookback=3)] == [7, 8, 9]


class TestOrderbook:
    def test_add_and_latest(self):
        f = MarketDataFeed()
        f.add_orderbook(_StubOrderbook(market="SOL-PERP", ts=1))
        f.add_orderbook(_StubOrderbook(market="SOL-PERP", ts=2))
        latest = f.latest_orderbook("SOL-PERP")
        assert latest is not None
        assert latest.ts == 2

    def test_latest_returns_none_for_unknown_market(self):
        f = MarketDataFeed()
        assert f.latest_orderbook("MISSING-PERP") is None


class TestOpenInterest:
    def test_add_and_latest(self):
        f = MarketDataFeed()
        f.add_open_interest(_StubOI(market="SOL-PERP", ts=1, long_oi=100.0, short_oi=80.0))
        f.add_open_interest(_StubOI(market="SOL-PERP", ts=2, long_oi=110.0, short_oi=90.0))
        assert f.latest_oi("SOL-PERP") == (110.0, 90.0)

    def test_recent_returns_tuples(self):
        f = MarketDataFeed()
        for i in range(5):
            f.add_open_interest(_StubOI(market="SOL-PERP", ts=i,
                                        long_oi=float(i), short_oi=float(i)))
        rec = f.oi_recent("SOL-PERP", lookback=3)
        assert len(rec) == 3
        assert rec[0] == (2, 2.0, 2.0)


class TestBacktestContextIntegration:
    def test_context_owns_feed(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        assert isinstance(ctx._mdf, MarketDataFeed)

    def test_set_market_histories_routes_through_feed(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        hist = {"SOL-PERP": [_candle(ts=i) for i in range(5)]}
        ctx.set_market_histories(hist)
        assert ctx.markets == ["SOL-PERP"]
        assert len(ctx.get_candles("SOL-PERP", lookback=2)) == 2

    def test_legacy_market_histories_alias_reassignable(self):
        """Some code paths in the engine do `self._market_histories = ...`
        directly. The setter must persist that to the feed."""
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx._market_histories = {"BTC-PERP": [_candle(market="BTC-PERP")]}
        assert ctx.markets == ["BTC-PERP"]

    def test_orderbook_round_trip(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx.add_orderbook_snapshot(_StubOrderbook(market="SOL-PERP", ts=42))
        # `get_orderbook` requires either a market arg or current_candle
        latest = ctx.get_orderbook("SOL-PERP")
        assert latest is not None and latest.ts == 42

    def test_open_interest_round_trip(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        ctx.add_open_interest(_StubOI(market="SOL-PERP", ts=1,
                                       long_oi=10.0, short_oi=8.0))
        assert ctx.get_open_interest("SOL-PERP") == (10.0, 8.0)


class TestFeedIsolation:
    def test_two_contexts_have_independent_feeds(self):
        from flint.execution.backtest_context import BacktestContext
        a = BacktestContext(initial_capital=1_000)
        b = BacktestContext(initial_capital=2_000)
        a.add_orderbook_snapshot(_StubOrderbook(market="SOL-PERP", ts=1))
        assert b.get_orderbook("SOL-PERP") is None
        assert a._mdf is not b._mdf
