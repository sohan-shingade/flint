"""The served web UI — token injection, SPA fallback, /api fencing (§12).

``create_app`` mounts the built SPA from ``ui/dist`` when it exists (override:
``FLINT_UI_DIST``). The index page is served with this session's bearer token
injected as ``window.__FLINT_TOKEN__`` — the same global the UI's
``getToken()`` reads first — so a localhost browser is authenticated without a
prompt. Assets are byte-for-byte; unknown non-``/api`` paths fall back to the
index (SPA routing); ``/api`` never falls back to HTML.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flint.api import create_app

TOKEN = "test-token"
INDEX = "<!doctype html><html><head><title>t</title></head><body></body></html>"


@pytest.fixture()
def dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX, encoding="utf-8")
    (d / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    monkeypatch.setenv("FLINT_UI_DIST", str(d))
    return d


def _client() -> TestClient:
    return TestClient(create_app(token=TOKEN))


def test_index_carries_the_session_token(dist: Path) -> None:
    resp = _client().get("/")
    assert resp.status_code == 200
    assert f'window.__FLINT_TOKEN__ = "{TOKEN}"' in resp.text
    assert "</head>" in resp.text  # injection kept the document intact


def test_spa_routes_fall_back_to_the_index(dist: Path) -> None:
    resp = _client().get("/funding")
    assert resp.status_code == 200
    assert TOKEN in resp.text  # the fallback is the injected index, not a 404


def test_assets_are_served_verbatim(dist: Path) -> None:
    resp = _client().get("/assets/app.js")
    assert resp.status_code == 200
    assert resp.text == "console.log(1)"
    assert TOKEN not in resp.text


def test_api_paths_never_fall_back_to_html(dist: Path) -> None:
    resp = _client().get("/api/v1/nonexistent", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_api_routes_still_require_the_token(dist: Path) -> None:
    resp = _client().get("/api/v1/runs")
    assert resp.status_code == 401


def test_traversal_stays_inside_dist(dist: Path) -> None:
    # a path that resolves outside dist must not leak the file — it is treated
    # as an SPA route and answered with the index page.
    resp = _client().get("/..%2Foutside.txt")
    assert resp.status_code == 200
    assert "secret" not in resp.text


def test_without_a_dist_the_server_stays_api_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FLINT_UI_DIST", str(tmp_path / "missing"))
    resp = _client().get("/")
    assert resp.status_code == 404
