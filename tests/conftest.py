"""Shared fixtures for the engine-parameterized end-to-end tests (§6.0, N9/N10).

The ``engine`` fixture names the simulation substrate an end-to-end test drives
through the full services/engine path. Since N10 deleted the legacy bar walk there
is exactly one substrate — Nautilus — but the fixture (and its parameterization
shape) survives so bar-semantics tests keep their signature and a future substrate
can be added by extending ``params``. The leg guards on the optional extra with
``importorskip`` — the repo's standard optional-dependency pattern.
"""

from __future__ import annotations

import pytest


@pytest.fixture(params=["nautilus"])
def engine(request: pytest.FixtureRequest) -> str:
    """A simulation substrate name; skips without the ``nautilus`` extra."""
    if request.param == "nautilus":
        pytest.importorskip("nautilus_trader")
    return request.param
