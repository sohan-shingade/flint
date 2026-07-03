"""Walk-forward optimization — the v1 anti-overfit backbone (§3.3, §11).

Walk-forward splits a history into contiguous blocks, *tunes* parameters on the
past and *tests* the tuned parameters on the immediately-following block the tuning
never saw. The reported number is the **out-of-sample (OOS)** score; if it is far
worse than the in-sample fit, the strategy is overfit and the user learns that
before risking money.

Two leak guards apply to **every multi-bar strategy** (any strategy whose signal or
label spans more than one bar — not only ML), per §11:

* **Purge** — drop the last ``purge_bars`` of each training block so a label whose
  horizon reaches into the OOS block cannot leak backwards into the fit. The purge
  must be at least the label horizon / average holding period; a visible warning
  fires when it is configured below that.
* **Embargo** — skip the first ``embargo_bars`` of each OOS block from scoring. The
  strategy still *runs* over them (rolling features warm up), but they are excluded
  from the evaluated range so the OOS score is not contaminated by state carried
  across the train/OOS boundary.

The optimizer is **Optuna TPE only** (D22 — the GA optimizer is cut; a second
optimizer doubles the selection surface for zero coverage). Vectorization is used
*only* to sweep parameters across trials — never for the fill simulation, which
stays the honest per-bar engine inside every trial's ``BacktestRunner`` call (§11).

This module is pure orchestration: it never runs the engine or touches data itself.
The caller injects a ``BacktestRunner`` — ``(params, start, end) -> score`` — that
owns engine construction and objective computation, so walk-forward stays venue- and
metric-agnostic (the OOS-Sharpe objective and the ``services`` runner land in later
slices; tests inject a fixture engine run).
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol


class WalkForwardWarning(UserWarning):
    """Raised (as a warning) when a walk-forward config weakens a leak guard."""


class BacktestRunner(Protocol):
    """Runs one backtest over the half-open bar range ``[start, end)``.

    Returns the scalar objective (higher-is-better under the default ``maximize``
    direction) the optimizer maximizes on train and the harness reports on OOS. The
    runner owns the engine, the data, and the metric — walk-forward only decides
    *which* bars each call sees, which is what makes purge/embargo real.
    """

    def __call__(
        self, params: Mapping[str, float], start: int, end: int
    ) -> float: ...


@dataclass(frozen=True)
class ParamSpace:
    """One searchable parameter: a closed ``[low, high]`` range, optional step.

    A ``None`` step is a continuous float; a set step quantizes the grid (e.g.
    ``entry_spread_bps`` in ``2:10:0.5``). Matches the CLI ``--param name=lo:hi:step``
    form (§3.3) via :meth:`parse`.
    """

    name: str
    low: float
    high: float
    step: float | None = None

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"{self.name}: high {self.high} < low {self.low}")
        if self.step is not None and self.step <= 0:
            raise ValueError(f"{self.name}: step must be > 0, got {self.step}")

    @classmethod
    def parse(cls, spec: str) -> "ParamSpace":
        """Parse ``name=lo:hi`` or ``name=lo:hi:step`` (the CLI ``--param`` form)."""
        name, sep, rng = spec.partition("=")
        if not sep or not name:
            raise ValueError(f"expected 'name=lo:hi[:step]', got {spec!r}")
        parts = rng.split(":")
        if len(parts) == 2:
            low, high = parts
            step: float | None = None
        elif len(parts) == 3:
            low, high, step_s = parts
            step = float(step_s)
        else:
            raise ValueError(f"expected 'name=lo:hi[:step]', got {spec!r}")
        return cls(name=name, low=float(low), high=float(high), step=step)


@dataclass(frozen=True)
class WalkForwardConfig:
    """How to split, guard, and search.

    ``n_windows`` OOS blocks are carved after an initial training block. ``n_trials``
    Optuna trials run per training block. ``purge_bars`` and ``embargo_bars`` are the
    leak guards above; ``label_horizon_bars`` is the strategy's label horizon /
    average holding period (in bars) that the purge is checked against. ``seed`` makes
    the TPE search deterministic (offset per window so windows differ but replay).
    """

    n_windows: int
    n_trials: int
    purge_bars: int = 0
    embargo_bars: int = 0
    label_horizon_bars: int = 1
    direction: str = "maximize"
    seed: int = 0

    def __post_init__(self) -> None:
        if self.n_windows < 1:
            raise ValueError("n_windows must be >= 1")
        if self.n_trials < 1:
            raise ValueError("n_trials must be >= 1")
        if self.purge_bars < 0 or self.embargo_bars < 0:
            raise ValueError("purge_bars and embargo_bars must be >= 0")
        if self.direction not in ("maximize", "minimize"):
            raise ValueError("direction must be 'maximize' or 'minimize'")


@dataclass(frozen=True)
class Window:
    """One fold: expanding train ``[train_start, train_end)`` then OOS ``[oos_start,
    oos_end)``. ``train_end`` already has the purge subtracted; ``oos_start`` already
    has the embargo added — so the ranges are exactly what the runner should execute.
    ``test_block_start`` is the pre-embargo block edge, kept for transparency.
    """

    index: int
    train_start: int
    train_end: int
    test_block_start: int
    oos_start: int
    oos_end: int


@dataclass(frozen=True)
class WindowResult:
    """The tuned outcome for one fold: the best params and their in-sample score,
    the OOS score those same params earned, and the trial evidence (count + every
    trial's train score — the trial-Sharpe variance DSR consumes in slice 6.2)."""

    window: Window
    best_params: dict[str, float]
    train_score: float
    oos_score: float
    n_trials: int
    trial_scores: tuple[float, ...]


@dataclass(frozen=True)
class WalkForwardResult:
    """The whole run: per-window results, the OOS series that is the honest report,
    the total trials evaluated (the DSR input, and the number the tearsheet displays),
    and any leak-guard warnings raised while planning."""

    windows: tuple[WindowResult, ...]
    oos_scores: tuple[float, ...]
    total_trials: int
    config: WalkForwardConfig
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def mean_oos(self) -> float:
        """Equal-weighted mean OOS score across windows (0.0 if none)."""
        return sum(self.oos_scores) / len(self.oos_scores) if self.oos_scores else 0.0

    def describe(self) -> str:
        """A human-readable summary — trial count is displayed, not just recorded."""
        lines = [
            f"walk-forward: {len(self.windows)} windows, "
            f"{self.total_trials} trials total, mean OOS {self.mean_oos:.4f}",
            f"  purge={self.config.purge_bars} embargo={self.config.embargo_bars} "
            f"label_horizon={self.config.label_horizon_bars} bars",
        ]
        for r in self.windows:
            lines.append(
                f"  window {r.window.index}: OOS {r.oos_score:.4f} "
                f"(train {r.train_score:.4f}, {r.n_trials} trials) {r.best_params}"
            )
        for w in self.warnings:
            lines.append(f"  ! {w}")
        return "\n".join(lines)


def plan_windows(n_bars: int, config: WalkForwardConfig) -> list[Window]:
    """Carve ``n_bars`` into an expanding-train / forward-OOS walk (§3.3, pure).

    The timeline is split into ``n_windows + 1`` equal blocks; block 0 seeds the
    first training set and blocks 1..N are the OOS tests. Window ``i`` trains on
    everything up to its test block minus the purge, and is scored on its test block
    minus the leading embargo. The final window's OOS block absorbs the remainder so
    no bars are dropped off the end.
    """
    n_windows = config.n_windows
    step = n_bars // (n_windows + 1)
    if step <= 0:
        raise ValueError(
            f"need at least {n_windows + 1} bars for {n_windows} windows, got {n_bars}"
        )

    windows: list[Window] = []
    for i in range(n_windows):
        test_block_start = (i + 1) * step
        oos_end = n_bars if i == n_windows - 1 else (i + 2) * step
        train_end = test_block_start - config.purge_bars
        oos_start = test_block_start + config.embargo_bars
        if train_end - 0 < 1:
            raise ValueError(
                f"window {i}: purge_bars={config.purge_bars} leaves no training bars "
                f"(train block is {test_block_start} bars)"
            )
        if oos_end - oos_start < 1:
            raise ValueError(
                f"window {i}: embargo_bars={config.embargo_bars} leaves no OOS bars "
                f"(OOS block is {oos_end - test_block_start} bars)"
            )
        windows.append(
            Window(
                index=i,
                train_start=0,
                train_end=train_end,
                test_block_start=test_block_start,
                oos_start=oos_start,
                oos_end=oos_end,
            )
        )
    return windows


def _check_guards(config: WalkForwardConfig) -> tuple[str, ...]:
    """Warn (visibly, and on the result) when a leak guard is weaker than the label
    horizon — the §11 rule: purge must be >= the label horizon / holding period, and
    a multi-bar strategy must carry an embargo."""
    msgs: list[str] = []
    if config.purge_bars < config.label_horizon_bars:
        msgs.append(
            f"purge_bars={config.purge_bars} is below the label horizon "
            f"({config.label_horizon_bars} bars): labels can leak from train into OOS"
        )
    if config.label_horizon_bars > 1 and config.embargo_bars < 1:
        msgs.append(
            f"multi-bar strategy (label horizon {config.label_horizon_bars} bars) "
            f"with embargo_bars={config.embargo_bars}: boundary state can leak into OOS"
        )
    for m in msgs:
        warnings.warn(m, WalkForwardWarning, stacklevel=3)
    return tuple(msgs)


def _suggest(trial: object, space: ParamSpace) -> float:
    """Draw one parameter from ``space`` on an Optuna trial."""
    if space.step is not None:
        return trial.suggest_float(  # type: ignore[attr-defined]
            space.name, space.low, space.high, step=space.step
        )
    return trial.suggest_float(  # type: ignore[attr-defined]
        space.name, space.low, space.high
    )


def _optimize(
    space: Sequence[ParamSpace],
    runner: BacktestRunner,
    start: int,
    end: int,
    *,
    n_trials: int,
    seed: int,
    direction: str,
) -> tuple[dict[str, float], float, list[float]]:
    """Tune ``space`` on ``[start, end)`` with Optuna TPE; return best params, best
    score, and every trial's score. Optuna is imported lazily — it is an optional
    (``research``) dependency, so importing this module never requires it."""
    import optuna  # lazy: optional dependency, keeps import-time deps light

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction=direction,
        sampler=optuna.samplers.TPESampler(seed=seed),
    )

    def _objective(trial: "optuna.Trial") -> float:
        params = {s.name: _suggest(trial, s) for s in space}
        return runner(params, start, end)

    study.optimize(_objective, n_trials=n_trials)
    scores = [t.value for t in study.trials if t.value is not None]
    return dict(study.best_params), float(study.best_value), scores


