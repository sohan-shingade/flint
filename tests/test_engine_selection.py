"""N7 — engine selection reaches every entry point; the sandbox runs Nautilus (§6.0, D29).

Covers the ``engine`` field end-to-end: enum validation at the API surface and
the services front doors (unknown engine → the uniform §19.1 validation error,
never a stack trace), resolved-engine + Nautilus-pin stamping into the run
summary/manifest (§19.4/§19.6 attribution), and the headline D25 path — a
trivial user-source strategy walked by the **Nautilus** engine inside the OS
sandbox via ``run_backtest_source(engine="nautilus")`` (§13.2 on the new core).

Legacy-lane tests run in every environment; Nautilus tests guard with a
function-level ``importorskip`` so this module still collects and the
legacy/validation coverage still runs without the ``nautilus`` extra. Inputs
are hand-authored (D26).
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from flint.adapters import InMemoryUserData
from flint.api import create_app
from flint.data import DataManager
from flint.data.ranges import Kind
from flint.data.sources import InMemoryCacheSource
from flint.engine.select import UnknownEngineError, resolve_engine_name
from flint.ports import TenantContext
from flint.services import ValidationError, run_backtest_source
from flint.services.backtest import BacktestRequest, run_backtest

ALICE = TenantContext(tenant_id="alice")
TOKEN = "test-token"
VENUE = "hyperliquid"
MARKET = "SOL-PERP"
T0 = 1_700_000_000_000
H = 3_600_000
N = 6
COVERED_END = T0 + (N - 1) * H + 60_000 + 1

SOURCE = """
from flint.strategy import Strategy
from flint.core.models import Signal

class MomoStrat(Strategy):
    def on_candle(self, candle, history, ctx):
        if len(history) < 2:
            return []
        if candle.close > history[-2].close:
            return [Signal.long(candle.market, candle.venue, size_usd=2000.0)]
        return [Signal(market=candle.market, venue=candle.venue, action="close")]
