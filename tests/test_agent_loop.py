"""The agentic loop — author → validate → backtest → read failure → revise (§13.1).

This is slice 7.3's acceptance: a scripted agent loop that iterates on USER SOURCE
end to end for >= 3 turns, exercising every structured-feedback branch (a screen
violation, a sandbox runtime error, a look-ahead leak caught by the sandboxed
truncation probe, and a clean converging strategy). All sources and data are
hand-authored (D26).
"""

from __future__ import annotations

import pyarrow as pa

from flint.adapters import InMemoryUserData
from flint.data import DataManager
from flint.data.ranges import Kind
from flint.data.sources import InMemoryCacheSource
from flint.ports import TenantContext
from flint.agent import Feedback, run_agent_loop, sequence_author
from flint.mcp_srv import AgentTools

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
T0 = 1_700_000_000_000
H = 3_600_000
N = 8


def _data() -> DataManager:
    closes = [100.0, 101.0, 102.0, 101.0, 103.0, 104.0, 103.0, 105.0]
    cache = InMemoryCacheSource()
    cache.store(
        VENUE,
        MARKET,
        Kind.CANDLES,
        pa.table(
            {
                "ts": [T0 + i * H for i in range(N)],
                "open": closes,
                "high": [c + 1.0 for c in closes],
                "low": [c - 1.0 for c in closes],
                "close": closes,
                "volume": [1000.0] * N,
                "market": [MARKET] * N,
                "resolution_s": [3600] * N,
                "venue": [VENUE] * N,
            }
        ),
    )
    tp = [T0 + i * H for i in range(N)]
    tf = [T0 + i * H + 60_000 for i in range(N)]
    cache.store(
        VENUE,
        MARKET,
        Kind.FUNDING,
        pa.table(
            {
                "ts": tp + tf,
                "rate_hourly": [0.001] * (2 * N),
                "interval_s": [3600] * (2 * N),
                "price_basis": ["oracle"] * (2 * N),
                "rate_type": ["predicted"] * N + ["final"] * N,
                "venue": [VENUE] * (2 * N),
                "market": [MARKET] * (2 * N),
            }
        ),
    )
    return DataManager(sources=[cache])


def _covered_end() -> int:
    return T0 + (N - 1) * H + 60_000 + 1


def _tools() -> AgentTools:
    return AgentTools(
        tenant=TenantContext(tenant_id="agent"),
        user_data=InMemoryUserData(),
        data=_data(),
    )


def _bt_kwargs() -> dict:
    return dict(
        universe=(MARKET,),
        venues=(VENUE,),
        start_ms=T0,
        end_ms=_covered_end(),
        resolution_s=3600,
    )


# Four hand-authored revisions, one per structured-feedback branch.
BAD_IMPORT = """
import requests
from flint.strategy import Strategy


class S(Strategy):
    def on_candle(self, candle, history, ctx):
        return []
"""

RUNTIME_ERROR = """
from flint.strategy import Strategy


class S(Strategy):
    def on_candle(self, candle, history, ctx):
        return 1 / 0
"""

LEAKY = """
from flint.strategy import Strategy
from flint.core.models import Signal


def features(rows):
    last = rows[-1].close
    return [r.close / last for r in rows]


class S(Strategy):
    def on_candle(self, candle, history, ctx):
        if len(history) < 2:
            return []
        return [Signal.long(candle.market, candle.venue, size_usd=1000.0)]
"""

CLEAN = """
from flint.strategy import Strategy
from flint.core.models import Signal


class S(Strategy):
    def on_candle(self, candle, history, ctx):
        if len(history) < 2:
            return []
        if candle.close > history[-2].close:
            return [Signal.long(candle.market, candle.venue, size_usd=2000.0)]
        return [Signal(market=candle.market, venue=candle.venue, action="close")]
"""


def test_scripted_loop_converges_and_exercises_every_feedback_branch():
    author = sequence_author([BAD_IMPORT, RUNTIME_ERROR, LEAKY, CLEAN])
    session = run_agent_loop(_tools(), author, backtest_kwargs=_bt_kwargs())

    assert session.converged is True
    assert session.n_iterations == 4  # >= 3 iterations on user source (acceptance)

    steps = session.iterations
    # Turn 0: a forbidden import → structured screen error, no run.
    assert steps[0].status == "invalid"
    assert steps[0].validation["stage"] == "screen"
    assert steps[0].verdict is None
    # Turn 1: a runtime error → OS-isolated sandbox error, still no run.
    assert steps[1].status == "invalid"
    assert steps[1].validation["stage"] == "sandbox"
    # Turn 2: it runs, but the sandboxed truncation probe flags a leak → revise.
    assert steps[2].status == "needs_revision"
    assert steps[2].verdict == "ok"
    assert steps[2].validation["leak_detected"] is True
    assert any(r["reason"] == "lookahead_detected" for r in steps[2].failure_reasons)
    # Turn 3: a clean strategy runs and converges.
    assert steps[3].status == "clean"
    assert steps[3].verdict == "ok"
    assert steps[3].results["metrics"]["n_returns"] == N - 1


def test_reactive_author_revises_off_structured_feedback():
    # A closure-based author that reads the last feedback stage and picks the next
    # source — the same loop a real LLM-backed author drives.
    plan = {
        None: BAD_IMPORT,
        "screen": RUNTIME_ERROR,
        "sandbox": CLEAN,
    }

    def author(feedback: Feedback | None) -> str | None:
        if feedback is None:
            return plan[None]
        return plan.get(feedback.payload.get("stage"))

    session = run_agent_loop(_tools(), author, backtest_kwargs=_bt_kwargs())
    assert session.converged is True
    assert session.final().status == "clean"
    # It walked bad-import → runtime-error → clean, keyed off the feedback stage.
    assert [s.status for s in session.iterations] == ["invalid", "invalid", "clean"]


def test_author_giving_up_stops_the_loop_without_converging():
    session = run_agent_loop(
        _tools(), sequence_author([BAD_IMPORT]), backtest_kwargs=_bt_kwargs()
    )
    assert session.converged is False
    assert session.n_iterations == 1
    assert session.final().status == "invalid"
