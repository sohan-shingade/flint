"""Flint — local-first backtesting, paper, and live lab for perp/DEX strategies.

Hyperliquid-native, venue-agnostic core. Ports-and-adapters architecture (see
docs/redesign/DESIGN.md §2.7, §4, §17): surfaces (api/, sdk/, mcp_srv/) talk only
to services/; services/ drive the domain core (engine/, research/, strategy/,
core/) and reach infrastructure exclusively through ports/. The engine never
touches storage directly.

The blessed public entry points (§3.2): ``from flint import Strategy, Signal``."""

from __future__ import annotations

from flint.core.models import Signal
from flint.strategy import Strategy

__all__ = ["Strategy", "Signal"]
