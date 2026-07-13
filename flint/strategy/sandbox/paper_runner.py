"""``run_paper_step_in_sandbox`` — one warm-started paper engine step, isolated.

The paper counterpart of :func:`~flint.strategy.sandbox.backtest_runner.run_backtest_in_sandbox`
(D25): a poll-driven or live paper session over *user source* must keep every
engine walk of that source inside the OS boundary, not just pre-run validation.
This parent half serializes one batch of closed-bar inputs (the LiveBar lowering
a :class:`~flint.live.SandboxedPaperSession` step produced) **plus the run's
prior event rows**, and spawns the same isolated backtest child. The child folds
the prior rows into the carried book (``warm_state`` — the identical §6.7
reconstruction the in-process session uses), walks the engine warm-started from
it, and returns only the step's new rows; persisting them (tenant-scoped) is the
caller's job, exactly as on the backtest path.

Everything that crosses the boundary is copied bytes: candles/funding/marks/
OI/books/trades as plain scalars, prior events as the stored wire rows. Like
``run_backtest_in_sandbox``, this is deliberately not re-exported from the
package ``__all__`` — it is another *sandboxed* entrypoint, never a fallback to
in-process execution.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from flint.core.models.market import (
    Candle,
    FundingRate,
    MarkSnapshot,
    OrderbookSnapshot,
)
from flint.data.normalize import TradePrint
from flint.engine.context import OpenInterestSnapshot
from flint.ports import ResourceQuota

from ._spawn import spawn_child
from .backtest_runner import _CHILD_MODULE, SandboxBacktestResult
from .errors import StrategyError


def run_paper_step_in_sandbox(
    source: str,
    class_name: str,
    *,
    candles: list[Candle],
    funding: dict[str, list[FundingRate]],
    marks: dict[str, list[MarkSnapshot]],
    oi: dict[str, list[OpenInterestSnapshot]] | None = None,
    books: dict[str, list[OrderbookSnapshot]] | None = None,
    trades: dict[str, list[TradePrint]] | None = None,
    prior_events: Sequence[Mapping[str, Any]] = (),
    seed: int = 0,
    initial_capital: str = "100000",
    fund_venue: str = "hyperliquid",
    run_id: str = "paper",
    overrides: dict[str, Any] | None = None,
    quota: ResourceQuota | None = None,
    engine: str = "nautilus",
) -> SandboxBacktestResult:
    """Run one paper step of ``source``'s ``class_name`` in the sandbox, warm-started.

    ``prior_events`` are the run's persisted rows so far; the child folds them
    into the engine's ``initial_state`` and resumes its event ``seq`` after them,
    and the returned :class:`SandboxBacktestResult` carries **only the new rows**
    this step appended (plus the drained rejections/notes and the engine
    attribution). The failure taxonomy and quota defaults are identical to
    ``run_backtest_in_sandbox`` — and there is likewise no in-process fallback.
    """
    if quota is None:
        quota = (
            ResourceQuota.default_nautilus()
            if engine == "nautilus"
            else ResourceQuota.default()
        )
    job = {
        "source": source,
        "class_name": class_name,
        "engine": engine,
        "seed": seed,
        "initial_capital": initial_capital,
        "fund_venue": fund_venue,
        "run_id": run_id,
        "overrides": dict(overrides or {}),
        "prior_events": [dict(r) for r in prior_events],
        "inputs": {
            "candles": [asdict(c) for c in candles],
            "funding": {m: [asdict(f) for f in lst] for m, lst in funding.items()},
            "marks": {m: [asdict(x) for x in lst] for m, lst in marks.items()},
            "oi": {m: [asdict(x) for x in lst] for m, lst in (oi or {}).items()},
            "books": {m: [asdict(x) for x in lst] for m, lst in (books or {}).items()},
            "trades": {
                m: [asdict(x) for x in lst] for m, lst in (trades or {}).items()
            },
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
        engine=value.get("engine", "nautilus"),
        engine_versions=dict(value.get("engine_versions", {})),
    )
