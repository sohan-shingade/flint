"""Tests for multi-market optimization."""
import pytest
from fastapi.testclient import TestClient
from flint.api.main import app

client = TestClient(app)

STRATEGY_CODE = '''
from flint.strategy import Strategy
from flint.models import Candle, Signal

class SimpleStrategy(Strategy):
    def __init__(self, lookback: int = 10):
        self.lookback = lookback
    @property
    def name(self): return "Simple"
    def reset(self): pass
    @classmethod
    def parameters(cls):
        return {"lookback": {"type": "int", "low": 5, "high": 20, "default": 10}}
    def on_candle(self, candle, history, ctx=None): return Signal.HOLD
'''


def test_optimize_accepts_markets_field():
    """Optimizer should accept a 'markets' field for multi-market optimization."""
    r = client.post("/api/v1/optimize/run", json={
        "code": STRATEGY_CODE,
        "market": "SOL-PERP",
        "markets": ["BTC-PERP"],
        "start_ts": 1709251200,
        "end_ts": 1709337600,
        "trials": 5,
    })
    assert r.status_code == 200
    data = r.json()
    assert "id" in data
