"""Red-to-green regression tests for bugs surfaced by the 2026-04-24 smoke run.

Each test class captures one bug that was live on the freshly-deployed
server and is now fixed. Tests pass on current code; reverting the
corresponding fix makes them red.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# BUG-1 — /api/v1/live/sessions returned DuckDB Binder Error
# ---------------------------------------------------------------------------

class TestLiveSessionsColumnNameFix:
    """
    Symptom pre-fix:
      GET /api/v1/live/sessions → 200
      body = {"error": "Binder Error: Referenced column \\"strategy\\"
              not found in FROM clause! Candidate bindings:
              \\"strategy_name\\", ..."}

    Root cause: FlintStore.list_live_sessions (shipped in Phase 2 T2.2)
    used SELECT column `strategy`; actual live_sessions table column is
    `strategy_name`.

    Fix: flint/store.py — rename `strategy` → `strategy_name` in the
    SELECT list (Python-side dict key stays `strategy` for UI contract).
    """

    def test_live_sessions_returns_list_not_error(self):
        from flint.api.main import app

        # `with TestClient(app):` triggers lifespan so store is attached.
        with TestClient(app) as client:
            r = client.get("/api/v1/live/sessions")
        assert r.status_code == 200
        body = r.json()
        assert "sessions" in body, f"expected sessions key, got {body}"
        assert "error" not in body, f"unexpected error: {body.get('error')}"
        assert isinstance(body["sessions"], list)


# ---------------------------------------------------------------------------
# BUG-2 — engine_used + fallback_reason absent from backtest results envelope
# ---------------------------------------------------------------------------

class TestEngineUsedTelemetryInAPIResponse:
    """
    Symptom pre-fix:
      POST /api/v1/backtest/run → run_id
      GET  /api/v1/backtest/{run_id}/results → body.results has 20 keys
        but neither 'engine_used' nor 'fallback_reason'.

    Root cause: BacktestResult (Phase 3 T3.2) carries engine_used +
    fallback_reason, but the tearsheet.to_dict() serializer in
    flint/api/routes/backtest.py does not include them when building
    the API response.

    Fix: flint/api/routes/backtest.py — after ts_dict = tearsheet.to_dict(),
    explicitly set ts_dict['engine_used'] and ts_dict['fallback_reason']
    from the BacktestResult instance.
    """

    def test_engine_used_key_present_in_results(self):
        from flint.api.main import app

        with TestClient(app) as client:
            # Any small run — we only inspect the response envelope shape.
            # Use a range that local SOL-PERP data covers with enough bars
            # to produce finite Sharpe / drawdown (10 bars can yield NaN).
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            end_ts = int(now.timestamp())
            start_ts = int((now - timedelta(days=14)).timestamp())
            r = client.post("/api/v1/backtest/run", json={
                "strategy": "ma_crossover",
                "market": "SOL-PERP",
                "start_ts": start_ts,
                "end_ts": end_ts,
                "initial_capital": 10_000,
                "fee_rate": 0.0005,
                "resolution_s": 3600,
                "params": {"fast_period": 5, "slow_period": 20},
            })
            assert r.status_code == 200, r.text
            run_id = r.json()["id"]

            # Poll until complete (short run).
            import time
            for _ in range(40):
                s = client.get(f"/api/v1/backtest/{run_id}/status").json()
                if s.get("status") in {"complete", "failed", "cancelled"}:
                    break
                time.sleep(0.2)

            body = client.get(f"/api/v1/backtest/{run_id}/results").json()

        assert body.get("status") == "complete", (
            f"backtest did not complete: {body}"
        )
        results = body.get("results") or {}
        assert "engine_used" in results, (
            f"engine_used missing from results; keys={sorted(results)}"
        )
        assert "fallback_reason" in results, (
            f"fallback_reason missing from results; keys={sorted(results)}"
        )
        # engine_used must be one of the known tags.
        assert results["engine_used"] in {"rust", "python"}


# ---------------------------------------------------------------------------
# BUG-3 — Version mismatch between pyproject, API, and UI
# ---------------------------------------------------------------------------

class TestVersionConsistency:
    """
    Symptom pre-fix:
      - pyproject.toml: flint-trading 1.3.1
      - /api/v1/capabilities → version: "0.1.0"
      - /api/v1/system/status → version: "0.1.0" (or 0.2.0 locally)
      - UI footer hardcoded: "FLINT v0.3.0"

    Root cause: system.py:_get_version calls pkg_version("flint"), but
    the package distribution name is "flint-trading". It finds a stale
    separate `flint` egg-info and returns that instead.

    Fix: read the version straight from pyproject.toml with
    importlib.metadata fallbacks in correct order (flint-trading first).
    """

    def _pyproject_version(self) -> str:
        try:
            import tomllib  # py3.11+
        except ImportError:
            import tomli as tomllib
        from pathlib import Path
        root = Path(__file__).parent.parent
        with open(root / "pyproject.toml", "rb") as f:
            return tomllib.load(f)["project"]["version"]

    def test_api_version_matches_pyproject(self):
        from flint.api.main import app

        pyproj = self._pyproject_version()
        with TestClient(app) as client:
            caps = client.get("/api/v1/capabilities").json()
            sysstat = client.get("/api/v1/system/status").json()

        assert caps["version"] == pyproj, (
            f"/api/v1/capabilities version={caps['version']!r} != "
            f"pyproject {pyproj!r}"
        )
        assert sysstat["version"] == pyproj, (
            f"/api/v1/system/status version={sysstat['version']!r} != "
            f"pyproject {pyproj!r}"
        )


# ---------------------------------------------------------------------------
# BUG-4 — Monaco editor pulled from cdn.jsdelivr.net
# ---------------------------------------------------------------------------

class TestMonacoLoadsLocallyNotFromCDN:
    """
    Symptom pre-fix:
      Opening /backtest in the browser triggered ~14 GET requests to
      cdn.jsdelivr.net/npm/monaco-editor@0.55.1/... (loader.js,
      editor.main.js, language chunks, workers). Verified via Playwright
      network tracing on 2026-04-24.

    This broke:
      - "Local-first, nothing leaves your machine" (README + home page)
      - Offline operation
      - Privacy posture

    Root cause: @monaco-editor/react defaults to a CDN loader when
    `loader.config({ monaco })` is not called. Default behavior is to
    fetch Monaco from jsdelivr at first mount.

    Fix: ui/src/components/CodeEditor.tsx imports monaco-editor as a
    module and calls loader.config({ monaco }). Vite bundles Monaco into
    our own JS so nothing leaves the machine.

    This is a source-level assertion because testing browser network
    behavior from pytest is out of scope. Playwright verified
    cdnCount == 0 after the fix on 2026-04-24.
    """

    def test_code_editor_calls_loader_config_with_monaco(self):
        from pathlib import Path
        root = Path(__file__).parent.parent
        path = root / "ui" / "src" / "components" / "CodeEditor.tsx"
        text = path.read_text(encoding="utf-8")
        assert "loader.config" in text, (
            "CodeEditor.tsx must call loader.config() to bind @monaco-editor/react "
            "to the locally-bundled monaco-editor; without it, Monaco assets "
            "load from cdn.jsdelivr.net."
        )
        assert "import { loader }" in text or "from '@monaco-editor/react'" in text
        assert "import * as monaco from 'monaco-editor'" in text, (
            "CodeEditor.tsx must import monaco-editor so Vite bundles it into the "
            "local JS output."
        )
