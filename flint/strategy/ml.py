"""The declarative ML strategy surface — features/target/train, not a training loop (§8.5, D19).

FreqAI's key lesson: a user *describes* features and a target and (optionally) how
to train, and the platform owns causality and scheduling. That is what lets the
engine promise no look-ahead and run training outside the per-bar budget. The user
writes :meth:`MLStrategy.features`, :meth:`MLStrategy.target`, :meth:`MLStrategy.train`,
and either :meth:`MLStrategy.decide` (the declarative path) or a full ``on_candle``
(the §8.5 example). The platform guarantees, mechanically:

* **Causal feed** — ``features()`` only ever sees history at-or-before the current
  bar. :func:`build_training_set` hands each training row a *truncated* history
  ending at its anchor bar; the engine never hands the strategy the future frame.
* **Per-window target derivation, right-edge purged** — a label is built only when
  its entire forward window has closed. Rows whose forward window runs past the end
  of closed history are dropped, so a model never trains on an unfinished label.
* **train() outside the bar budget, on a ``retrain_days`` schedule** — training runs
  once per retrain window, not per bar; the fitted model is cached in the managed
  store and re-loaded each bar (train-once/trade-many). v1 amortises training onto
  the retrain-boundary bar in-process; off-thread dispatch (``JobRunnerPort``) is a
  cloud concern. Batch only — online/continual learning is deferred to v2 (D19).
* **Determinism** — ``ctx.seed`` is a fixed run-level int and ``ctx.rng`` the engine
  RNG, so a re-run reproduces bar-for-bar (pass ``random_state=ctx.seed`` to the
  library). Models persist only through ``ctx.model_store`` (§8.5) — never raw pickle.

The walk-forward purge+embargo across train/test splits (López de Prado) is the
research layer's job (Phase 6); the piece that lives here is the *within-window*
right-edge purge that keeps unfinished labels out of a single training set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .base import EngineStrategy, Strategy, _ALL_CLOSED, _CtxProxy, normalize_signals
from .model_store import ModelStore

if TYPE_CHECKING:
    from flint.core.models import Candle, Signal

_MS_PER_DAY = 86_400_000


@dataclass(frozen=True, slots=True)
class FutureWindow:
    """The forward label window handed to :meth:`MLStrategy.target` (§8.5).

    Available **only** during training, over candles whose window has fully closed —
    never reachable from ``on_candle`` at trade time. ``return_pct`` is close-to-close
    from the anchor bar; ``max``/``min`` use the window's highs/lows so a labeller can
    ask "did it hit +2% before −1%?" without touching data past the window.
    """

    candles: tuple["Candle", ...]
    anchor_close: float

    @property
    def return_pct(self) -> float:
        """Close-to-close return over the window, in percent (0 if empty)."""
        if not self.candles or self.anchor_close == 0:
            return 0.0
        return (self.candles[-1].close - self.anchor_close) / self.anchor_close * 100.0

    @property
    def max_return_pct(self) -> float:
        """Best high reached in the window vs the anchor close, in percent."""
        if not self.candles or self.anchor_close == 0:
            return 0.0
        best = max(c.high for c in self.candles)
        return (best - self.anchor_close) / self.anchor_close * 100.0

    @property
    def min_return_pct(self) -> float:
        """Worst low reached in the window vs the anchor close, in percent."""
        if not self.candles or self.anchor_close == 0:
            return 0.0
        worst = min(c.low for c in self.candles)
        return (worst - self.anchor_close) / self.anchor_close * 100.0


class MLContext:
    """The read-only ctx an ML strategy sees: engine ctx + ``model_store`` + ``seed``.

    Thin like :class:`~flint.strategy.base._CtxProxy` — it adds exactly two things
    (:attr:`model_store` and :attr:`seed`) and passes everything else (``rng``,
    ``now``, ``account``, ``candles``, ``funding_rate``, and the ``submit_order``
    escape hatch with its imperative-use flag) straight through to the wrapped ctx.
    The engine ctx has no ``model_store``/``seed``; the platform injects them here so
    the engine stays frozen (§8.2) and the strategy still gets managed persistence.
    """

    __slots__ = ("_ctx", "model_store", "seed")

    def __init__(self, ctx: Any, model_store: ModelStore, seed: int) -> None:
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "model_store", model_store)
        object.__setattr__(self, "seed", seed)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)


def build_training_set(
    strategy: "MLStrategy",
    market: str,
    closed_history: list["Candle"],
    ctx: Any,
    *,
    label_horizon: int,
    warmup: int = 0,
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Build ``(X, y)`` causally from closed history, purging the right edge (§8.5).

    For each anchor bar ``i`` (from ``warmup`` on) whose forward window of
    ``label_horizon`` bars is **fully within** ``closed_history``:

    * ``X`` row = ``strategy.features(market, closed_history[: i + 1], ctx)`` — the
      history is truncated at the anchor, so a feature can only read the past.
    * ``y`` label = ``strategy.target(market, closed_history[: i + 1], future_window)``
      where ``future_window`` is the ``label_horizon`` bars *after* the anchor.

    Anchors whose forward window would run past the end of ``closed_history`` are
    dropped (the within-window right-edge purge) — no unfinished label ever trains.
    """
    features_rows: list[dict[str, Any]] = []
    labels: list[Any] = []
    n = len(closed_history)
    for i in range(max(warmup, 0), n):
        fwd_end = i + 1 + label_horizon
        if fwd_end > n:
            break  # forward window not fully closed -> purge (and all later i too)
        hist_i = closed_history[: i + 1]  # causal: strictly <= anchor bar
        window = FutureWindow(
            candles=tuple(closed_history[i + 1 : fwd_end]),
            anchor_close=closed_history[i].close,
        )
        features_rows.append(strategy.features(market, hist_i, ctx))
        labels.append(strategy.target(market, hist_i, window))
    return features_rows, labels


