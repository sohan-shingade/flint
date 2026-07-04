"""Shared fixtures for the engine-parameterized end-to-end tests (§6.0, N9).

The ``engine`` fixture yields each simulation substrate in turn so a bar-semantics
test that drives the full services/engine path proves the same behavior on both.
The Nautilus leg guards on the optional extra with ``importorskip`` — the repo's
standard optional-dependency pattern — so extras-less environments still collect
the test and skip only that leg while the legacy leg always runs.
"""

from __future__ import annotations

import pytest


@pytest.fixture(params=["legacy-bar", "nautilus"])
def engine(request: pytest.FixtureRequest) -> str:
    """A simulation substrate name; the Nautilus leg skips without the extra."""
    if request.param == "nautilus":
        pytest.importorskip("nautilus_trader")
    return request.param