"""


def _data() -> DataManager:
    closes = [100.0, 101.0, 102.0, 101.0, 103.0, 104.0][:N]
    candles = pa.table(
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
    )
    ts_pred = [T0 + i * H for i in range(N)]
    ts_fin = [T0 + i * H + 60_000 for i in range(N)]
    funding = pa.table(
        {
            "ts": ts_pred + ts_fin,
            "rate_hourly": [0.001] * (2 * N),
            "interval_s": [3600] * (2 * N),
            "price_basis": ["oracle"] * (2 * N),
            "rate_type": ["predicted"] * N + ["final"] * N,
            "venue": [VENUE] * (2 * N),
            "market": [MARKET] * (2 * N),
        }
    )
    cache = InMemoryCacheSource()
    cache.store(VENUE, MARKET, Kind.CANDLES, candles)
    cache.store(VENUE, MARKET, Kind.FUNDING, funding)
    return DataManager(sources=[cache])


def _request(**over) -> BacktestRequest:
    kw = dict(
        run_id="r1",
        strategy="funding_harvest",
        universe=(MARKET,),
        venues=(VENUE,),
        start_ms=T0,
        end_ms=COVERED_END,
        resolution_s=3600,
    )
    kw.update(over)
    return BacktestRequest(**kw)


# -- select seam (pure string resolution, no heavy imports) -------------------


def test_resolve_engine_name_maps_auto_to_legacy_until_the_n9_flip():
    assert resolve_engine_name("auto") == "legacy-bar"
    assert resolve_engine_name("legacy-bar") == "legacy-bar"
    assert resolve_engine_name("nautilus") == "nautilus"


def test_resolve_engine_name_rejects_unknown_names():
    with pytest.raises(UnknownEngineError):
        resolve_engine_name("zipline")


# -- services: uniform validation error (§19.1) --------------------------------


def test_run_backtest_unknown_engine_is_a_uniform_validation_error():
    ud = InMemoryUserData()
    with pytest.raises(ValidationError):
        run_backtest(ALICE, _request(engine="zipline"), user_data=ud, data=_data())
    # Rejected before anything ran: no dangling "running" head was persisted.
    with pytest.raises(KeyError):
        ud.load_run(ALICE, "r1")


def test_run_backtest_source_unknown_engine_is_a_uniform_validation_error():
    ud = InMemoryUserData()
    with pytest.raises(ValidationError):
        run_backtest_source(
            ALICE,
            source=SOURCE,
            run_id="u1",
            universe=(MARKET,),
            venues=(VENUE,),
            start_ms=T0,
            end_ms=COVERED_END,
            engine="zipline",
            user_data=ud,
            data=_data(),
        )
    with pytest.raises(KeyError):
        ud.load_run(ALICE, "u1")


# -- services: resolved-engine stamping (§19.4/§19.6) --------------------------


def test_template_run_stamps_the_resolved_engine_auto_is_legacy_bar():
    ud = InMemoryUserData()
    out = run_backtest(ALICE, _request(engine="auto"), user_data=ud, data=_data())
    assert out.verdict == "ok"
    # "auto" resolves to the legacy bar loop until the N9 flip — the summary
    # records what actually ran, never the request alias.
    assert out.summary["engine"] == "legacy-bar"
    assert "engine: legacy-bar" in out.summary["fidelity_lines"]
    assert "engine_versions" not in out.summary  # no extra pins behind legacy
    # The persisted Run-Library head carries the same attribution in its
    # manifest fidelity block (§19.6).
    record = ud.load_run(ALICE, "r1")
    assert "engine: legacy-bar" in record.summary["fidelity"]["lines"]


def test_source_run_stamps_the_resolved_engine_legacy_bar():
    ud = InMemoryUserData()
    out = run_backtest_source(
        ALICE,
        source=SOURCE,
        run_id="u-legacy",
        universe=(MARKET,),
        venues=(VENUE,),
        start_ms=T0,
        end_ms=COVERED_END,
        engine="legacy-bar",
        user_data=ud,
        data=_data(),
    )
    assert out.verdict == "ok"
    assert out.summary["engine"] == "legacy-bar"
    assert "engine: legacy-bar" in out.summary["fidelity_lines"]
    record = ud.load_run(ALICE, "u-legacy")
    assert "engine: legacy-bar" in record.summary["fidelity"]["lines"]


# -- API surface: enum-validated wire field ------------------------------------


def _client() -> TestClient:
    app = create_app(user_data=InMemoryUserData(), data=_data(), token=TOKEN)
    return TestClient(app)


def _auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


def _body(**over) -> dict:
    b = {
        "strategy": "funding_harvest",
        "universe": [MARKET],
        "venues": [VENUE],
        "range": {"start_ms": T0, "end_ms": COVERED_END},
        "resolution_s": 3600,
    }
    b.update(over)
    return b


def test_api_backtest_rejects_unknown_engine_with_uniform_validation_error():
    resp = _client().post(
        "/api/v1/backtests", json=_body(engine="zipline"), headers=_auth()
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation"


def test_api_source_backtest_rejects_unknown_engine_with_uniform_validation_error():
    body = {
        "source": SOURCE,
        "universe": [MARKET],
        "venues": [VENUE],
        "range": {"start_ms": T0, "end_ms": COVERED_END},
        "engine": "zipline",
    }
    resp = _client().post("/api/v1/backtests/source", json=body, headers=_auth())
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation"


def test_api_backtest_threads_engine_through_to_the_run_record():
    client = _client()
    run_id = client.post(
        "/api/v1/backtests", json=_body(engine="legacy-bar"), headers=_auth()
    ).json()["run_id"]
    summary = client.get(f"/api/v1/backtests/{run_id}", headers=_auth()).json()
    assert summary["engine"] == "legacy-bar"


# -- Nautilus lane (guarded per-test so the legacy coverage above always runs) --


def test_template_run_on_nautilus_stamps_engine_and_exact_pin():
    pytest.importorskip("nautilus_trader")
    from flint.engine.nautilus._compat import NAUTILUS_REQUIRED

    ud = InMemoryUserData()
    out = run_backtest(ALICE, _request(engine="nautilus"), user_data=ud, data=_data())
    assert out.verdict == "ok"
    assert out.summary["engine"] == "nautilus"
    # §19.4: the pinned Nautilus version is recorded alongside the engine so a
    # churn-induced numeric change is attributable, never silent.
    assert out.summary["engine_versions"] == {"nautilus_trader": NAUTILUS_REQUIRED}
    line = f"engine: nautilus (nautilus_trader=={NAUTILUS_REQUIRED})"
    assert line in out.summary["fidelity_lines"]
    record = ud.load_run(ALICE, "r1")
    assert line in record.summary["fidelity"]["lines"]


def test_source_backtest_runs_on_nautilus_inside_the_sandbox_end_to_end():
    """The N7 headline: §13.2 user source on the new core, contained by D25.

    A trivial user strategy goes through ``run_backtest_source`` with
    ``engine="nautilus"`` — validated, exec'd in the OS-isolated child, walked by
    the Nautilus engine (imported inside the child before the source runs), its
    event stream persisted on the trusted side, and the run head stamped with the
    resolved engine + exact pin.
    """
    pytest.importorskip("nautilus_trader")
    from flint.engine.nautilus._compat import NAUTILUS_REQUIRED

    ud = InMemoryUserData()
    out = run_backtest_source(
        ALICE,
        source=SOURCE,
        run_id="u-naut",
        universe=(MARKET,),
        venues=(VENUE,),
        start_ms=T0,
        end_ms=COVERED_END,
        engine="nautilus",
        user_data=ud,
        data=_data(),
    )
    assert out.verdict == "ok"
    assert out.summary["engine"] == "nautilus"
    assert out.summary["engine_versions"] == {"nautilus_trader": NAUTILUS_REQUIRED}
    line = f"engine: nautilus (nautilus_trader=={NAUTILUS_REQUIRED})"
    assert line in out.summary["fidelity_lines"]
    # A real engine walk produced a real trust report over the sandbox's events.
    assert out.summary["metrics"]["n_returns"] == N - 1
    # Durable, tenant-scoped head: source for reproduction + engine attribution.
    record = ud.load_run(ALICE, "u-naut")
    assert record.status == "done"
    assert record.summary["strategy_source"] == SOURCE
    assert line in record.summary["fidelity"]["lines"]