class MLStrategy(Strategy):
    """Base for a declarative ML strategy (§8.5, D19).

    Subclass and implement :meth:`features` (a causal feature row), :meth:`target`
    (a label over the forward window), :meth:`train` (fit + ``ctx.model_store.save``),
    and :meth:`decide` (turn a prediction into signals) — or override ``on_candle``
    directly for full control. ``params`` carries the ML knobs (auto-exposed to the
    optimizer/UI via :meth:`~flint.strategy.base.Strategy.param_spec`); override them
    per instance like any other strategy param.
    """

    #: ML knobs. ``retrain_days`` sets the retrain cadence, ``label_horizon`` the
    #: number of forward bars a label looks over, ``warmup_bars`` how many leading
    #: bars to skip before features are trustworthy, ``model_key`` the store key.
    params: dict[str, Any] = dict(
        retrain_days=7,
        label_horizon=24,
        warmup_bars=128,
        threshold=0.6,
        model_key="clf",
    )

    @property
    def model_key(self) -> str:
        """The managed-store key this strategy trains and predicts against."""
        return self.params["model_key"]

    def features(self, market: str, history: list["Candle"], ctx: Any) -> dict[str, Any]:
        """Return one feature row from data **at or before now** (§8.5).

        ``history`` ends at the current/anchor bar; reading anything later is a
        look-ahead leak. Use expanding/rolling windows only — an unbounded
        full-window ``mean()``/``std()`` leaks the future and the feature-causality
        screen flags it (§8.5).
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement features(self, market, history, ctx)"
        )

    def target(
        self, market: str, history: list["Candle"], future_window: FutureWindow
    ) -> Any:
        """Return the label for the anchor bar from its forward window (§8.5).

        Only ever called during training, on windows that have fully closed — it is
        never reachable from ``on_candle`` at trade time.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement target(self, market, history, future_window)"
        )

    def train(self, X: list[dict[str, Any]], y: list[Any], ctx: Any) -> None:
        """Fit a model on ``(X, y)`` and persist it via ``ctx.model_store`` (§8.5).

        Seed with ``ctx.seed`` for reproducibility and save in a safe format (the
        store refuses raw pickle). Runs outside the per-bar budget, on the
        ``retrain_days`` schedule.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement train(self, X, y, ctx)"
        )

    def decide(
        self, candle: "Candle", features: dict[str, Any], model: Any, ctx: Any
    ) -> list["Signal"] | "Signal" | None:
        """Turn one prediction into signals (the declarative trade hook, §8.5).

        Called by the default :meth:`on_candle` once a model exists, with the loaded
        model and the current bar's features. Override this for the common case, or
        override ``on_candle`` for full control. Return ``[]``/``None`` for no-op.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement decide(...) or override on_candle(...)"
        )

    def on_candle(
        self, candle: "Candle", history: list["Candle"], ctx: Any
    ) -> list["Signal"] | "Signal" | None:
        """Default declarative loop: no-op until a model exists, then :meth:`decide`.

        Before the first successful train (warmup / no model yet) this is a no-op —
        ``[]`` — so a fresh run never trades on an empty store. Once a model is
        cached it is loaded each bar, features are rebuilt causally, and
        :meth:`decide` maps the prediction to signals. Override wholesale for the
        §8.5-example style (load, ``predict_proba``, threshold).
        """
        if not ctx.model_store.exists(self.model_key):
            return []
        model = ctx.model_store.load(self.model_key)
        row = self.features(candle.market, history, ctx)
        return self.decide(candle, row, model, ctx)


