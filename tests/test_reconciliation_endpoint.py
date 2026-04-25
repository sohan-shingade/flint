"""D-1.4-ui — reconciliation endpoint test.

Asserts that POST /api/v1/paper/{id}/reconciliation accepts a multipart
CSV, calls scripts.reconcile_fills.reconcile, and returns counts + bps
deltas. Schema-error path also covered (400, not 500).
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Force a fresh DuckDB per test so live_fills is empty by default.
    db_path = tmp_path / "test_reconcile.duckdb"
    monkeypatch.setenv("FLINT_DB_PATH", str(db_path))

    # Use TestClient as a context manager so the FastAPI lifespan runs
    # and `app.state.store` is initialized before the request lands.
    from flint.api.main import app
    with TestClient(app) as c:
        yield c


def _csv_bytes(rows: list[dict]) -> bytes:
    header = "ts_epoch_s,market,venue,side,size,price,fee,order_id\n"
    body = "\n".join(
        f"{r['ts']},{r['market']},{r['venue']},{r['side']},{r['size']},"
        f"{r['price']},{r.get('fee', 0)},{r.get('order_id', '')}"
        for r in rows
    )
    return (header + body + "\n").encode("utf-8")


class TestPostReconciliation:
    def test_no_engine_fills_returns_venue_only_count(self, client):
        csv = _csv_bytes([
            dict(ts=1700000000, market="SOL-PERP", venue="drift",
                 side="long", size=1.0, price=150.0),
            dict(ts=1700000060, market="SOL-PERP", venue="drift",
                 side="short", size=1.0, price=151.0),
        ])
        r = client.post(
            "/api/v1/paper/test-session-no-engine/reconciliation",
            files={"file": ("fills.csv", csv, "text/csv")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["engine_count"] == 0
        assert body["venue_count"] == 2
        assert body["venue_only_count"] == 2
        assert body["match_count"] == 0

    def test_schema_error_returns_400(self, client):
        # Missing required columns "venue" and "size"
        bad = b"ts_epoch_s,market,side,price\n1700000000,SOL-PERP,long,150.0\n"
        r = client.post(
            "/api/v1/paper/test-session/reconciliation",
            files={"file": ("bad.csv", bad, "text/csv")},
        )
        assert r.status_code == 400
        assert "missing required columns" in r.json()["detail"].lower()

    def test_oversized_upload_rejected(self, client):
        big = io.BytesIO(b"a" * (11 * 1024 * 1024))  # 11 MB > 10 MB cap
        r = client.post(
            "/api/v1/paper/test-session/reconciliation",
            files={"file": ("big.csv", big, "text/csv")},
        )
        assert r.status_code == 400
        assert "exceeds" in r.json()["detail"].lower()

    def test_non_utf8_rejected(self, client):
        # Latin-1 byte that's not valid UTF-8 (0xff before any text)
        broken = b"\xff\xfets_epoch_s,market,venue,side,size,price\n"
        r = client.post(
            "/api/v1/paper/test-session/reconciliation",
            files={"file": ("bad.csv", broken, "text/csv")},
        )
        assert r.status_code == 400
        assert "utf-8" in r.json()["detail"].lower()


class TestReconcileLogicEndToEnd:
    def test_match_with_small_ts_drift(self):
        from scripts.reconcile_fills import Fill, reconcile

        engine = [
            Fill(ts=1700000010, market="SOL-PERP", venue="drift",
                 side="long", size=1.0, price=150.0, source="engine"),
        ]
        venue = [
            Fill(ts=1700000005, market="SOL-PERP", venue="drift",
                 side="long", size=1.0, price=150.5, source="venue"),
        ]
        result = reconcile(engine, venue, ts_window_s=60)
        assert result["match_count"] == 1
        assert result["engine_only_count"] == 0
        assert result["venue_only_count"] == 0
        assert result["price_bps_p50"] > 0  # half a buck on $150 ≈ 33 bps

    def test_no_match_outside_window(self):
        from scripts.reconcile_fills import Fill, reconcile

        engine = [
            Fill(ts=1700000000, market="SOL-PERP", venue="drift",
                 side="long", size=1.0, price=150.0, source="engine"),
        ]
        venue = [
            Fill(ts=1700001000, market="SOL-PERP", venue="drift",
                 side="long", size=1.0, price=150.0, source="venue"),
        ]
        result = reconcile(engine, venue, ts_window_s=60)
        assert result["match_count"] == 0
        assert result["engine_only_count"] == 1
        assert result["venue_only_count"] == 1
