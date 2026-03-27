"""Tests for the FastAPI backend."""
import pytest

from fastapi.testclient import TestClient

from flint.api.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_list_strategies():
    r = client.get("/api/v1/strategies")
    assert r.status_code == 200
    data = r.json()
    assert len(data["strategies"]) >= 1
    assert data["strategies"][0]["name"] == "ma_crossover"


def test_get_strategy():
    r = client.get("/api/v1/strategies/ma_crossover")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "ma_crossover"
    assert "params" in data


def test_get_ohlcv_empty():
    r = client.get("/api/v1/data/ohlcv?market=NONEXISTENT&resolution_s=60")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_get_funding_empty():
    r = client.get("/api/v1/data/funding?market=NONEXISTENT")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_get_markets():
    r = client.get("/api/v1/data/markets")
    assert r.status_code == 200
    assert "markets" in r.json()


def test_run_backtest_unknown_strategy():
    r = client.post("/api/v1/backtest/run", json={
        "strategy": "nonexistent",
        "market": "SOL-PERP",
        "start_ts": 1709251200,
        "end_ts": 1709337600,
    })
    assert r.status_code == 200
    run_id = r.json()["id"]

    # Poll until done
    import time
    for _ in range(10):
        time.sleep(0.2)
        sr = client.get(f"/api/v1/backtest/{run_id}/results")
        if sr.json()["status"] != "running":
            break
    sr = client.get(f"/api/v1/backtest/{run_id}/results")
    assert sr.json()["status"] == "failed"


def test_backtest_not_found():
    r = client.get("/api/v1/backtest/nonexistent/status")
    assert r.status_code == 404


def test_mev_arb_scan():
    r = client.post("/api/v1/mev/scan/arb", json={
        "pools": [
            {"pool_address": "p1", "dex": "raydium", "token_a_mint": "A", "token_b_mint": "B", "reserve_a": 1000, "reserve_b": 1000, "fee_rate": 0.003},
            {"pool_address": "p2", "dex": "orca", "token_a_mint": "B", "token_b_mint": "C", "reserve_a": 1000, "reserve_b": 1000, "fee_rate": 0.003},
            {"pool_address": "p3", "dex": "raydium", "token_a_mint": "C", "token_b_mint": "A", "reserve_a": 1000, "reserve_b": 1000, "fee_rate": 0.003},
        ],
        "start_token": "A",
        "amount": 1.0,
        "min_profit_bps": 10.0,
    })
    assert r.status_code == 200
    # Balanced pools → no arb
    assert len(r.json()["routes"]) == 0


def test_mev_arb_scan_with_opportunity():
    r = client.post("/api/v1/mev/scan/arb", json={
        "pools": [
            {"pool_address": "p1", "dex": "raydium", "token_a_mint": "SOL", "token_b_mint": "USDC", "reserve_a": 1000, "reserve_b": 100000, "fee_rate": 0.003},
            {"pool_address": "p2", "dex": "orca", "token_a_mint": "USDC", "token_b_mint": "ETH", "reserve_a": 200000, "reserve_b": 100, "fee_rate": 0.003},
            {"pool_address": "p3", "dex": "raydium", "token_a_mint": "ETH", "token_b_mint": "SOL", "reserve_a": 50, "reserve_b": 1050, "fee_rate": 0.003},
        ],
        "start_token": "SOL",
        "amount": 1.0,
        "min_profit_bps": 1.0,
    })
    assert r.status_code == 200
    assert len(r.json()["routes"]) > 0


def test_backtest_with_inline_code():
    """Backtest should accept inline strategy code."""
    code = '''
from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List

class AlwaysHold(Strategy):
    @property
    def name(self) -> str:
        return "always_hold"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        return Signal.HOLD

    def reset(self) -> None:
        pass
'''
    resp = client.post("/api/v1/backtest/run", json={
        "strategy": "custom",
        "code": code,
        "market": "SOL-PERP",
        "start_ts": 1709251200,
        "end_ts": 1711929600,
    })
    assert resp.status_code == 200
    assert "id" in resp.json()


