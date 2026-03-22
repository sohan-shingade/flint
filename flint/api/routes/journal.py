"""Journal API — persistent backtest history."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request

from ...journal.storage import JournalStorage
from ...store import FlintStore

router = APIRouter()


def _get_journal(request: Request) -> Optional[JournalStorage]:
    store = getattr(request.app.state, "store", None)
    if store is None:
        return None
    # Cache journal instance on app state to avoid CREATE TABLE on every request
    journal = getattr(request.app.state, "_journal", None)
    if journal is None:
        journal = JournalStorage(store)
        request.app.state._journal = journal
    return journal


@router.get("/runs")
def list_runs(request: Request, limit: int = 50):
    journal = _get_journal(request)
    if journal is None:
        return {"runs": []}
    return {"runs": journal.list_runs(limit)}


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    journal = _get_journal(request)
    if journal is None:
        return {"error": "No store"}
    run = journal.get_run(run_id)
    if run is None:
        return {"error": "Run not found"}
    return run


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, request: Request):
    journal = _get_journal(request)
    if journal is None:
        return {"error": "No store"}
    journal.delete_run(run_id)
    return {"deleted": True}


@router.get("/compare")
def compare_runs(ids: str, request: Request):
    journal = _get_journal(request)
    if journal is None:
        return {"comparisons": []}
    run_ids = [i.strip() for i in ids.split(",") if i.strip()]
    return {"comparisons": journal.compare_runs(run_ids)}
