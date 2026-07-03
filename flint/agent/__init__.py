"""agent — the agentic development loop over the MCP tool surface (§13.1).

The control flow of "author → validate → backtest → read structured failure →
revise": :func:`run_agent_loop` drives an injected author against
:class:`~flint.mcp_srv.tools.AgentTools`, recording every structured response.
:func:`sequence_author` is a scripted author for deterministic fixture runs.
"""

from __future__ import annotations

from .loop import (
    AgentIteration,
    AgentSession,
    Feedback,
    StrategyAuthor,
    run_agent_loop,
    sequence_author,
)

__all__ = [
    "run_agent_loop",
    "sequence_author",
    "AgentSession",
    "AgentIteration",
    "Feedback",
    "StrategyAuthor",
]
