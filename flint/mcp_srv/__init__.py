"""mcp_srv — the MCP agent surface (§13).

Named ``mcp_srv`` (not ``mcp``) so it never shadows the pip ``mcp`` package the
stdio adapter imports. :class:`~flint.mcp_srv.tools.AgentTools` is the transport-
independent tool layer (JSON in / JSON out over ``services/``); :mod:`server`
binds those callables to a FastMCP stdio server, degrading gracefully when the
``mcp`` SDK is absent.
"""

from __future__ import annotations

from .server import build_server, main, mcp_available
from .tools import AgentTools

__all__ = ["AgentTools", "build_server", "main", "mcp_available"]
