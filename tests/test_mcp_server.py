"""Tests for the Flint MCP server tools and resources.

The mcp package is an optional dependency (pip install flint[mcp]).
We mock it at the sys.modules level so tests run without it installed.
"""
from __future__ import annotations

import sys
import json
from unittest.mock import MagicMock, patch

import pytest

# Mock the mcp package before importing flint.mcp_server
_mock_mcp = MagicMock()
# FastMCP must behave as a class whose instances have .tool() and .resource() decorators
_mock_fastmcp = MagicMock()
_mock_fastmcp.return_value.tool.return_value = lambda fn: fn  # @mcp.tool() is passthrough
_mock_fastmcp.return_value.resource.return_value = lambda fn: fn  # @mcp.resource() is passthrough
_mock_fastmcp.return_value.name = "Flint"
_mock_mcp.server.fastmcp.FastMCP = _mock_fastmcp

sys.modules.setdefault("mcp", _mock_mcp)
sys.modules.setdefault("mcp.server", _mock_mcp.server)
sys.modules.setdefault("mcp.server.fastmcp", _mock_mcp.server.fastmcp)

from flint.models import Candle, OpenInterest


# ─── Fixtures ────────────────────────────────────────────────

@pytest.fixture
def mock_store():
    """Mock FlintStore with common query methods."""
    store = MagicMock()
    store._lock = MagicMock()
    store._conn = MagicMock()

    candles = [
        Candle(ts=1700000000 + i * 3600, open=100 + i, high=102 + i,
               low=99 + i, close=101 + i, volume=1000.0,
               market="SOL-PERP", resolution_s=3600)
        for i in range(100)
    ]
    store.query_candles.return_value = candles
    store.upsert_candles.return_value = 0

    store.query_funding_by_venue.return_value = {
        "drift": [{"ts": 1700000000 + i * 3600, "rate": 0.0001} for i in range(10)],
        "hyperliquid": [{"ts": 1700000000 + i * 3600, "rate": 0.00015} for i in range(10)],
    }

    store.query_open_interest.return_value = [
        OpenInterest(market="SOL-PERP", ts=1700000000, long_oi=5_000_000, short_oi=4_500_000),
    ]

    store.get_data_freshness.return_value = [
        {"market": "SOL-PERP", "resolution_s": 3600, "last_ts": 1700000000, "age_hours": 2.5},
    ]

    return candles, store


@pytest.fixture(autouse=True)
def patch_store(mock_store):
    """Patch _get_store and _get_config in the MCP server module."""
    candles, store = mock_store
    with patch("flint.mcp_server._get_store", return_value=store), \
         patch("flint.mcp_server._get_config", return_value=MagicMock()):
        yield store


# ─── Tool Registration ───────────────────────────────────────

class TestToolRegistration:
    def test_mcp_server_imports(self):
        from flint.mcp_server import mcp
        assert mcp.name == "Flint"

    def test_all_tools_registered(self):
        from flint.mcp_server import (
            run_backtest, list_strategies, get_candles, download_market_data,
            list_available_markets, list_local_markets, get_funding_rates,
            get_open_interest, get_correlation, get_data_freshness,
            optimize_strategy, start_paper_trading, stop_paper_trading,
            get_paper_sessions, get_paper_status, list_journal_runs,
            compare_runs,
        )
        # All 17 tools are importable functions
        for fn in [run_backtest, list_strategies, get_candles, download_market_data,
                   list_available_markets, list_local_markets, get_funding_rates,
                   get_open_interest, get_correlation, get_data_freshness,
                   optimize_strategy, start_paper_trading, stop_paper_trading,
                   get_paper_sessions, get_paper_status, list_journal_runs,
                   compare_runs]:
            assert callable(fn)


# ─── Backtesting Tools ───────────────────────────────────────

class TestListStrategies:
    def test_returns_valid_json(self):
        from flint.mcp_server import list_strategies
        result = json.loads(list_strategies())
        assert "strategies" in result
        assert "count" in result

    def test_has_strategies(self):
        from flint.mcp_server import list_strategies
        result = json.loads(list_strategies())
        assert result["count"] == 20
        names = [s["name"] for s in result["strategies"]]
        assert "ma_crossover" in names
        assert "rsi" in names
        assert "funding_harvest" in names
        assert "funding_arb" in names
        assert "basis_trade" in names

    def test_strategy_structure(self):
        from flint.mcp_server import list_strategies
        result = json.loads(list_strategies())
        for s in result["strategies"]:
            assert "name" in s
            assert "category" in s
            assert "description" in s
            assert "params" in s