class MLEngineStrategy(EngineStrategy):
    """Adapts an :class:`MLStrategy` to the engine seam, owning the ML lifecycle (§8.5).

    Like :class:`~flint.strategy.base.EngineStrategy` it bridges the user's 3-arg
    ``on_candle`` to the engine's 2-arg seam and enforces the D28 venue gate — but it
    additionally (1) injects a tenant/strategy-scoped ``model_store`` and a fixed
    ``seed`` into the ctx via :class:`MLContext`, and (2) drives the ``retrain_days``
    schedule: when a retrain is due and enough closed history exists, it builds the
    causal training set and calls ``strategy.train`` *outside* the per-bar decision,
    caching the fitted model in the store. Runners wrap an ``MLStrategy`` in this and
    call ``engine.run(strategy=...)`` exactly as for a plain strategy.
    """

    def __init__(
        self, strategy: MLStrategy, model_store: ModelStore, *, seed: int = 0
    ) -> None:
        super().__init__(strategy)
        self.strategy: MLStrategy = strategy
        self.model_store = model_store
        self.seed = seed
        self._last_train_ts: int | None = None
        self.retrain_count = 0

    def on_candle(self, candle: "Candle", ctx: Any) -> list["Signal"]:
        """Engine seam: retrain-if-due, then delegate the decision, then gate (§8.5, D28)."""
        closed = list(ctx.candles(candle.market, _ALL_CLOSED))  # closed-only, training
        history = closed + [candle]  # closed + current, decision
        proxy = _CtxProxy(ctx, self)
        ml_ctx = MLContext(proxy, self.model_store, self.seed)
        self._maybe_retrain(candle, closed, ml_ctx, ctx.now)
        signals = normalize_signals(self.strategy.on_candle(candle, history, ml_ctx))
        return self._gate(signals, candle, ctx.now)

    def _maybe_retrain(
        self, candle: "Candle", closed: list["Candle"], ml_ctx: MLContext, now: int
    ) -> None:
        """Retrain on the ``retrain_days`` schedule, off the per-bar path (§8.5)."""
        params = self.strategy.params
        retrain_days = params["retrain_days"]
        label_horizon = params["label_horizon"]
        warmup = params["warmup_bars"]

        # Need at least one fully-closed label past warmup before a first fit.
        if len(closed) < warmup + label_horizon + 1:
            return
        due = (
            self._last_train_ts is None
            or (now - self._last_train_ts) >= retrain_days * _MS_PER_DAY
        )
        if not due:
            return

        X, y = build_training_set(
            self.strategy,
            candle.market,
            closed,
            ml_ctx,
            label_horizon=label_horizon,
            warmup=warmup,
        )
        if not X:
            return
        self.strategy.train(X, y, ml_ctx)
        self._last_train_ts = now
        self.retrain_count += 1

    def tearsheet_notes(self) -> list[str]:
        """Carry the base notes plus the ML retrain count (§8.1, §8.5)."""
        notes = super().tearsheet_notes()
        notes.append(
            f"ml: {self.retrain_count} scheduled retrain(s) (batch; §8.5)"
        )
        return notes
