"""Journal API — persistent backtest history.

D-4.7-full: thin adapter over `flint.services.journal`. Routes here
just translate HTTP↔dict; the service does the work.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ...services import journal as journal_service

router = APIRouter()


def _store(request: Request):
    return getattr(request.app.state, "store", None)


@router.get("/runs")
def list_runs(request: Request, limit: int = 50):
    store = _store(request)
    if store is None:
        return {"runs": []}
    return {"runs": journal_service.list_runs(store, limit)}


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    store = _store(request)
    if store is None:
        return {"error": "No store"}
    run = journal_service.get_run(store, run_id)
    if run is None:
        return {"error": "Run not found"}
    return run


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, request: Request):
    store = _store(request)
    if store is None:
        return {"error": "No store"}
    journal_service.delete_run(store, run_id)
    return {"deleted": True}


@router.get("/compare")
def compare_runs(ids: str, request: Request):
    store = _store(request)
    if store is None:
        return {"comparisons": []}
    run_ids = [i.strip() for i in ids.split(",") if i.strip()]
    return {"comparisons": journal_service.compare_runs(store, run_ids)}
