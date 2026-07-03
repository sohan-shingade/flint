"""The optimize front door — walk-forward param search under a TenantContext (§11, §6.1).

Optimization is not a second engine: it is the same backtest front door, driven by
``research.walkforward``. This service supplies the production wiring the walk-forward
needs — a real per-tenant backtest runner (:func:`make_backtest_runner`) — bridges the
walk-forward's **bar-index** coordinate space to the runner's **millisecond** ranges,
and folds the result into an honest, persisted Run-Library head.

The honesty contract (D22, carry-forward (i)) is the point: the tearsheet the SDK/CLI
renders **always** shows the raw Sharpe together with the Deflated Sharpe and the trial
count. A search that evaluated N parameter sets is scored out-of-sample and its selected
strategy's Sharpe is deflated by the whole trial family — an optimizer can't fool itself
with an in-sample fit, and the number of trials that went into the winner is never hidden.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from warnings import catch_warnings, simplefilter

from flint.data import DataManager
from flint.ports import TenantContext, UserDataPort
from flint.research import (
    ParamSpace,
    WalkForwardConfig,
    WalkForwardResult,
    bar_returns,
    deflated_sharpe,
)

from .backtest import (
    BacktestRequest,
    _execute,
    _manifest,
    _persist,
    make_backtest_runner,
)


@dataclass(frozen=True)
class OptimizeRequest:
    """What to search: a template + parameter ranges over a universe/venue/range.

    ``params`` are ``ParamSpace`` in the ``name=lo:hi[:step]`` CLI form (§3.3); the
    walk-forward carves the range into ``n_windows`` out-of-sample folds and runs
    ``n_trials`` Optuna trials per training block. ``purge_bars``/``embargo_bars`` are
    the leak guards, checked against ``label_horizon_bars`` (§11).
    """

    run_id: str
    strategy: str
    params: tuple[ParamSpace, ...]
    universe: tuple[str, ...] = ("SOL-PERP",)
    venues: tuple[str, ...] = ("hyperliquid",)
    start_ms: int = 0
    end_ms: int = 0
    resolution_s: int = 3600
    n_windows: int = 3
    n_trials: int = 20
    purge_bars: int = 0
    embargo_bars: int = 0
    label_horizon_bars: int = 1
    direction: str = "maximize"
    seed: int = 0
    initial_capital: str = "100000"


@dataclass(frozen=True)
class OptimizeOutcome:
    """The head of a finished search: the persisted run + the walk-forward evidence."""

    run_id: str
    verdict: str  # "ok"
    summary: Mapping[str, Any] = field(default_factory=dict)


def run_optimization(
    tenant: TenantContext,
    request: OptimizeRequest,
    *,
    user_data: UserDataPort,
    data: DataManager,
    now_ms: int = 0,
) -> OptimizeOutcome:
    """Search ``request`` for ``tenant`` and persist the winner as a Run-Library head.

    Runs the walk-forward (tune → OOS test per window), selects the params with the
    best mean-representative OOS fold, re-executes a confirmation backtest over the full
    range with those params, and deflates its Sharpe by the pooled trial family. The
    persisted run carries the DSR + total trial count so the tearsheet shows them.
    """
    if not request.params:
        raise ValueError("optimize needs at least one --param name=lo:hi[:step]")

    bar_ms = request.resolution_s * 1000
    n_bars = (request.end_ms - request.start_ms) // bar_ms

    ms_runner = make_backtest_runner(
        tenant,
        data=data,
        strategy=request.strategy,
        universe=request.universe,
        venues=request.venues,
        resolution_s=request.resolution_s,
        initial_capital=request.initial_capital,
        seed=request.seed,
    )

    def wf_runner(params: Mapping[str, Any], start_bar: int, end_bar: int) -> float:
        # Walk-forward speaks bar indices; the backtest runner speaks unix-ms. Map the
        # fold's [start_bar, end_bar) onto the request's real timeline before running.
        start = request.start_ms + start_bar * bar_ms
        end = request.start_ms + end_bar * bar_ms
        return ms_runner(params, start, end)

    config = WalkForwardConfig(
        n_windows=request.n_windows,
        n_trials=request.n_trials,
        purge_bars=request.purge_bars,
        embargo_bars=request.embargo_bars,
        label_horizon_bars=request.label_horizon_bars,
        direction=request.direction,
        seed=request.seed,
    )
    # The leak-guard warnings are captured onto the result already; silence the
    # duplicate warnings.warn emission so a CLI run isn't noisy on stderr.
    with catch_warnings():
        simplefilter("ignore")
        from flint.research import run_walk_forward

        wf = run_walk_forward(n_bars, request.params, wf_runner, config)

    best = _best_window(wf)
    final_request = BacktestRequest(
        run_id=request.run_id,
        strategy=request.strategy,
        universe=request.universe,
        venues=request.venues,
        start_ms=request.start_ms,
        end_ms=request.end_ms,
        resolution_s=request.resolution_s,
        seed=request.seed,
        initial_capital=request.initial_capital,
        overrides=dict(best),
    )
    run = _execute(tenant, final_request, data=data, event_store=user_data)

    pooled = tuple(s for w in wf.windows for s in w.trial_scores)
    deflated = _deflate(run.equity, pooled)

    manifest = _manifest(
        final_request,
        now_ms=now_ms,
        effective=run.summary["effective_range"],
        metrics=run.summary["metrics"],
        fidelity_lines=run.summary["fidelity_lines"],
        note=f"optimize: {len(wf.windows)} windows, {wf.total_trials} trials",
    )
    summary = {
        **manifest.to_summary(),
        **run.summary,
        "kind": "optimize",
        "deflated_sharpe": deflated,
        "n_trials": wf.total_trials,
        "optimize": _optimize_block(wf, best),
    }
    _persist(user_data, tenant, request.run_id, now_ms, summary)
    return OptimizeOutcome(request.run_id, "ok", summary)


# -- internals ---------------------------------------------------------------


def _best_window(wf: WalkForwardResult) -> dict[str, float]:
    """The params of the best out-of-sample fold (the honest selection, not in-sample).

    ``maximize`` picks the highest OOS score, ``minimize`` the lowest; an empty search
    (no windows) returns no overrides so the confirmation run is the bare template.
    """
    if not wf.windows:
        return {}
    pick = max if wf.config.direction == "maximize" else min
    winner = pick(wf.windows, key=lambda w: w.oos_score)
    return dict(winner.best_params)


def _deflate(
    equity: Sequence[float], trial_sharpes: Sequence[float]
) -> dict[str, Any] | None:
    """DSR of the confirmation run deflated by the trial family — ``None`` if degenerate.

    A search that produced fewer than two trials or a track record too short to score
    has no meaningful deflation; the tearsheet reports that honestly as ``n/a`` rather
    than inventing a number (carry-forward (i))."""
    returns = bar_returns(equity)
    if len(returns) < 2 or len(trial_sharpes) < 1:
        return None
    try:
        d = deflated_sharpe(returns, trial_sharpes)
    except (ValueError, ZeroDivisionError):
        return None
    return {
        "dsr": d.dsr,
        "observed_sharpe": d.observed_sharpe,
        "expected_max_sharpe": d.expected_max_sharpe,
        "n_trials": d.n_trials,
        "n_returns": d.n_returns,
        "trial_sharpe_variance": d.trial_sharpe_variance,
    }


def _optimize_block(wf: WalkForwardResult, best: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "n_windows": len(wf.windows),
        "total_trials": wf.total_trials,
        "mean_oos": wf.mean_oos,
        "best_params": dict(best),
        "oos_scores": list(wf.oos_scores),
        "windows": [
            {
                "index": w.window.index,
                "oos_score": w.oos_score,
                "train_score": w.train_score,
                "n_trials": w.n_trials,
                "best_params": dict(w.best_params),
            }
            for w in wf.windows
        ],
        "warnings": list(wf.warnings),
    }
