"""``run_strategy_sandboxed`` — the ONE way strategy code ever executes (§8.3).

There is exactly one entry and **no in-process fallback for any argument
combination**. Round-1's hole was a service path that skipped sandbox routing
precisely when funding/orderbook args were present (the normal perp case); the
lesson is that isolation must be unconditional. Every caller — CLI, REST, MCP,
optimizer workers — comes through here.

The parent spawns an env-scrubbed, isolated (`python -I`) subprocess, hands it
the job over a pipe, enforces the wall-clock deadline and output cap, and decodes
the result with JSON/Arrow only (never pickle). The spawn/scrub/limits machinery
lives in ``_spawn`` so the full-run sandbox path (``run_backtest_in_sandbox``)
reuses the exact same isolation with a different child payload.
"""

from __future__ import annotations

from typing import Any

from flint.ports import ResourceQuota

from ._spawn import OUTPUT_CAP_BYTES, spawn_child
from .errors import StrategyError
from .protocol import decode_value, encode_value
from .screen import screen_or_raise

_CHILD_MODULE = "flint.strategy.sandbox._child"

__all__ = ["OUTPUT_CAP_BYTES", "run_strategy_sandboxed"]


def run_strategy_sandboxed(
    source: str,
    entry: str = "run",
    input_value: Any = None,
    *,
    quota: ResourceQuota | None = None,
    screen: bool = False,
) -> Any:
    """Run ``source``'s ``entry(input_value)`` in the sandbox; return its result.

    Raises ``StrategyError`` if the strategy raised (incl. a denied import),
    ``SandboxTimeout`` on a CPU/wall limit, ``SandboxViolation`` if the child
    crashed or blew the output cap. Never falls back to in-process execution.

    ``screen=True`` runs the static AST/import screen first (§8.3) and raises
    ``StrategyScreenError`` with line-precise issues *before* a subprocess is
    spawned — the fast, lint-grade UX a surface (CLI/API/MCP/optimizer) turns on.
    It is opt-in, never load-bearing: the boundary contains code the screen would
    reject, so the security guarantee never depends on it (which is why the
    escape-suite runs with ``screen=False``).
    """
    if screen:
        screen_or_raise(source)
    quota = quota or ResourceQuota.default()
    job = {
        "source": source,
        "entry": entry,
        "input": encode_value(input_value),
        "quota": {
            "cpu_seconds": quota.cpu_seconds,
            "mem_bytes": quota.mem_bytes,
            "wall_seconds": quota.wall_seconds,
        },
    }
    out = spawn_child(_CHILD_MODULE, job, quota)
    if out.get("ok"):
        return decode_value(out["value"])
    raise StrategyError(out.get("error_type", "Error"), out.get("error", ""))
