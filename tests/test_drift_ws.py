"""Tests for DriftWebSocketFeed — mocked, no real connections."""
import asyncio

from flint.providers.drift_ws import DriftWebSocketFeed


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestMessageHandling:
    def test_trade_message_feeds_aggregator(self):
        closed = []
        feed = DriftWebSocketFeed(
            markets=["SOL-PERP"],
            resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
        )
        trade_msg = {
            "channel": "trades",
            "market": "SOL-PERP",
            "data": {"price": 150.0, "size": 10.0, "ts": 1000},
        }
        run(feed._handle_message(trade_msg))
        agg = feed._aggregators.get("SOL-PERP")
        assert agg is not None
        bar = agg.current_bar()
        assert bar is not None
        assert bar.open == 150.0

    def test_trade_closes_candle(self):
        closed = []
        feed = DriftWebSocketFeed(
            markets=["SOL-PERP"],
            resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
        )
        run(feed._handle_message({
            "channel": "trades", "market": "SOL-PERP",
            "data": {"price": 150.0, "size": 10.0, "ts": 960},
        }))
        run(feed._handle_message({
            "channel": "trades", "market": "SOL-PERP",
            "data": {"price": 155.0, "size": 5.0, "ts": 1020},
        }))
        assert len(closed) == 1
        assert closed[0].venue == "drift"

    def test_funding_message_persisted(self, tmp_path):
        from flint.store import FlintStore
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        feed = DriftWebSocketFeed(
            markets=["SOL-PERP"],
            resolution_s=60,
            on_candle_close=lambda c: None,
            store=store,
        )
        funding_msg = {
            "channel": "funding",
            "market": "SOL-PERP",
            "data": {"rate": 0.0001, "mark_price": 150.0, "index_price": 149.8, "ts": 1000},
        }
        run(feed._handle_message(funding_msg))
        rates = store.query_venue_funding("drift", "SOL-PERP")
        assert len(rates) == 1
        store.close()

    def test_unknown_channel_ignored(self):
        feed = DriftWebSocketFeed(
            markets=["SOL-PERP"],
            resolution_s=60,
            on_candle_close=lambda c: None,
        )
        run(feed._handle_message({"channel": "unknown", "data": {}}))


class TestMultipleMarkets:
    def test_separate_aggregators_per_market(self):
        feed = DriftWebSocketFeed(
            markets=["SOL-PERP", "BTC-PERP"],
            resolution_s=60,
            on_candle_close=lambda c: None,
        )
        run(feed._handle_message({
            "channel": "trades", "market": "SOL-PERP",
            "data": {"price": 150.0, "size": 10.0, "ts": 1000},
        }))
        run(feed._handle_message({
            "channel": "trades", "market": "BTC-PERP",
            "data": {"price": 65000.0, "size": 0.1, "ts": 1000},
        }))
        assert len(feed._aggregators) == 2
        assert feed._aggregators["SOL-PERP"].current_bar().open == 150.0
        assert feed._aggregators["BTC-PERP"].current_bar().open == 65000.0