class TestRunBacktest:
    def test_returns_valid_json(self):
        """D-4.7-full migration: MCP `run_backtest` now calls
        `flint.services.backtest.run_backtest_sync` directly. Mock at
        the service boundary instead of `BacktestEngine`."""
        from flint.mcp_server import run_backtest

        # The service returns the same dict shape `Tearsheet.to_dict()`
        # produces, plus the supplementary fields (winning_trades,
        # losing_trades, total_fees). Only fields the MCP tool reads
        # need to be present.
        fake_result = {
            "metrics": {
                "total_pnl": 500.0,
                "total_return_pct": 5.0,
                "sharpe_ratio": 1.5,
                "max_drawdown": 0.1,
                "total_trades": 20,
                "win_rate": 0.6,
            },
            "winning_trades": 12,
            "losing_trades": 8,
            "total_fees": 25.0,
            "engine_used": "python",
            "fallback_reason": None,
        }
        # Pretend candles already exist locally so the auto-fetch path
        # short-circuits.
        with patch("flint.services.backtest.run_backtest_sync",
                   return_value=fake_result), \
             patch("flint.mcp_server._get_store") as mock_store:
            mock_store.return_value.query_candles.return_value = [MagicMock()]
            result = json.loads(run_backtest(
                market="SOL-PERP", strategy="ma_crossover",
                start_date="2025-01-01", end_date="2025-06-01",
            ))

        assert result["market"] == "SOL-PERP"
        assert result["total_pnl"] == 500.0
        assert result["sharpe_ratio"] == 1.5
        assert result["total_trades"] == 20

    def test_unknown_strategy(self):
        """The service raises ValueError for unknown strategy names;
        MCP catches and surfaces as `error`."""
        from flint.mcp_server import run_backtest
        with patch("flint.services.backtest.run_backtest_sync",
                   side_effect=ValueError("Unknown strategy: nonexistent")), \
             patch("flint.mcp_server._get_store") as mock_store:
            mock_store.return_value.query_candles.return_value = [MagicMock()]
            result = json.loads(run_backtest(strategy="nonexistent"))
        assert "error" in result

    def test_no_data(self, patch_store):
        from flint.mcp_server import run_backtest
        patch_store.query_candles.return_value = []
        with patch("flint.mcp_server._download_range_mcp", return_value=[]):
            result = json.loads(run_backtest(market="FAKE-PERP"))
        assert "error" in result


# ─── Data Tools ──────────────────────────────────────────────

class TestGetCandles:
    def test_returns_candle_data(self):
        from flint.mcp_server import get_candles
        result = json.loads(get_candles(market="SOL-PERP", limit=10))
        assert result["market"] == "SOL-PERP"
        assert result["count"] <= 10
        assert "candles" in result

    def test_candle_structure(self):
        from flint.mcp_server import get_candles
        result = json.loads(get_candles(market="SOL-PERP", limit=5))
        if result["candles"]:
            c = result["candles"][0]
            for key in ["ts", "date", "open", "high", "low", "close", "volume"]:
                assert key in c

    def test_no_data_hint(self, patch_store):
        from flint.mcp_server import get_candles
        patch_store.query_candles.return_value = []
        result = json.loads(get_candles(market="FAKE-PERP"))
        assert result["count"] == 0
        assert "hint" in result


class TestDownloadMarketData:
    def test_returns_download_summary(self, patch_store):
        from flint.mcp_server import download_market_data
        patch_store.is_range_synced = MagicMock(return_value=False)
        patch_store.upsert_candles.return_value = 0
        with patch("flint.mcp_server._download_range_mcp", return_value=[]), \
             patch("flint.services.data.download_funding_all_venues", return_value=0):
            result = json.loads(download_market_data(market="SOL-PERP", days=30))
        assert result["market"] == "SOL-PERP"
        assert "total" in result
        assert "funding_venues" in result

    def test_perp_includes_funding(self, patch_store):
        from flint.mcp_server import download_market_data
        patch_store.is_range_synced = MagicMock(return_value=False)
        patch_store.upsert_candles.return_value = 0
        with patch("flint.mcp_server._download_range_mcp", return_value=[]), \
             patch("flint.services.data.download_funding_all_venues", return_value=50):
            result = json.loads(download_market_data(market="SOL-PERP", days=30))
        assert result["funding_fetched"] == 50
        assert len(result["funding_venues"]) > 0


class TestListAvailableMarkets:
    def test_returns_market_lists(self):
        from flint.mcp_server import list_available_markets
        with patch("flint.collector.tasks.MARKET_INDEX", {"SOL-PERP": 0, "BTC-PERP": 1}), \
             patch("flint.collector.tasks.SPOT_WITH_CANDLES", {"SOL", "JUP"}):
            result = json.loads(list_available_markets())
        assert "perpetuals" in result
        assert "perp_count" in result
        assert result["perp_count"] >= 2


