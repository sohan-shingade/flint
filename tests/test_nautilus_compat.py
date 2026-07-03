"""The churn firewall — the pin is asserted and the re-exports are complete (§A3).

Gated on the extra: without ``nautilus_trader`` installed, importing ``_compat``
raises an actionable ImportError, so the whole module skips.
"""

from __future__ import annotations

import pytest

pytest.importorskip("nautilus_trader")

import nautilus_trader  # noqa: E402

from flint.engine.nautilus import _compat  # noqa: E402


def test_pin_matches_the_installed_version():
    assert _compat.NAUTILUS_REQUIRED == nautilus_trader.__version__


def test_every_reexport_is_present():
    # __all__ is the single inventory of Nautilus names Flint uses; each must resolve.
    for name in _compat.__all__:
        assert getattr(_compat, name) is not None, name
