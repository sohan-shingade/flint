"""Tests for GET /api/v1/capabilities — Phase 4 T4.6."""
from __future__ import annotations

from fastapi.testclient import TestClient

from flint.api.main import app


def _client():
    return TestClient(app)


class TestCapabilitiesRoute:
    def test_returns_expected_top_level_keys(self):
        r = _client().get("/api/v1/capabilities")
        assert r.status_code == 200
        body = r.json()
        for k in ("version", "api_version", "engine", "features", "limits"):
            assert k in body, f"missing top-level key {k!r}"

    def test_engine_section_shape(self):
        body = _client().get("/api/v1/capabilities").json()
        engine = body["engine"]
        assert "rust_available" in engine
        assert isinstance(engine["rust_available"], bool)
        assert "rust_capabilities" in engine
        assert isinstance(engine["rust_capabilities"], dict)

    def test_features_are_bools(self):
        body = _client().get("/api/v1/capabilities").json()
        for k, v in body["features"].items():
            assert isinstance(v, bool), f"features.{k} is {type(v).__name__}"

    def test_reports_live_trading_api_false(self):
        """Phase 6.5 hasn't landed — UI must see this flag so the deploy
        button stays hidden."""
        body = _client().get("/api/v1/capabilities").json()
        assert body["features"]["live_trading_api"] is False

    def test_limits_are_integers(self):
        body = _client().get("/api/v1/capabilities").json()
        for k, v in body["limits"].items():
            assert isinstance(v, int), f"limits.{k} is {type(v).__name__}"
