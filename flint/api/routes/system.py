"""System API — status check, configuration, and strategy registry."""
from __future__ import annotations

import json
import time
import uuid
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class SystemStatus(BaseModel):
    initialized: bool
    version: str


class ConfigRequest(BaseModel):
    birdeye_api_key: Optional[str] = None
    helius_api_key: Optional[str] = None


class ConfigResponse(BaseModel):
    saved: bool


_KEY_MAP = {
    "birdeye_api_key": "FLINT_BIRDEYE_API_KEY",
    "helius_api_key": "FLINT_HELIUS_API_KEY",
}


def _get_env_path() -> str:
    return str(Path(".env"))


def _get_version() -> str:
    try:
        return pkg_version("flint")
    except Exception:
        return "0.0.0"


@router.get("/status", response_model=SystemStatus)
def system_status(request: Request):
    store = getattr(request.app.state, "store", None)
    has_data = False
    if store is not None:
        has_data = store.has_candles()
    return SystemStatus(initialized=has_data, version=_get_version())


@router.get("/capabilities")
def get_capabilities(request: Request):
    """Feature matrix for UI + MCP clients.

    Phase 4 T4.6. Stable contract — UI feature-flags hidden surface when a
    flag is False; MCP clients can detect missing server features instead
    of failing at call time.
    """
    from ...backtest.rust_capabilities import rust_capabilities

    rust_caps = rust_capabilities()
    rust_available = rust_caps.get("engine") == "rust"
    return {
        "version": _get_version(),
        "api_version": "v1",
        "engine": {
            "rust_available": rust_available,
            "rust_capabilities": rust_caps,
        },
        "features": {
            # Phase-shipped surface
            "custom_strategies": True,
            "optimization": True,
            "walk_forward": True,
            "parity_test": True,
            "reconciliation_cli": True,   # T1.4 CLI; API endpoint = D-1.4-api
            "pit_audit": True,            # T1.3 CLI
            "custom_data_ingest": True,   # T1.6
            "calibration_cli": True,      # T3.6
            "mev_scanning": True,         # routes exist, user-supplied pools
            # Surface still building
            "live_trading_api": False,    # 4 read-only stubs only; Phase 6.5
            "paper_reconciliation_api": False,  # D-1.4-api
            "websocket_streams": False,   # D-4.3-websocket
        },
        "limits": {
            "max_concurrent_backtests": 5,
            "backtest_timeout_s": 300,
        },
    }


@router.post("/config", response_model=ConfigResponse)
def save_config(body: ConfigRequest):
    """Save optional API keys to .env file."""
    env_path = Path(_get_env_path())

    # Collect updates: only non-empty values
    updates = {}
    for field_name, env_var in _KEY_MAP.items():
        value = getattr(body, field_name, None)
        if value:  # skip None and empty strings
            updates[env_var] = value

    if not updates:
        return ConfigResponse(saved=True)

    # Read existing lines (preserve comments, blanks, ordering)
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()

    # Update existing lines or track which keys still need appending
    remaining = dict(updates)
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                new_lines.append(f"{key}={remaining.pop(key)}")
                continue
        new_lines.append(line)

    # Append any keys that weren't found in existing file
    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(new_lines) + "\n")
    return ConfigResponse(saved=True)


@router.get("/venues")
def list_venues():
    """List all supported execution venues with their configurations.

    This is the single source of truth for venue data — the UI, MCP server,
    and backend all read from flint/execution/venue_config.py.
    """
    from ...execution.venue_config import VENUE_DEFAULTS

    venues = []
    for vid, vc in VENUE_DEFAULTS.items():
        if vid == "default":
            continue
        # Classify venue type
        dex_venues = {"drift", "hyperliquid", "jupiter"}
        vtype = "dex" if vid in dex_venues else "cex"

        venues.append({
            "id": vid,
            "label": vid.capitalize() if vid not in ("okx", "dydx", "htx", "mexc") else vid.upper(),
            "type": vtype,
            "taker_fee_bps": vc.taker_fee_bps,
            "maker_fee_bps": vc.maker_fee_bps,
            "max_leverage": int(vc.max_leverage),
            "latency_s": vc.base_latency_s,
        })

    return {"venues": venues, "count": len(venues)}


@router.get("/regimes")
def list_regimes():
    """List all market regime definitions.

    Single source of truth: flint/regimes.py. Used by BacktestLab regime
    testing, DataExplorer date presets, and documentation.
    """
    from ...regimes import REGIMES, REGIME_TYPES

    return {
        "regimes": [r.to_dict() for r in REGIMES],
        "types": REGIME_TYPES,
        "count": len(REGIMES),
    }


# ─── Strategy Registry ──────────────────────────────────────


class StrategyCreate(BaseModel):
    name: str
    code: str = ""
    params: Optional[dict] = None
    category: str = "custom"
    notes: str = ""


@router.get("/strategies")
def list_registered_strategies(request: Request):
    """List all registered strategies with lifecycle stats.

    Returns each strategy's metadata plus computed fields:
    - backtest_count, best_sharpe, best_pnl (from backtest_runs)
    - paper_session_id, paper_status (latest paper session)
    - live_session_id, live_status (latest live session)
    """
    store = getattr(request.app.state, "store", None)
    if store is None:
        return {"strategies": [], "count": 0}
    return {"strategies": store.list_strategies(), "count": len(store.list_strategies())}


@router.post("/strategies")
def register_strategy(body: StrategyCreate, request: Request):
    """Register a new strategy or update an existing one."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(503, "Store not available")

    strategy_id = str(uuid.uuid4())[:8]
    params_json = json.dumps(body.params) if body.params else "{}"

    # Check if name already exists
    existing = store.get_strategy(body.name)
    if existing:
        strategy_id = existing["strategy_id"]

    store.upsert_strategy(
        strategy_id=strategy_id,
        name=body.name,
        code=body.code,
        params_json=params_json,
        category=body.category,
        notes=body.notes,
    )
    return {"strategy_id": strategy_id, "name": body.name, "status": "saved"}


@router.get("/strategies/{name}")
def get_registered_strategy(name: str, request: Request):
    """Get a specific registered strategy by name."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(503, "Store not available")
    strat = store.get_strategy(name)
    if strat is None:
        raise HTTPException(404, f"Strategy '{name}' not found")
    return strat


@router.delete("/strategies/{name}")
def delete_registered_strategy(name: str, request: Request):
    """Delete a registered strategy."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(503, "Store not available")
    store.delete_strategy(name)
    return {"deleted": name}