def test_mev_liq_scan():
    r = client.post("/api/v1/mev/scan/liquidations", json={
        "positions": [
            {"user_account": "u1", "market": "SOL-PERP", "size": 10, "entry_price": 100, "collateral": 200},
        ],
        "oracle_prices": {"SOL-PERP": 80},
    })
    assert r.status_code == 200
    assert len(r.json()["opportunities"]) == 1


def test_data_freshness_returns_data_after_download():
    """Freshness endpoint should return metadata after sync_metadata is populated."""
    from flint.store import FlintStore
    from flint.models import SyncMetadata
    import tempfile, os

    db_path = os.path.join(tempfile.gettempdir(), "test_freshness.duckdb")
    try:
        store = FlintStore(db_path)
        meta = SyncMetadata(
            provider="drift_api", market="SOL-PERP", data_type="candles",
            last_sync_ts=1700000000, record_count=100, status="ok", error_msg="",
        )
        store.upsert_sync_metadata(meta)
        freshness = store.get_data_freshness()
        assert len(freshness) == 1
        assert freshness[0]["market"] == "SOL-PERP"
        assert freshness[0]["record_count"] == 100
        store.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_collector_status():
    resp = client.get("/api/v1/collector/status")
    assert resp.status_code == 200
    assert "status" in resp.json()


def test_collector_config():
    resp = client.get("/api/v1/collector/config")
    assert resp.status_code == 200
    assert "markets" in resp.json()


def test_custom_code_no_default_params():
    """Bug fix: custom code strategies should NOT receive default params from built-in strategy names."""
    code = '''
from flint.strategy import Strategy
from flint.models import Candle, Signal
from typing import List, Optional, Dict

class SimpleStrategy(Strategy):
    def __init__(self):
        self._count = 0

    @property
    def name(self) -> str:
        return "Simple"

    def reset(self) -> None:
        self._count = 0

    def on_candle(self, candle, history, ctx=None):
        return Signal.HOLD
'''
    r = client.post("/api/v1/backtest/run", json={
        "code": code,
        "market": "SOL-PERP",
        "start_ts": 1709251200,
        "end_ts": 1709337600,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "running"

    import time
    result = {}
    for _ in range(10):
        time.sleep(1)
        r2 = client.get(f"/api/v1/backtest/{data['id']}/results")
        if r2.status_code == 200:
            result = r2.json()
            if result["status"] in ("complete", "failed"):
                break

    if result.get("status") == "failed":
        error = result.get("results", {}).get("error", "")
        assert "unexpected keyword argument" not in error, \
            f"Default params leaked into custom code strategy: {error}"


def test_results_unknown_id_returns_404():
    """Results endpoint should return 404 for unknown IDs, not 500."""
    r = client.get("/api/v1/backtest/nonexistent-id/results")
    assert r.status_code == 404


def test_results_always_returns_json():
    """Results endpoint must always return valid JSON, never empty body or 500."""
    code = '''
from flint.strategy import Strategy
from flint.models import Candle, Signal

class HoldStrategy(Strategy):
    @property
    def name(self): return "Hold"
    def reset(self): pass
    def on_candle(self, candle, history, ctx=None): return Signal.HOLD
'''
    r = client.post("/api/v1/backtest/run", json={
        "code": code,
        "strategy": "custom",
        "market": "SOL-PERP",
        "start_ts": 1709251200,
        "end_ts": 1709337600,
    })
    assert r.status_code == 200
    run_id = r.json()["id"]

    r2 = client.get(f"/api/v1/backtest/{run_id}/results")
    assert r2.status_code == 200
    data = r2.json()
    assert "status" in data
    assert data["status"] in ("running", "complete", "failed")
    assert "progress" in data
