"""Enforce CLAUDE.md rule:

    'Never access store._conn or store._lock from API routes — add a method
    to FlintStore instead.'

Phase 2 T2.2.e. Any future PR that reintroduces a leak fails here.

Scope: `flint/api/` (all routes + main.py) and `flint/mcp_server.py`. Internal
storage helpers (`flint/journal/`, `flint/paper/session_store.py`) are pre-rule
and are migrated separately under Phase 2.2.c follow-up.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent
FORBIDDEN_PATTERN = re.compile(r"(?:^|[^.\w])store\._(conn|lock)\b")

# Scope: API surface + MCP only. Internal store-backed helpers are allowed
# to bypass encapsulation until their own refactor in Phase 2.
SCOPE_DIRS = [
    ROOT / "flint" / "api",
]
SCOPE_FILES = [
    ROOT / "flint" / "mcp_server.py",
]


def _scan_python_files():
    paths = []
    for d in SCOPE_DIRS:
        paths.extend(d.rglob("*.py"))
    paths.extend(SCOPE_FILES)
    return [p for p in paths if "__pycache__" not in p.parts]


def test_no_raw_store_conn_or_lock_in_api_or_mcp():
    violations = []
    for path in _scan_python_files():
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            # Ignore comments and docstrings we intentionally reference
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') \
                    or stripped.startswith("'''"):
                continue
            if FORBIDDEN_PATTERN.search(line):
                violations.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not violations, (
        "Raw store._conn / store._lock access found in API or MCP "
        "(CLAUDE.md forbids — add a FlintStore method instead):\n  "
        + "\n  ".join(violations)
    )


def test_flint_store_exposes_expected_methods():
    """Sanity-check that the encapsulated methods added in T2.2.a exist."""
    from flint.store import FlintStore

    expected = {
        "get_live_equity_history",
        "list_live_sessions",
        "list_markets_with_data",
        "list_venues_for_market",
        "delete_market_data",
        "count_funding_rates",
        "count_orderbook_snapshots",
        "count_open_interest",
        "count_venue_candles",
        "mark_running_live_sessions_interrupted",
    }
    missing = [m for m in expected if not hasattr(FlintStore, m)]
    assert not missing, f"FlintStore missing methods: {missing}"
