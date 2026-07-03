"""Flint — local-first backtesting, paper, and live lab for perp/DEX strategies.

Hyperliquid-native, venue-agnostic core. Ports-and-adapters architecture (see
docs/redesign/DESIGN.md §2.7, §4, §17): surfaces (api/, sdk/, mcp_srv/) talk only
to services/; services/ drive the domain core (engine/, research/, strategy/,
core/) and reach infrastructure exclusively through ports/. The engine never
touches storage directly."""