def run_walk_forward(
    n_bars: int,
    space: Iterable[ParamSpace],
    runner: BacktestRunner,
    config: WalkForwardConfig,
) -> WalkForwardResult:
    """Tune → OOS-test across every window and report the out-of-sample series.

    For each window: run ``config.n_trials`` Optuna TPE trials on the (purged)
    training range, then evaluate the single best parameter set on the (embargoed)
    OOS range. The OOS scores — not the in-sample fits — are the honest report; the
    trial counts and per-trial scores are recorded for the tearsheet and DSR.
    """
    space = tuple(space)
    if not space:
        raise ValueError("no parameters to search")
    guard_warnings = _check_guards(config)
    windows = plan_windows(n_bars, config)

    results: list[WindowResult] = []
    total_trials = 0
    for w in windows:
        best_params, train_score, trial_scores = _optimize(
            space,
            runner,
            w.train_start,
            w.train_end,
            n_trials=config.n_trials,
            seed=config.seed + w.index,  # per-window: distinct yet deterministic
            direction=config.direction,
        )
        oos_score = runner(best_params, w.oos_start, w.oos_end)
        results.append(
            WindowResult(
                window=w,
                best_params=best_params,
                train_score=train_score,
                oos_score=oos_score,
                n_trials=len(trial_scores),
                trial_scores=tuple(trial_scores),
            )
        )
        total_trials += len(trial_scores)

    return WalkForwardResult(
        windows=tuple(results),
        oos_scores=tuple(r.oos_score for r in results),
        total_trials=total_trials,
        config=config,
        warnings=guard_warnings,
    )
