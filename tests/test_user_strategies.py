"""Tests for user strategy CRUD API endpoints."""
import pytest
from fastapi.testclient import TestClient
from flint.api.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Use a temp directory for user strategies."""
    monkeypatch.setattr("flint.api.routes.user_strategies.STRATEGIES_DIR", tmp_path)
    return TestClient(app)


VALID_CODE = '''
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List

class TestStrat(Strategy):
    @property
    def name(self) -> str:
        return "test_strat"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        return Signal.HOLD

    def reset(self) -> None:
        pass
'''


def test_save_strategy(client):
    resp = client.post("/api/v1/user-strategies", json={"name": "my_strat", "code": VALID_CODE})
    assert resp.status_code == 200
    assert resp.json()["name"] == "my_strat"


def test_list_strategies(client):
    client.post("/api/v1/user-strategies", json={"name": "strat_a", "code": VALID_CODE})
    client.post("/api/v1/user-strategies", json={"name": "strat_b", "code": VALID_CODE})
    resp = client.get("/api/v1/user-strategies")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()["strategies"]]
    assert "strat_a" in names
    assert "strat_b" in names


def test_load_strategy(client):
    client.post("/api/v1/user-strategies", json={"name": "my_strat", "code": VALID_CODE})
    resp = client.get("/api/v1/user-strategies/my_strat")
    assert resp.status_code == 200
    assert "TestStrat" in resp.json()["code"]


def test_delete_strategy(client):
    client.post("/api/v1/user-strategies", json={"name": "to_delete", "code": VALID_CODE})
    resp = client.delete("/api/v1/user-strategies/to_delete")
    assert resp.status_code == 200
    resp = client.get("/api/v1/user-strategies/to_delete")
    assert resp.status_code == 404


def test_validate_endpoint(client):
    resp = client.post("/api/v1/user-strategies/validate", json={"code": VALID_CODE})
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


def test_validate_bad_code(client):
    resp = client.post("/api/v1/user-strategies/validate", json={"code": "class Foo: pass"})
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


def test_load_nonexistent_returns_404(client):
    resp = client.get("/api/v1/user-strategies/nonexistent")
    assert resp.status_code == 404
