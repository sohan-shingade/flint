"""System API — status check and configuration."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
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


@router.get("/status", response_model=SystemStatus)
def system_status(request: Request):
    store = getattr(request.app.state, "store", None)
    has_data = False
    if store is not None:
        with store._lock:
            row = store._conn.execute("SELECT COUNT(*) FROM candles").fetchone()
            has_data = row[0] > 0 if row else False
    return SystemStatus(initialized=has_data, version="0.3.0")


@router.post("/config", response_model=ConfigResponse)
def save_config(body: ConfigRequest):
    env_path = _get_env_path()
    # Read existing .env lines into an ordered dict
    existing: dict[str, str] = {}
    path = Path(env_path)
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                existing[key.strip()] = value.strip()

    # Update only non-empty values from the request
    for field_name, env_key in _KEY_MAP.items():
        value = getattr(body, field_name, None)
        if value is not None and value != "":
            existing[env_key] = value

    # Write back all keys
    lines = [f"{k}={v}" for k, v in existing.items()]
    path.write_text("\n".join(lines) + "\n" if lines else "")

    return ConfigResponse(saved=True)
