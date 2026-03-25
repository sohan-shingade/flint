"""User strategy CRUD API routes."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...strategy.loader import validate_strategy_code

router = APIRouter()

# Resolved at import time; tests monkeypatch this
STRATEGIES_DIR = Path(__file__).resolve().parents[3] / "strategies" / "user"

# Strategy names: letter-start, alphanumeric/underscore/hyphen, max 64 chars
_SAFE_NAME = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')


def _safe_path(name: str) -> Path:
    """Resolve strategy path, rejecting traversal attempts."""
    if not _SAFE_NAME.match(name):
        raise HTTPException(
            400,
            "Strategy name must start with a letter and contain only "
            "letters, digits, underscores, or hyphens (max 64 chars)",
        )
    path = (STRATEGIES_DIR / f"{name}.py").resolve()
    if not str(path).startswith(str(STRATEGIES_DIR.resolve())):
        raise HTTPException(400, "Invalid strategy name")
    return path


class SaveStrategyRequest(BaseModel):
    name: str
    code: str


class ValidateRequest(BaseModel):
    code: str


@router.post("")
def save_strategy(req: SaveStrategyRequest):
    STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
    path = _safe_path(req.name)
    path.write_text(req.code, encoding="utf-8")
    return {"name": req.name, "saved": True}


@router.get("")
def list_strategies():
    STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
    strategies = []
    for f in sorted(STRATEGIES_DIR.glob("*.py")):
        strategies.append({"name": f.stem, "file": f.name})
    return {"strategies": strategies}


@router.get("/{name}")
def load_strategy(name: str):
    path = _safe_path(name)
    if not path.exists():
        raise HTTPException(404, f"Strategy '{name}' not found")
    code = path.read_text(encoding="utf-8")
    return {"name": name, "code": code}


@router.delete("/{name}")
def delete_strategy(name: str):
    path = _safe_path(name)
    if not path.exists():
        raise HTTPException(404, f"Strategy '{name}' not found")
    path.unlink()
    return {"name": name, "deleted": True}


@router.post("/validate")
def validate_strategy(req: ValidateRequest):
    return validate_strategy_code(req.code)
