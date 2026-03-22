"""Tests for trade journal — persistent backtest history."""
from __future__ import annotations

from flint.journal.storage import JournalStorage
from flint.models import BacktestResult, Position
from flint.store import FlintStore


def _result(pnl=100, trades=5) -> BacktestResult:
    positions = []
    for i in range(trades):
        p = Position(entry_price=100, size=10, entry_ts=i * 3600,
                     exit_price=102, exit_ts=(i + 1) * 3600, pnl=20, closed=True)
        positions.append(p)
    return BacktestResult(
        total_pnl=pnl, win_rate=0.6, max_drawdown=0.05,
        sharpe_ratio=1.5, total_trades=trades,
        winning_trades=3, losing_trades=2,
        positions=positions, equity_curve=[10000 + i * 20 for i in range(trades)],
        total_fees=5.0, funding_paid=1.0,
    )


class TestJournalStorage:
    def test_save_and_list(self):
        store = FlintStore(":memory:")
        journal = JournalStorage(store)
        journal.save_run("r1", "MA-Cross", "SOL-PERP", 3600,
                         1000, 5000, 10000, result=_result())
        runs = journal.list_runs()
        assert len(runs) == 1
        assert runs[0]["run_id"] == "r1"
        assert runs[0]["total_pnl"] == 100
        store.close()

    def test_get_run_with_trades(self):
        store = FlintStore(":memory:")
        journal = JournalStorage(store)
        journal.save_run("r1", "MA-Cross", "SOL-PERP", 3600,
                         1000, 5000, 10000, result=_result(trades=3))
        run = journal.get_run("r1")
        assert run is not None
        assert len(run["trades"]) == 3
        store.close()

    def test_get_nonexistent(self):
        store = FlintStore(":memory:")
        journal = JournalStorage(store)
        assert journal.get_run("nope") is None
        store.close()

    def test_delete_run(self):
        store = FlintStore(":memory:")
        journal = JournalStorage(store)
        journal.save_run("r1", "Test", "SOL-PERP", 3600,
                         0, 10000, 10000, result=_result())
        journal.delete_run("r1")
        assert journal.get_run("r1") is None
        assert len(journal.list_runs()) == 0
        store.close()

    def test_compare_runs(self):
        store = FlintStore(":memory:")
        journal = JournalStorage(store)
        journal.save_run("r1", "MA-Cross", "SOL-PERP", 3600,
                         0, 10000, 10000, result=_result(pnl=100))
        journal.save_run("r2", "RSI", "SOL-PERP", 3600,
                         0, 10000, 10000, result=_result(pnl=200))
        comparison = journal.compare_runs(["r1", "r2"])
        assert len(comparison) == 2
        assert comparison[0]["total_pnl"] != comparison[1]["total_pnl"]
        store.close()

    def test_save_with_params(self):
        store = FlintStore(":memory:")
        journal = JournalStorage(store)
        journal.save_run("r1", "MA-Cross", "SOL-PERP", 3600,
                         0, 10000, 10000,
                         params={"fast": 10, "slow": 30},
                         result=_result())
        run = journal.get_run("r1")
        assert run["params_json"] is not None
        store.close()
