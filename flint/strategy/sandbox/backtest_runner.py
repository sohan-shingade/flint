"""``run_backtest_in_sandbox`` — a full user-source backtest inside the sandbox.

The D25 closure (team-lead ruling after 7.3): the OS boundary must contain the
*entire* run of user code, not just pre-run validation — a strategy can behave on
the single-entry validation probe and misbehave once the engine actually walks it.
This is the parent half. It serializes the (inert) engine inputs, spawns the
isolated backtest child (reusing the very same env-scrub / RLIMIT / deadline
machinery as ``run_strategy_sandboxed`` via ``spawn_child``), and returns the
child's event log + rejections + notes. The child compiles and runs the untrusted
strategy; nothing of the parent's crosses the boundary but copied bytes.

Persisting the returned events and folding the trust report is the caller's job
(``services/``), which keeps every store touch on the trusted side and lets the
in-process template path and this path fold from an identical event stream.

Note this is deliberately NOT re-exported from ``flint.strategy.sandbox``'s public
``__all__``: it is a second *sandboxed* entrypoint, not the only one, and the
no-in-process-fallback contract test asserts over that public surface.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from flint.core.models.market import Candle, FundingRate, MarkSnapshot
from flint.ports import ResourceQuota

from ._spawn import spawn_child
from .errors import StrategyError

_CHILD_MODULE = "flint.strategy.sandbox._child_backtest"


@dataclass(frozen=True)
class SandboxBacktestResult:
    """What the sandboxed engine run produced, as inert data for the parent.

    ``events`` are raw stored rows (``EventLog`` wire form) the parent re-persists
    bit-for-bit; ``rejections`` are already in the ``services.backtest._rej`` shape
    ({reason, detail, ts, venue, market, action}); ``notes`` are tearsheet lines.
    """

    events: list[dict[str, Any]]
    rejections: list[dict[str, Any]]
    notes: list[str]


def run_backtest_in_sandbox(
    source: str,
    class_name: str,
    *,
    candles: list[Candle],
    funding: dict[str, list[FundingRate]],
    marks: dict[str, list[MarkSnapshot]],
    seed: int = 0,
    initial_capital: str = "100000",
    fund_venue: str = "hyperliquid",
    run_id: str = "sandbox",
    overrides: dict[str, Any] | None = None,
    quota: ResourceQuota | None = None,
) -> SandboxBacktestResult:
    """Run ``source``'s ``class_name`` backtest over the given inert inputs, isolated.

    Raises ``StrategyError`` if the strategy raised (incl. a denied import) or the
    class was not defined, ``SandboxTimeout`` on a CPU/wall limit, and
    ``SandboxViolation`` if the child crashed or blew the output cap — the same
    taxonomy as ``run_strategy_sandboxed``. Never falls back to in-process
    execution. The inputs are serialized to plain JSON scalars (the codec refuses
    live references by design), so only copies cross the boundary.
    """
    quota = quota or ResourceQuota.default()
    job = {
        "source": source,
        "class_name": class_name,
        "seed": seed,
        "initial_capital": initial_capital,
        "fund_venue": fund_venue,
        "run_id": run_id,
        "overrides": dict(overrides or {}),
        "inputs": {
            "candles": [asdict(c) for c in candles],
            "funding": {m: [asdict(f) for f in lst] for m, lst in funding.items()},
            "marks": {m: [asdict(x) for x in lst] for m, lst in marks.items()},
        },
        "quota": {
            "cpu_seconds": quota.cpu_seconds,
            "mem_bytes": quota.mem_bytes,
            "wall_seconds": quota.wall_seconds,
        },
    }
    out = spawn_child(_CHILD_MODULE, job, quota)
    if not out.get("ok"):
        raise StrategyError(out.get("error_type", "Error"), out.get("error", ""))
    value = out["value"]
    return SandboxBacktestResult(
        events=value["events"],
        rejections=value["rejections"],
        notes=value["notes"],
    )
