"""Paper over untrusted user source — the sandboxed session + its front door (D25).

``services.start_paper_source`` is the paper counterpart of
``run_backtest_source``: researcher-written source is validated by the sandbox/
lint gate, then every engine step of the returned ``SandboxedPaperSession`` runs
inside the OS-isolated child — untrusted code never executes in the orchestrator
process. Coverage: the pre-run gate (invalid / tick-native / no-Strategy
sources), an end-to-end headless run with warm-started steps, kill/restart via
``resume_paper_source`` without double-fills, and step-for-step parity with the
in-process template session. All inputs are hand-authored fragments (D26).
"""

from __future__ import annotations

import pytest

from flint.adapters import InMemoryUserData
from flint.core.models import Candle, MarkSnapshot, Signal
from flint.data.livefeed import InMemoryGapSource
from flint.engine.portfolio import EventLog
from flint.engine.portfolio.events import FILL
from flint.live import PaperSession, SlippageBaseline
from flint.ports import TenantContext
from flint.services import (
    SourceValidationError,
    ValidationError,
    resume_paper_source,
    start_paper_source,
)
from flint.strategy import Strategy
from flint.strategy.templates.registry import TemplateSpec

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = HOUR_S * 1000
BASE_HOUR = 472_223


def bar(n: int) -> int:
    return (BASE_HOUR + n) * HOUR_MS


def _lake(first: int, last: int, px: float = 100.0) -> InMemoryGapSource:
    key = (VENUE, MARKET)
    return InMemoryGapSource(
        candles={
            key: [
                Candle(
                    ts=bar(n),
                    open=px,
                    high=px,
                    low=px,
                    close=px,
                    volume=1.0,
                    market=MARKET,
                    resolution_s=HOUR_S,
                    venue=VENUE,
                )
                for n in range(first, last + 1)
            ]
        },
        marks={
            key: [
                MarkSnapshot(MARKET, bar(n) + 500, px, px, VENUE)
                for n in range(first, last + 1)
            ]
        },
    )


# The researcher-written source (untrusted): open once, position-aware.
LONG_SOURCE = """
from flint.strategy import Strategy
from flint.core.models import Signal


class HoldLong(Strategy):
    params = {"venue": "hyperliquid", "notional_usd": 1000.0}

    def on_candle(self, candle, history, ctx):
        if ctx.position(candle.market, self.params["venue"]) is None:
            return Signal.long(candle.market, self.params["venue"],
                               size_usd=self.params["notional_usd"])
        return []
"""

BROKEN_SOURCE = """
import socket  # forbidden — fails the static screen

class Evil:
    pass
"""

TICK_SOURCE = """
from flint.strategy.tick import TickStrategy


class Scalper(TickStrategy):
    def on_trade(self, trade, ctx):
        return []
"""

NO_STRATEGY_SOURCE = """
def features(frame):
    return [c.close for c in frame]
"""


def _start(store, run_id, *, lake, source=LONG_SOURCE):
    return start_paper_source(
        TenantContext.local(),
        user_data=store,
        run_id=run_id,
        source=source,
        market=MARKET,
        resolution_s=HOUR_S,
        gap_source=lake,
        slippage_baseline=SlippageBaseline(0.0, 1.0),
    )


def _fills(store, run_id):
    events = EventLog(store, TenantContext.local(), run_id).read()
    return [e for e in events if e.kind == FILL]


# --- the pre-run gate ---------------------------------------------------------


def test_start_paper_source_rejects_invalid_source_with_the_validation_report():
    store = InMemoryUserData()
    with pytest.raises(SourceValidationError) as err:
        _start(store, "bad-1", lake=_lake(0, 3), source=BROKEN_SOURCE)

    payload = err.value.to_payload()["error"]
    assert payload["code"] == "validation"
    assert payload["validation"]["stage"] == "screen"
    assert payload["validation"]["screen_violations"]  # line-precise, structured
    # Nothing was persisted for the rejected start.
    assert store.list_runs(TenantContext.local()) == []


