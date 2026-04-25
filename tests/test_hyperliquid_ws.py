"""Tests for HyperliquidWebSocketFeed — mocked, no real connections."""
import asyncio
from unittest.mock import AsyncMock

from flint.providers.hyperliquid_ws import HyperliquidWebSocketFeed


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestConstruction:
    def test_creates_with_markets(self):
        feed = HyperliquidWebSocketFeed(markets=["SOL-PERP", "BTC-PERP"])
        assert feed._name == "hyperliquid"
        assert len(feed._markets) == 2

    def test_testnet_url(self):
        feed = HyperliquidWebSocketFeed(markets=["SOL-PERP"], network="testnet")
        assert "testnet" in feed._url

    def test_mainnet_url(self):
        feed = HyperliquidWebSocketFeed(markets=["SOL-PERP"], network="mainnet")
        assert "testnet" not in feed._url


class TestCandleHandling:
    def test_candle_message_fires_callback(self):
        closed = []
        feed = HyperliquidWebSocketFeed(
            markets=["SOL-PERP"],
            on_candle_close=lambda c: closed.append(c),
        )
        run(feed._handle_message({
            "channel": "candle",
            "data": {"t": 1000000, "T": 1060000, "s": "SOL", "i": "1m",
                     "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "1000.0"},
        }))
        run(feed._handle_message({
            "channel": "candle",
            "data": {"t": 1060000, "T": 1120000, "s": "SOL", "i": "1m",
                     "o": "153.0", "h": "156.0", "l": "152.0", "c": "154.0", "v": "800.0"},
        }))
        assert len(closed) == 1
        assert closed[0].market == "SOL-PERP"
        assert closed[0].venue == "hyperliquid"
        assert closed[0].open == 150.0
        assert closed[0].close == 153.0

    def test_same_timestamp_updates_candle(self):
        closed = []
        feed = HyperliquidWebSocketFeed(
            markets=["SOL-PERP"],
            on_candle_close=lambda c: closed.append(c),
        )
        run(feed._handle_message({
            "channel": "candle",
            "data": {"t": 1000000, "T": 1060000, "s": "SOL", "i": "1m",
                     "o": "150.0", "h": "155.0", "l": "148.0", "c": "153.0", "v": "1000.0"},
        }))
        run(feed._handle_message({
            "channel": "candle",
            "data": {"t": 1000000, "T": 1060000, "s": "SOL", "i": "1m",
                     "o": "150.0", "h": "156.0", "l": "148.0", "c": "155.0", "v": "1200.0"},
        }))
        assert len(closed) == 0


class TestL2BookHandling:
    def test_l2_book_updates_state(self):
        feed = HyperliquidWebSocketFeed(markets=["SOL-PERP"])
        run(feed._handle_message({
            "channel": "l2Book",
            "data": {"coin": "SOL", "levels": [[{"px": "149.0", "sz": "100.0"}], [{"px": "151.0", "sz": "80.0"}]]},
        }))
        book = feed.get_orderbook("SOL-PERP")
        assert book is not None
        assert "levels" in book

    def test_unknown_market_ignored(self):
        feed = HyperliquidWebSocketFeed(markets=["SOL-PERP"])
        run(feed._handle_message({
            "channel": "l2Book",
            "data": {"coin": "UNKNOWN", "levels": [[], []]},
        }))
        assert feed.get_orderbook("UNKNOWN-PERP") is None


class TestOrderUpdateHandling:
    def test_order_update_fires_callback(self):
        updates = []
        feed = HyperliquidWebSocketFeed(
            markets=["SOL-PERP"],
            on_order_update=lambda u: updates.append(u),
        )
        run(feed._handle_message({
            "channel": "orderUpdates",
            "data": [{"order": {"oid": 42, "coin": "SOL"}, "status": "filled"}],
        }))
        assert len(updates) == 1
        assert updates[0]["order"]["oid"] == 42


class TestSubscription:
    def test_subscribe_sends_messages(self):
        feed = HyperliquidWebSocketFeed(
            markets=["SOL-PERP", "BTC-PERP"],
            user_address="0x1234",
        )
        mock_ws = AsyncMock()
        run(feed._subscribe(mock_ws))
        # 2 markets * 2 channels (candle + l2Book) + 1 orderUpdates = 5
        assert mock_ws.send.call_count == 5
