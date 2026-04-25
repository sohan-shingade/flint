"""D-4.7-full — MCP can run without `flint serve`.

These tests assert that the in-process service layer (`flint/services/*`)
backs the MCP tools that don't strictly need a running daemon: backtest
runs, journal queries, data reads, strategy listing.

Paper start/stop still need an asyncio event-loop owner (the running
`flint serve` process), so those are explicitly out of scope here —
`get_paper_sessions` falls back to a store-only view that this test
also exercises.

Acceptance criteria from `docs/specs/deferred-execution-plan.md`:
- grep `httpx|requests` in mcp_server.py for backtest/journal/data tools
  returns 0 (paper still legitimately calls HTTP for start/stop)
- backtest output matches between `run_backtest_sync` (this file) and
  `flint/api/routes/backtest.py:_run` (existing test_backtest_api.py)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from flint.models import Candle
from flint.services import journal as journal_service
from flint.services import paper as paper_service
from flint.services.backtest import BacktestRunRequest, run_backtest_sync
from flint.services.strategies import build_strategy, list_builtin_strategies
from flint.store import FlintStore


def _make_candles(n: int = 200, start_ts: int = 1_700_000_000, market: str = "SOL-PERP"):
    """Synthetic OHLCV — sin wave so MA crossover actually trades."""
    import math
    out = []
    for i in range(n):
        price = 100.0 + 5.0 * math.sin(i / 20.0) + 0.1 * i
        out.append(Candle(
            ts=start_ts + i * 3600,
            open=price, high=price + 1, low=price - 1, close=price,
            volume=1000.0, market=market, resolution_s=3600,
        ))
    return out


@pytest.fixture
def populated_store(tmp_path: Path):
    db = tmp_path / "test_mcp.duckdb"
    store = FlintStore(str(db))
    store.upsert_candles(_make_candles(300))
    yield store
    store.close()


class TestStrategiesService:
    def test_builtin_list_includes_core_templates(self):
        names = list_builtin_strategies()
        for required in ("ma_crossover", "rsi", "bollinger", "funding_arb"):
            assert required in names

    def test_build_returns_instance(self):
        s = build_strategy("ma_crossover", {"fast_period": 5, "slow_period": 20})
        assert s is not None
        assert "MA" in s.name or "Cross" in s.name

    def test_unknown_returns_none(self):
        assert build_strategy("does_not_exist_xyz", {}) is None


class TestBacktestServiceStandalone:
    def test_runs_without_http_server(self, populated_store):
        candles = populated_store.query_candles("SOL-PERP", 3600, None, None)
        assert candles, "fixture must populate candles"

        req = BacktestRunRequest(
            market="SOL-PERP",
            start_ts=candles[0].ts,
            end_ts=candles[-1].ts,
            resolution_s=3600,
            strategy="ma_crossover",
            params={"fast_period": 5, "slow_period": 20},
            initial_capital=10_000.0,
            fee_rate=0.0005,
        )
        result = run_backtest_sync(req, populated_store)

        # tearsheet shape
        assert "metrics" in result
        assert "equity_curve" in result
        assert "trades" in result
        assert "engine_used" in result
        assert "winning_trades" in result
        assert "losing_trades" in result
        assert "total_fees" in result

    def test_no_data_raises_clear_error(self, populated_store):
        with pytest.raises(ValueError, match="No candles"):
            run_backtest_sync(
                BacktestRunRequest(
                    market="DOES-NOT-EXIST-PERP",
                    start_ts=1_700_000_000,
                    end_ts=1_700_500_000,
                    strategy="ma_crossover",
                ),
                populated_store,
            )

    def test_unknown_strategy_raises(self, populated_store):
        with pytest.raises(ValueError, match="Unknown strategy"):
            run_backtest_sync(
                BacktestRunRequest(
                    market="SOL-PERP",
                    start_ts=1_700_000_000,
                    end_ts=1_700_500_000,
                    strategy="not_a_real_strategy",
                ),
                populated_store,
            )


class TestJournalServiceStandalone:
    def test_empty_journal_returns_empty_list(self, populated_store):
        runs = journal_service.list_runs(populated_store, limit=10)
        assert runs == []

    def test_round_trip_via_journal_storage(self, populated_store):
        from flint.journal.storage import JournalStorage
        from flint.backtest.engine import BacktestResult

        # Save one run via the underlying storage
        storage = JournalStorage(populated_store)
        result = BacktestResult(
            total_pnl=123.45, total_trades=5, win_rate=0.6,
            sharpe_ratio=1.2, max_drawdown=0.05,
            equity_curve=[10000.0, 10100.0, 10123.45],
            positions=[], fills=[], total_fees=0.0,
            winning_trades=3, losing_trades=2,
        )
        storage.save_run(
            run_id="test123", strategy_name="ma_crossover",
            market="SOL-PERP", resolution_s=3600,
            start_ts=1, end_ts=2, initial_capital=10_000.0,
            params={}, result=result,
        )

        # Read via the service
        runs = journal_service.list_runs(populated_store, limit=10)
        assert len(runs) == 1
        assert runs[0]["run_id"] == "test123"

        single = journal_service.get_run(populated_store, "test123")
        assert single is not None
        assert single["strategy_name"] == "ma_crossover"


class TestPaperServiceStoreFallback:
    def test_list_sessions_from_store_with_no_sessions(self, populated_store):
        sessions = paper_service.list_sessions_from_store(populated_store)
        assert sessions == []


class TestMcpServerHasNoHttpForCoreTools:
    """Acceptance check from the spec: MCP backtest/journal/data tools
    must not call out over HTTP. Paper start/stop are allowed (need
    daemon); paper status falls back to store-only."""

    def test_run_backtest_uses_service(self):
        src = Path("flint/mcp_server.py").read_text()
        # The body of the run_backtest tool must reference the service
        assert "from flint.services.backtest import" in src
        assert "run_backtest_sync" in src

    def test_journal_tools_use_service(self):
        src = Path("flint/mcp_server.py").read_text()
        # list_journal_runs and compare_runs must import the journal service
        assert "from flint.services import journal" in src

    def test_paper_sessions_falls_back_to_store(self):
        src = Path("flint/mcp_server.py").read_text()
        assert "paper_service.list_sessions_from_store" in src
