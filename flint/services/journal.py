"""Journal service — past-run browsing and comparison.

Wraps `JournalStorage` so callers (FastAPI routes, MCP tools, scripts)
all go through the same code path. The wrapper is thin — `JournalStorage`
already does the SQL — but it gives MCP a single import surface and
keeps the route file focused on HTTP concerns.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..journal.storage import JournalStorage
from ..store import FlintStore


def _journal(store: FlintStore) -> JournalStorage:
    return JournalStorage(store)


def list_runs(store: FlintStore, limit: int = 50) -> List[Dict[str, Any]]:
    return _journal(store).list_runs(limit)


def get_run(store: FlintStore, run_id: str) -> Optional[Dict[str, Any]]:
    return _journal(store).get_run(run_id)


def delete_run(store: FlintStore, run_id: str) -> None:
    _journal(store).delete_run(run_id)


def compare_runs(store: FlintStore, run_ids: List[str]) -> List[Dict[str, Any]]:
    return _journal(store).compare_runs(run_ids)
