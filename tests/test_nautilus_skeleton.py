"""N2 gate — a NoopStrategy runs end-to-end on Nautilus through the real EventLog.

The skeleton's acceptance test (§A10-N2): a hand-authored 10-bar :class:`EngineFeed`
driven through ``engine_for("nautilus")`` emits ``RUN_STARTED`` / per-bar
``EQUITY`` / ``RUN_FINISHED`` through the injected Flint ``EventLog``, the equity
line holds at initial capital (no trades), ``build_tearsheet`` folds it, and
``check_invariants`` passes. The run is also deterministic and its EQUITY payloads
are byte-identical to the legacy engine's on the same feed.

Gated on the ``nautilus`` extra (``importorskip``). Hand-authored inputs (D26).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytest.importorskip("nautilus_trader")

from flint.adapters import InMemoryUserData  # noqa: E402
from flint.core.models import Candle, FundingRate, MarkSnapshot  # noqa: E402
from flint.engine.api import EngineFeed, EngineRunSpec  # noqa: E402
from flint.engine.loop import EngineConfig, NoopStrategy  # noqa: E402
from flint.engine.portfolio import EventLog  # noqa: E402
from flint.engine.portfolio.events import EQUITY, RUN_FINISHED, RUN_STARTED  # noqa: E402
from flint.engine.select import engine_for  # noqa: E402
from flint.engine.tearsheet import build_tearsheet, check_invariants  # noqa: E402
from flint.ports import TenantContext  # noqa: E402
from flint.venues import HYPERLIQUID  # noqa: E402

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_040_000  # minute-aligned base
CAPITAL = "100000"
N_BARS = 10


def _feed() -> EngineFeed:
    candles = [
        Candle(
            ts=T0 + i * HOUR_MS, open=100.0, high=101.0, low=99.0, close=100.0,
            volume=1000.0, market=MARKET, resolution_s=HOUR_S, venue=VENUE,
        )
        for i in range(N_BARS)
    ]
    marks = {
        MARKET: [
            MarkSnapshot(market=MARKET, ts=T0 + i * HOUR_MS, mark_price=100.0, index_price=100.0, venue=VENUE)
            for i in range(N_BARS)
        ]
    }
    # A few predicted rows so the FlintFundingRate stream is exercised end-to-end.
    funding = {
        MARKET: [
            FundingRate(
                market=MARKET, ts=T0 + i * HOUR_MS + 12 * 60 * 1000, rate_hourly=0.001,
                interval_s=HOUR_S, price_basis="oracle", rate_type="predicted", venue=VENUE,
            )
            for i in range(3)
        ]
    }
    return EngineFeed(candles=candles, funding=funding, marks=marks)


def _spec() -> EngineRunSpec:
    return EngineRunSpec(
        config=EngineConfig(), venue_spec=HYPERLIQUID, initial_capital=CAPITAL,
        fund_venue=VENUE, mark_policy="recorded",
    )


def _run(run_id: str) -> tuple[EventLog, object]:
    log = EventLog(InMemoryUserData(), TenantContext.local(), run_id=run_id)
    state = engine_for("nautilus")().run(_feed(), NoopStrategy(), event_log=log, spec=_spec())
    return log, state


def test_noop_run_emits_run_started_ten_equity_run_finished():
    log, _ = _run("gate")
    events = log.read()
    kinds = [e.kind for e in events]
    assert kinds == [RUN_STARTED] + [EQUITY] * N_BARS + [RUN_FINISHED]
    assert events[0].payload["engine"] == "nautilus"


def test_equity_is_stamped_at_bar_start_and_constant_at_initial_capital():
    log, _ = _run("gate-equity")
    eq = [e for e in log.read() if e.kind == EQUITY]
    assert [e.ts for e in eq] == [T0 + i * HOUR_MS for i in range(N_BARS)]
    for e in eq:
        # No trades -> equity, cash all pin to initial capital; unrealized/funding 0.
        assert Decimal(e.payload["equity"]) == Decimal(CAPITAL)
        assert Decimal(e.payload["cash"]) == Decimal(CAPITAL)
        assert Decimal(e.payload["unrealized"]) == Decimal("0")
        assert Decimal(e.payload["accrued_funding"]) == Decimal("0")


def test_tearsheet_folds_and_invariants_pass():
    log, _ = _run("gate-tearsheet")
    ts = build_tearsheet(log.read())
    assert ts.net_pnl == Decimal("0")
    assert ts.fills == 0
    assert ts.orders_placed == 0
    assert ts.evaluated_start_ts == T0
    assert ts.evaluated_end_ts == T0 + (N_BARS - 1) * HOUR_MS
    check_invariants(ts, {})  # no orders — closes trivially


def test_run_returns_shadow_state_at_initial_capital():
    _, state = _run("gate-state")
    assert state.account(VENUE).cash == Decimal(CAPITAL)
    assert state.positions == {}


def test_run_is_deterministic():
    log_a, _ = _run("det-a")
    log_b, _ = _run("det-b")
    assert [e.to_row() for e in log_a.read()] == [e.to_row() for e in log_b.read()]


def test_equity_payloads_match_the_legacy_engine_byte_for_byte():
    # The recorder's per-bar EQUITY is the legacy _emit_equity payload, unchanged.
    naut_log, _ = _run("parity-naut")
    legacy_log = EventLog(InMemoryUserData(), TenantContext.local(), run_id="parity-legacy")
    engine_for("legacy-bar")().run(_feed(), NoopStrategy(), event_log=legacy_log, spec=_spec())
    naut_eq = [dict(e.payload) for e in naut_log.read() if e.kind == EQUITY]
    legacy_eq = [dict(e.payload) for e in legacy_log.read() if e.kind == EQUITY]
    assert naut_eq == legacy_eq
