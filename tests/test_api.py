"""REST + WebSocket API surface — TestClient, fully mocked, no network (§12).

Every endpoint goes through ``services/`` only. Security is exercised (the
per-session bearer token + Origin check on the code-executing server), the
uniform error schema is asserted, and the funding hard gate surfaces as a
structured ``rejected`` body rather than an HTTP error (§19.1). Data is
hand-authored recorded-fragment fixtures (D26).
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from flint.adapters import InMemoryUserData
from flint.api import create_app
from flint.data import DataManager
from flint.data.ranges import Kind
from flint.data.sources import InMemoryCacheSource
from flint.ports import RunRecord, TenantContext

TOKEN = "test-token"
VENUE = "hyperliquid"
MARKET = "SOL-PERP"
T0 = 1_700_000_000_000
H = 3_600_000
N = 6
COVERED_END = T0 + (N - 1) * H + 60_000 + 1


def _data() -> DataManager:
    n = N
    candles = pa.table(
        {
            "ts": [T0 + i * H for i in range(n)],
            "open": [100.0] * n,
            "high": [100.0] * n,
            "low": [100.0] * n,
            "close": [100.0] * n,
            "volume": [1000.0] * n,
            "market": [MARKET] * n,
            "resolution_s": [3600] * n,
            "venue": [VENUE] * n,
        }
    )
    ts_pred = [T0 + i * H for i in range(n)]
    ts_fin = [T0 + i * H + 60_000 for i in range(n)]
    funding = pa.table(
        {
            "ts": ts_pred + ts_fin,
            "rate_hourly": [0.001] * (2 * n),
            "interval_s": [3600] * (2 * n),
            "price_basis": ["oracle"] * (2 * n),
            "rate_type": ["predicted"] * n + ["final"] * n,
            "venue": [VENUE] * (2 * n),
            "market": [MARKET] * (2 * n),
        }
    )
    cache = InMemoryCacheSource()
    cache.store(VENUE, MARKET, Kind.CANDLES, candles)
    cache.store(VENUE, MARKET, Kind.FUNDING, funding)
    return DataManager(sources=[cache])


def _client(user_data=None) -> TestClient:
    app = create_app(user_data=user_data or InMemoryUserData(), data=_data(), token=TOKEN)
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


# -- happy path --------------------------------------------------------------


def test_submit_status_result_flow():
    client = _client()
    resp = client.post("/api/v1/backtests", json=_body(), headers=_auth())
    assert resp.status_code == 201
    run_id = resp.json()["run_id"]

    status = client.get(f"/api/v1/backtests/{run_id}/status", headers=_auth()).json()
    assert status["state"] == "done"
    assert status["progress_pct"] == 100

    result = client.get(f"/api/v1/backtests/{run_id}", headers=_auth())
    assert result.status_code == 200
    summary = result.json()
    assert summary["verdict"] == "ok"
    assert "sharpe" in summary["metrics"]
    assert len(summary["equity_curve"]) == N


def test_result_carries_the_per_run_timing_breakdown():
    # §19.2: the backtest result exposes a phase timing breakdown through the API so
    # "why is my backtest slow" is answerable from the output the surface returns.
    client = _client()
    run_id = client.post("/api/v1/backtests", json=_body(), headers=_auth()).json()["run_id"]
    summary = client.get(f"/api/v1/backtests/{run_id}", headers=_auth()).json()
    assert set(summary["timing"]) == {
        "data_fetch_ms",
        "input_build_ms",
        "engine_run_ms",
        "report_ms",
    }


def test_result_is_404_for_unknown_run():
    client = _client()
    resp = client.get("/api/v1/backtests/nope", headers=_auth())
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_idempotency_key_returns_the_same_run():
    client = _client()
    body = _body(idempotency_key="abc")
    first = client.post("/api/v1/backtests", json=body, headers=_auth()).json()["run_id"]
    second = client.post("/api/v1/backtests", json=body, headers=_auth()).json()["run_id"]
    assert first == second


def test_cancel_is_idempotent_on_a_finished_run():
    client = _client()
    run_id = client.post("/api/v1/backtests", json=_body(), headers=_auth()).json()["run_id"]
    resp = client.post(f"/api/v1/backtests/{run_id}/cancel", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["state"] == "done"  # already terminal → unchanged


# -- funding hard gate as data, not an error ---------------------------------


def test_funding_gate_surfaces_as_a_rejected_result_body():
    client = _client()
    body = _body(range={"start_ms": T0, "end_ms": COVERED_END + H})
    run_id = client.post("/api/v1/backtests", json=body, headers=_auth()).json()["run_id"]
    # The job completed (200, not an HTTP error) with a structured rejected verdict.
    summary = client.get(f"/api/v1/backtests/{run_id}", headers=_auth()).json()
    assert summary["verdict"] == "rejected"
    assert summary["rejected"]["code"] == "funding_gap"
    assert summary["rejected"]["missing"]


def test_unknown_template_is_a_400_with_the_uniform_error_schema():
    client = _client()
    resp = client.post("/api/v1/backtests", json=_body(strategy="nope"), headers=_auth())
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "validation"
    assert err["hint"]


def test_malformed_body_is_a_400_validation_error():
    client = _client()
    resp = client.post("/api/v1/backtests", json={"strategy": "x"}, headers=_auth())
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation"


# -- security ----------------------------------------------------------------


def test_missing_token_is_401():
    client = _client()
    resp = client.post("/api/v1/backtests", json=_body())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_disallowed_origin_is_403():
    client = _client()
    resp = client.post(
        "/api/v1/backtests",
        json=_body(),
        headers={**_auth(), "Origin": "http://evil.example"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden_origin"


def test_localhost_origin_is_allowed():
    client = _client()
    resp = client.post(
        "/api/v1/backtests",
        json=_body(),
        headers={**_auth(), "Origin": "http://localhost:5173"},
    )
    assert resp.status_code == 201


# -- run library, data, alerts -----------------------------------------------


def test_runs_lists_the_completed_backtest():
    ud = InMemoryUserData()
    client = _client(ud)
    client.post("/api/v1/backtests", json=_body(), headers=_auth())
    runs = client.get("/api/v1/runs", headers=_auth()).json()["runs"]
    assert len(runs) == 1
    assert runs[0]["strategy"] == "funding_harvest"
    assert "sharpe" in runs[0]["metrics"]


def test_runs_compare_returns_a_metric_table():
    ud = InMemoryUserData()
    client = _client(ud)
    r1 = client.post("/api/v1/backtests", json=_body(), headers=_auth()).json()["run_id"]
    b2 = _body(range={"start_ms": T0, "end_ms": T0 + 3 * H + 60_001})
    r2 = client.post("/api/v1/backtests", json=b2, headers=_auth()).json()["run_id"]
    resp = client.get(
        "/api/v1/runs/compare",
        params=[("run_id", r1), ("run_id", r2)],
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_ids"] == [r1, r2]
    assert "sharpe" in body["metrics"]  # one row per metric, one column per run


def test_data_coverage_reports_per_kind_ranges():
    client = _client()
    resp = client.get(
        "/api/v1/data/coverage", params={"market": MARKET, "venue": VENUE}, headers=_auth()
    )
    assert resp.status_code == 200
    cov = resp.json()["coverage"]
    assert cov["candles"] is not None
    assert cov["funding"] is not None
    assert cov["oi"] is None  # nothing stored → honestly absent


def test_lab_funding_returns_real_carry_and_degrades_missing_venues():
    client = _client()
    resp = client.get(
        "/api/v1/lab/funding",
        params={"market": [MARKET], "venue": [VENUE, "binance"]},
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.json()
    # hyperliquid has predicted funding in the fixture → a real cell with carry.
    hl = body["cells"][f"{VENUE}/{MARKET}"]
    assert hl is not None
    assert hl["n"] > 0
    assert hl["annualized"] is not None
    # binance has no funding — a lab leg degrades to null, never a rejection.
    assert body["cells"][f"binance/{MARKET}"] is None
    assert "effective_range" in body


def test_data_pull_warms_the_cache():
    client = _client()
    resp = client.post(
        "/api/v1/data/pull",
        json={
            "market": MARKET,
            "venues": [VENUE],
            "kind": "candles",
            "range": {"start_ms": T0, "end_ms": COVERED_END},
        },
        headers=_auth(),
    )
    assert resp.status_code == 200
    assert resp.json()["rows"]


def test_data_pull_unknown_kind_is_400():
    client = _client()
    resp = client.post(
        "/api/v1/data/pull",
        json={
            "market": MARKET,
            "venues": [VENUE],
            "kind": "bogus",
            "range": {"start_ms": T0, "end_ms": COVERED_END},
        },
        headers=_auth(),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation"


def test_create_alert_and_reject_unknown_rule():
    client = _client()
    ok = client.post(
        "/api/v1/alerts",
        json={"rule": "liq_distance", "threshold": 10.0, "channel": "webhook"},
        headers=_auth(),
    )
    assert ok.status_code == 201
    assert ok.json()["rule"] == "liq_distance"

    bad = client.post("/api/v1/alerts", json={"rule": "nope"}, headers=_auth())
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "validation"


# -- websocket paper monitor -------------------------------------------------


def test_paper_ws_streams_a_snapshot():
    ud = InMemoryUserData()
    ud.save_run(
        TenantContext.local(),
        RunRecord(
            run_id="p1",
            kind="paper",
            status="running",
            summary={"final_equity": "100000", "liq_distances_pct": {MARKET: 42.0}},
        ),
    )
    client = _client(ud)
    with client.websocket_connect(f"/api/v1/paper/p1/stream?token={TOKEN}") as ws:
        snap = ws.receive_json()
    assert snap["run_id"] == "p1"
    assert snap["kind"] == "paper"
    assert snap["liq_distances_pct"][MARKET] == 42.0


def test_paper_ws_rejects_a_bad_token():
    client = _client()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/paper/p1/stream?token=wrong") as ws:
            ws.receive_json()


# -- live kill switch (D20) --------------------------------------------------


class _FakeSecrets:
    def get_secret(self, tenant, name):
        return "0xKEY"


class _FakeLiveClient:
    def place_order(self, order):
        from flint.venues.hyperliquid import ExecReport

        return ExecReport(
            client_order_id=order.client_order_id,
            market=order.market,
            side=order.side,
            status="filled",
            filled_size=order.size,
            avg_price=order.price or 100.0,
            fee=0.0,
        )

    def cancel_all(self, market=None):
        return ["oid-1"]

    def clearinghouse_state(self):
        from flint.venues.hyperliquid import ClearinghouseState

        return ClearinghouseState()


def _live_client(user_data):
    app = create_app(
        user_data=user_data,
        data=_data(),
        token=TOKEN,
        secrets=_FakeSecrets(),
        live_client_factory=lambda key: _FakeLiveClient(),
    )
    return TestClient(app)


def test_live_stop_route_stops_a_running_run():
    from flint.live import LiveCaps, LiveExecutor

    ud = InMemoryUserData()
    tenant = TenantContext.local()
    LiveExecutor.start(
        tenant=tenant,
        store=ud,
        secrets=_FakeSecrets(),
        run_id="live-1",
        market=MARKET,
        caps=LiveCaps(max_position_usd=1000.0),
        client_factory=lambda key: _FakeLiveClient(),
    )
    client = _live_client(ud)
    resp = client.post("/api/v1/live/live-1/stop", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["reason"] == "operator"
    assert ud.load_run(tenant, "live-1").status == "stopped"


def test_live_stop_route_404_for_unknown_run():
    ud = InMemoryUserData()
    client = _live_client(ud)
    resp = client.post("/api/v1/live/ghost/stop", headers=_auth())
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_live_stop_route_requires_the_token():
    ud = InMemoryUserData()
    client = _live_client(ud)
    resp = client.post("/api/v1/live/live-1/stop")  # no bearer token
    assert resp.status_code == 401
