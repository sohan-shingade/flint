"""User strategy CRUD API routes."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...strategy.loader import validate_strategy_code

router = APIRouter()

# Resolved at import time; tests monkeypatch this
STRATEGIES_DIR = Path(__file__).resolve().parents[3] / "strategies" / "user"


class SaveStrategyRequest(BaseModel):
    name: str
    code: str


class ValidateRequest(BaseModel):
    code: str


@router.post("")
def save_strategy(req: SaveStrategyRequest):
    STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
    path = STRATEGIES_DIR / f"{req.name}.py"
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
    path = STRATEGIES_DIR / f"{name}.py"
    if not path.exists():
        raise HTTPException(404, f"Strategy '{name}' not found")
    code = path.read_text(encoding="utf-8")
    return {"name": name, "code": code}


@router.delete("/{name}")
def delete_strategy(name: str):
    path = STRATEGIES_DIR / f"{name}.py"
    if not path.exists():
        raise HTTPException(404, f"Strategy '{name}' not found")
    path.unlink()
    return {"name": name, "deleted": True}


@router.post("/validate")
def validate_strategy(req: ValidateRequest):
    return validate_strategy_code(req.code)
