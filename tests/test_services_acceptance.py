"""Phase-1 acceptance: a no-op backtest runs end-to-end through services (§18 item 1).

The one test that proves the walking skeleton is wired: an empty engine runs a
no-op backtest **through the services front door**, **inside the sandbox**,
**emitting versioned events**, with results **persisted via UserDataPort** — and
the tenant seam holds across the whole path.
"""

from __future__ import annotations

from flint.adapters import InMemoryUserData, InProcessJobRunner
from flint.engine.portfolio import NOOP, RUN_FINISHED, RUN_STARTED, EventLog
from flint.ports import TenantContext
from flint.services import BacktestRequest, run_backtest

ALICE = TenantContext(tenant_id="alice")
BOB = TenantContext(tenant_id="bob")

NOOP_STRATEGY = "def run(ctx):\n    return {'seen_universe': ctx['universe']}"


def _wire():
    return InMemoryUserData(), InProcessJobRunner()


def test_noop_backtest_runs_end_to_end():
    user_data, job_runner = _wire()
    request = BacktestRequest(
        run_id="run-1", strategy_source=NOOP_STRATEGY, universe=("SOL-PERP", "BTC-PERP")
    )

    result = run_backtest(
        ALICE, request, user_data=user_data, job_runner=job_runner, now_ms=1_700_000_000_000
    )

    # Front door returned a completed run.
    assert result.status == "done"
    assert result.events == 3
    # The strategy actually executed in the sandbox and its output flowed back.
    assert result.summary["strategy_result"] == {"seen_universe": ["SOL-PERP", "BTC-PERP"]}

    # Results persisted via UserDataPort, tenant-scoped.
    stored = user_data.load_run(ALICE, "run-1")
    assert stored.status == "done"
    assert stored.summary["strategy_result"]["seen_universe"] == ["SOL-PERP", "BTC-PERP"]

    # Versioned events emitted through the EventLog, in order.
    events = EventLog(user_data, ALICE, "run-1").read()
    assert [e.kind for e in events] == [RUN_STARTED, NOOP, RUN_FINISHED]
    assert all(e.event_version == 1 for e in events)  # every event is versioned
    assert events[1].payload["strategy_result"]["seen_universe"] == [
        "SOL-PERP",
        "BTC-PERP",
    ]


def test_tenant_isolation_holds_across_the_whole_path():
    user_data, job_runner = _wire()
    run_backtest(
        ALICE,
        BacktestRequest(run_id="run-1", strategy_source=NOOP_STRATEGY),
        user_data=user_data,
        job_runner=job_runner,
    )
    # Bob sees neither Alice's run nor her events.
    assert user_data.list_runs(BOB) == []
    assert EventLog(user_data, BOB, "run-1").read() == []


def test_sandbox_is_really_in_the_path_denied_import_fails_the_run():
    # A strategy that reaches for `os` must be blocked BY THE SANDBOX — proving
    # the sandbox is genuinely on the execution path, not bypassed.
    user_data, job_runner = _wire()
    evil = "import os\ndef run(ctx):\n    return os.environ"
    result = run_backtest(
        BOB,
        BacktestRequest(run_id="evil-1", strategy_source=evil),
        user_data=user_data,
        job_runner=job_runner,
    )

    assert result.status == "failed"
    assert "ImportError" in result.summary["error"]
    # The run is still recorded and closed (never left dangling).
    assert user_data.load_run(BOB, "evil-1").status == "failed"
    kinds = [e.kind for e in EventLog(user_data, BOB, "evil-1").read()]
    assert kinds == [RUN_STARTED, RUN_FINISHED]  # no NOOP — strategy never produced


def test_strategy_exception_fails_the_run_cleanly():
    user_data, job_runner = _wire()
    boom = "def run(ctx):\n    raise ValueError('kaboom')"
    result = run_backtest(
        ALICE,
        BacktestRequest(run_id="boom-1", strategy_source=boom),
        user_data=user_data,
        job_runner=job_runner,
    )
    assert result.status == "failed"
    assert "kaboom" in result.summary["error"]