class TestListLocalMarkets:
    def test_returns_local_data(self, patch_store):
        from flint.mcp_server import list_local_markets
        # Phase 2 T2.2: MCP now calls store.list_markets_with_data() which
        # returns dicts, not raw tuples from _conn.
        patch_store.list_markets_with_data.return_value = [
            {"market": "SOL-PERP", "resolution_s": 3600,
             "candle_count": 1000, "first_ts": 1700000000,
             "last_ts": 1700360000},
        ]
        result = json.loads(list_local_markets())
        assert "markets" in result
        assert result["total_markets"] == 1


class TestGetFundingRates:
    def test_returns_venue_data(self):
        from flint.mcp_server import get_funding_rates
        result = json.loads(get_funding_rates(market="SOL-PERP"))
        assert result["market"] == "SOL-PERP"
        assert "drift" in result["venues"]
        assert "hyperliquid" in result["venues"]

    def test_filter_by_venue(self):
        from flint.mcp_server import get_funding_rates
        result = json.loads(get_funding_rates(market="SOL-PERP", venue="drift"))
        assert result["venues"] == ["drift"]

    def test_no_data(self, patch_store):
        from flint.mcp_server import get_funding_rates
        patch_store.query_funding_by_venue.return_value = {}
        result = json.loads(get_funding_rates(market="FAKE-PERP"))
        assert result["count"] == 0
        assert "hint" in result


class TestGetOpenInterest:
    def test_returns_oi_data(self):
        from flint.mcp_server import get_open_interest
        result = json.loads(get_open_interest(market="SOL-PERP"))
        assert result["market"] == "SOL-PERP"
        assert result["latest_long_oi"] == 5_000_000
        assert result["latest_short_oi"] == 4_500_000
        assert result["net_oi"] == 500_000
        assert result["total_oi"] == 9_500_000

    def test_no_data(self, patch_store):
        from flint.mcp_server import get_open_interest
        patch_store.query_open_interest.return_value = []
        result = json.loads(get_open_interest(market="FAKE-PERP"))
        assert "note" in result


class TestGetCorrelation:
    def test_returns_matrix(self, patch_store):
        from flint.mcp_server import get_correlation
        with patch("flint.analytics.correlation.compute_correlation_matrix",
                   return_value={"SOL-PERP": {"SOL-PERP": 1.0, "BTC-PERP": 0.85},
                                 "BTC-PERP": {"SOL-PERP": 0.85, "BTC-PERP": 1.0}}):
            result = json.loads(get_correlation(markets="SOL-PERP,BTC-PERP"))
        assert "correlation_matrix" in result
        assert len(result["markets"]) == 2

    def test_insufficient_markets(self, patch_store):
        from flint.mcp_server import get_correlation
        patch_store.query_candles.side_effect = lambda m, *a, **kw: [] if m == "BTC-PERP" else [MagicMock()]
        result = json.loads(get_correlation(markets="SOL-PERP,BTC-PERP"))
        assert "error" in result


class TestGetDataFreshness:
    def test_returns_freshness(self):
        from flint.mcp_server import get_data_freshness
        result = json.loads(get_data_freshness())
        assert "freshness" in result
        assert "total_tracked" in result
        assert result["total_tracked"] == 1


# ─── Optimization Tools ─────────────────────────────────────

class TestOptimizeStrategy:
    def test_no_data(self, patch_store):
        from flint.mcp_server import optimize_strategy
        patch_store.query_candles.return_value = []
        result = json.loads(optimize_strategy(market="SOL-PERP"))
        assert "error" in result

    def test_unknown_strategy(self):
        from flint.mcp_server import optimize_strategy
        result = json.loads(optimize_strategy(strategy="nonexistent"))
        assert "error" in result


# ─── Paper Trading Tools ────────────────────────────────────

class TestPaperStatusUrl:
    def test_get_paper_status_uses_correct_url(self):
        """Regression: MCP was calling /paper/{id}/status but route is /paper/status/{id}."""
        from flint.mcp_server import get_paper_status
        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(text='{"session_id":"test"}')
            get_paper_status("test-session")
            # Verify the URL has /status/{id} not /{id}/status
            called_url = mock_get.call_args[0][0]
            assert "/status/test-session" in called_url
            assert "/test-session/status" not in called_url


# ─── Resources ───────────────────────────────────────────────

class TestResources:
    def test_guide_returns_content(self):
        from flint.mcp_server import flint_guide
        guide = flint_guide()
        assert "Flint" in guide or "flint" in guide
        assert len(guide) > 100  # should have substantial content

    def test_markets_returns_json(self):
        from flint.mcp_server import flint_markets
        with patch("flint.collector.tasks.MARKET_INDEX", {"SOL-PERP": 0}), \
             patch("flint.collector.tasks.SPOT_WITH_CANDLES", {"SOL"}):
            result = json.loads(flint_markets())
        assert "perpetuals" in result