def test_start_paper_source_rejects_a_tick_native_strategy():
    with pytest.raises(ValidationError, match="bar lane only"):
        _start(InMemoryUserData(), "bad-2", lake=_lake(0, 3), source=TICK_SOURCE)


def test_start_paper_source_rejects_source_with_no_strategy_subclass():
    with pytest.raises(ValidationError, match="no Strategy subclass"):
        _start(InMemoryUserData(), "bad-3", lake=_lake(0, 3), source=NO_STRATEGY_SOURCE)


# --- end-to-end: sandboxed steps, warm-started -------------------------------


def test_sandboxed_paper_session_fills_and_carries_state_across_steps():
    store = InMemoryUserData()
    sess = _start(store, "src-1", lake=_lake(0, 9))
    assert sess.validation is not None and sess.validation.valid

    sess.catch_up(bar(1))  # anchor tick
    result = sess.catch_up(bar(4))  # bars 1..3 — the long fills at bar 2's open
    assert result.processed == 3
    assert result.final_state.position(VENUE, MARKET) is not None
    assert len(_fills(store, "src-1")) == 1

    # The next step's child warm-starts from the persisted rows: the strategy
    # (rebuilt from source) sees the open position and does not re-enter.
    again = sess.catch_up(bar(7))
    assert again.processed == 3
    assert len(_fills(store, "src-1")) == 1


def test_resume_paper_source_restarts_without_double_fill():
    store = InMemoryUserData()
    s1 = _start(store, "src-2", lake=_lake(0, 9))
    s1.catch_up(bar(1))
    s1.catch_up(bar(4))
    assert len(_fills(store, "src-2")) == 1

    # KILL + RESTART: a brand-new session from the same store and source.
    s2 = resume_paper_source(
        TenantContext.local(),
        user_data=store,
        run_id="src-2",
        source=LONG_SOURCE,
        market=MARKET,
        resolution_s=HOUR_S,
        gap_source=_lake(0, 9),
        slippage_baseline=SlippageBaseline(0.0, 1.0),
    )
    result = s2.catch_up(bar(8))  # bars 4..7
    assert result.processed == 4
    assert len(_fills(store, "src-2")) == 1
    assert result.final_state.position(VENUE, MARKET) is not None


def test_resume_paper_source_is_tenant_scoped():
    store = InMemoryUserData()
    s1 = _start(store, "owned", lake=_lake(0, 3))
    s1.catch_up(bar(1))

    with pytest.raises(KeyError):
        resume_paper_source(
            TenantContext(tenant_id="intruder"),
            user_data=store,
            run_id="owned",
            source=LONG_SOURCE,
            market=MARKET,
            resolution_s=HOUR_S,
            gap_source=_lake(0, 3),
        )


# --- parity: the sandboxed path is the same engine loop ----------------------


class _HoldLong(Strategy):
    params = {"venue": VENUE, "notional_usd": 1000.0}

    def on_candle(self, candle, history, ctx):
        if ctx.position(candle.market, self.params["venue"]) is None:
            return Signal.long(
                candle.market,
                self.params["venue"],
                size_usd=self.params["notional_usd"],
            )
        return []


def test_sandboxed_steps_match_the_in_process_template_session_bit_for_bit():
    tenant = TenantContext.local()
    run_id = "par-1"

    in_proc = InMemoryUserData()
    spec = TemplateSpec(
        name="_hold_long", strategy_cls=_HoldLong, summary="test", category="technical"
    )
    template_sess = PaperSession.create(
        tenant=tenant,
        store=in_proc,
        run_id=run_id,
        template=spec,
        market=MARKET,
        resolution_s=HOUR_S,
        gap_source=_lake(0, 9),
        initial_capital="100000",
        slippage_baseline=SlippageBaseline(0.0, 1.0),
    )
    sandboxed = InMemoryUserData()
    source_sess = _start(sandboxed, run_id, lake=_lake(0, 9))

    for sess in (template_sess, source_sess):
        sess.catch_up(bar(1))
        sess.catch_up(bar(4))
        sess.catch_up(bar(7))

    # The event streams — the authoritative run history — are identical rows.
    assert sandboxed.load_events(tenant, run_id) == in_proc.load_events(tenant, run_id)
