"""System API — status check and configuration."""
from __future__ import annotations

from importlib.metadata import version as pkg_version
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
