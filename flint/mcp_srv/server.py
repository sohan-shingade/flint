"""The MCP stdio server — a thin adapter over :class:`AgentTools` (§13.2).

The tool *logic* lives in :mod:`flint.mcp_srv.tools` and needs no protocol library;
this module only binds those callables to MCP so an agent host can call them over
stdio. The ``mcp`` SDK is an optional import: if it is not installed, the tool
layer still works (and the tests still run) — :func:`build_server` raises a clear,
structured error and :func:`mcp_available` lets a caller degrade gracefully rather
than crash on import. Nothing here reaches past ``services/`` (§4, §13.4).
"""

from __future__ import annotations

from typing import Any

from .tools import AgentTools


def mcp_available() -> bool:
    """True if the ``mcp`` SDK (FastMCP) is importable in this environment."""
    try:
        import mcp.server.fastmcp  # noqa: F401
    except Exception:
        return False
    return True


def build_server(tools: AgentTools, *, name: str = "flint") -> Any:
    """Register the §13.2 agent tools on a FastMCP server bound to ``tools``.

    Returns the FastMCP instance (call ``.run()`` for stdio). Raises
    :class:`RuntimeError` with an actionable message when the ``mcp`` SDK is
    absent — the tool layer itself is always usable without it.
    """
    if not mcp_available():
        raise RuntimeError(
            "the `mcp` SDK is not installed — `pip install mcp` to run the stdio "
            "server, or drive flint.mcp_srv.tools.AgentTools directly"
        )
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name)

    @server.tool()
    def list_universe() -> dict[str, Any]:
        """List strategy templates and the executable venue (§13.2)."""
        return tools.list_universe()

    @server.tool()
    def data_coverage(market: str, venue: str) -> dict[str, Any]:
        """Covered data ranges for a (venue, market) — no fetch, no gate."""
        return tools.data_coverage(market=market, venue=venue)

    @server.tool()
    def validate_strategy(code: str) -> dict[str, Any]:
        """Sandbox + static-lint strategy source; structured errors before a run."""
        return tools.validate_strategy(code)

    @server.tool()
    def run_backtest(
        code: str | None = None,
        strategy: str | None = None,
        universe: list[str] | None = None,
        venues: list[str] | None = None,
        start_ms: int = 0,
        end_ms: int = 0,
        resolution_s: int = 3600,
        fill_mode: str = "auto",
        seed: int = 0,
        initial_capital: str = "100000",
        overrides: dict[str, Any] | None = None,
        signal_venues: list[str] | None = None,
    ) -> dict[str, Any]:
        """Backtest user `code` or a `strategy` template — returns a run id."""
        return tools.run_backtest(
            code=code,
            strategy=strategy,
            universe=tuple(universe) if universe else ("SOL-PERP",),
            venues=tuple(venues) if venues else ("hyperliquid",),
            start_ms=start_ms,
            end_ms=end_ms,
            resolution_s=resolution_s,
            fill_mode=fill_mode,
            seed=seed,
            initial_capital=initial_capital,
            overrides=overrides or {},
            signal_venues=tuple(signal_venues) if signal_venues else (),
        )

    @server.tool()
    def get_results(run_id: str) -> dict[str, Any]:
        """Structured metrics, equity curve, per-trade log, cost attribution."""
        return tools.get_results(run_id)

    @server.tool()
    def explain_failure(run_id: str) -> dict[str, Any]:
        """Why a run did poorly, as failure enums with detail (§13.3)."""
        return tools.explain_failure(run_id)

    @server.tool()
    def optimize(
        strategy: str,
        params: list[str],
        universe: list[str] | None = None,
        venues: list[str] | None = None,
        start_ms: int = 0,
        end_ms: int = 0,
        resolution_s: int = 3600,
        n_windows: int = 3,
        n_trials: int = 20,
        purge_bars: int = 0,
        embargo_bars: int = 0,
        label_horizon_bars: int = 1,
        seed: int = 0,
        initial_capital: str = "100000",
    ) -> dict[str, Any]:
        """Walk-forward param sweep with out-of-sample scores + Deflated Sharpe."""
        return tools.optimize(
            strategy=strategy,
            params=params,
            universe=tuple(universe) if universe else ("SOL-PERP",),
            venues=tuple(venues) if venues else ("hyperliquid",),
            start_ms=start_ms,
            end_ms=end_ms,
            resolution_s=resolution_s,
            n_windows=n_windows,
            n_trials=n_trials,
            purge_bars=purge_bars,
            embargo_bars=embargo_bars,
            label_horizon_bars=label_horizon_bars,
            seed=seed,
            initial_capital=initial_capital,
        )

    @server.tool()
    def compare(run_ids: list[str]) -> dict[str, Any]:
        """Side-by-side metric table with the different-effective-range warning."""
        return tools.compare(run_ids)

    return server


def main() -> None:  # pragma: no cover — process entrypoint, exercised via build_server
    """Compose a local ``AgentTools`` and serve the MCP tools over stdio."""
    tools = AgentTools()
    build_server(tools).run()


if __name__ == "__main__":  # pragma: no cover
    main()
