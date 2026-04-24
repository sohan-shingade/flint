"""Rust engine capability discovery — Phase 3 T3.2.

Single entry point for Python code to ask "what does the installed
flint_core engine support?" without importing flint_core itself.

Returns a frozen dict; callers can safely cache. When flint_core isn't
installed, every capability is False and `engine` is "python".
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict


_PYTHON_ONLY: Dict[str, Any] = {
    "engine": "python",
    "engine_version": None,
    "fill_models": [
        "close", "next_bar_open", "slippage", "pipeline",
        "orderbook", "impact",
    ],
    "fee_models": ["flat", "maker_taker", "drift", "hyperliquid"],
    "supports_partial_fills": True,
    "supports_latency_stage": True,
    "supports_tx_costs": True,
    "supports_orderbook_walk": True,
    "supports_cross_market": True,
    "supports_multi_venue_margin": True,
    "supports_borrow_snapshots": True,
    "supports_maker_taker_fees": True,
}


@lru_cache(maxsize=1)
def rust_capabilities() -> Dict[str, Any]:
    """Return the Rust engine capability dict, or a zero-support stub if
    flint_core isn't available.

    Cached for process lifetime — capabilities are a compile-time property
    of the installed wheel.
    """
    try:
        import flint_core  # type: ignore
    except ImportError:
        return {
            "engine": "python",
            "engine_version": None,
            **{k: False for k in _PYTHON_ONLY if k.startswith("supports_")},
            "fill_models": [],
            "fee_models": [],
        }
    try:
        return dict(flint_core.capabilities())
    except AttributeError:
        # Old flint_core build without capabilities() function.
        return {
            "engine": "rust",
            "engine_version": "unknown",
            **{k: False for k in _PYTHON_ONLY if k.startswith("supports_")},
            "fill_models": ["close", "next_bar_open", "slippage", "pipeline"],
            "fee_models": ["flat"],
            "legacy_build": True,
        }


def python_capabilities() -> Dict[str, Any]:
    """Return what the pure-Python engine supports — always the full superset."""
    return dict(_PYTHON_ONLY)


def rust_supports(feature: str) -> bool:
    """Boolean flag accessor with a safe default of False."""
    caps = rust_capabilities()
    return bool(caps.get(feature, False))
