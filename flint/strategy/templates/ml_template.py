"""The built-in ML template — a LightGBM trend classifier (§8.4, §8.5).

A concrete :class:`~flint.strategy.ml.MLStrategy`: declarative ``features`` (causal,
bounded windows only — it passes Flint's own feature-causality screen), a forward-
return ``target``, a ``train`` that fits LightGBM seeded on ``ctx.seed`` and saves via
``ctx.model_store`` (safe native-JSON format, never pickle), and a ``decide`` that maps
the prediction to a Signal. LightGBM is imported lazily inside ``train``/``decide`` so
this module (and the template registry) load without it; a run that actually trains
needs it installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..ml import MLStrategy
from . import indicators as ind
from ._base import rebalance_signals

if TYPE_CHECKING:
    from flint.core.models import Candle, Signal
    from ..ml import FutureWindow


class LightGbmTrendStrategy(MLStrategy):
    """LightGBM classifier on bounded trend/momentum features (§8.4 ML template).

    Features are deliberately expressed with **trailing windows only** (the
    :mod:`.indicators` helpers over ``history[-n:]``) — no full-window aggregate —
    so the feature-causality screen (§8.5) passes it. The label is the sign of the
    forward-window return; ``decide`` goes long when the model's positive-class
    probability clears ``threshold``, else flat.
    """

    #: Fixed feature order — ``train`` and ``decide`` build vectors identically.
    FEATURE_KEYS: tuple[str, ...] = ("ret", "ma_ratio", "rsi", "vol")

    params: dict[str, Any] = dict(
        retrain_days=7,
        label_horizon=6,
        warmup_bars=60,
        threshold=0.55,
        model_key="lgbm_trend",
        venue="hyperliquid",
        notional_usd=1000.0,
        feat_window=10,
        fast=5,
        slow=20,
        rsi_period=14,
        vol_window=10,
        n_estimators=50,
        num_leaves=15,
        min_child_samples=5,
    )

    def features(self, market: str, history: list["Candle"], ctx: Any) -> dict[str, float]:
        # Bounded, trailing windows only — causal by construction (§8.5).
        series = ind.closes(history)
        w = self.params["feat_window"]
        ret = 0.0
        if len(series) > w and series[-1 - w] != 0:
            ret = series[-1] / series[-1 - w] - 1.0
        fast = ind.sma(series, self.params["fast"])
        slow = ind.sma(series, self.params["slow"])
        ma_ratio = (fast / slow - 1.0) if (fast and slow) else 0.0
        rsi = ind.rsi(series, self.params["rsi_period"])
        sd = ind.stdev(series, self.params["vol_window"])
        vol = (sd / series[-1]) if (sd is not None and series[-1] != 0) else 0.0
        return {
            "ret": ret,
            "ma_ratio": ma_ratio,
            "rsi": (rsi / 100.0) if rsi is not None else 0.5,
            "vol": vol,
        }

    def target(self, market: str, history: list["Candle"], future_window: "FutureWindow") -> int:
        """1 if the forward window closed up, else 0 (§8.5 — closed windows only)."""
        return 1 if future_window.return_pct > 0 else 0

    def train(self, X: list[dict[str, Any]], y: list[Any], ctx: Any) -> None:
        import numpy as np
        import lightgbm as lgb

        labels = np.asarray(y)
        if len(set(labels.tolist())) < 2:
            return  # degenerate window (one class) — skip rather than fit a constant
        matrix = np.asarray([[row[k] for k in self.FEATURE_KEYS] for row in X], dtype=float)
        model = lgb.LGBMClassifier(
            n_estimators=self.params["n_estimators"],
            num_leaves=self.params["num_leaves"],
            min_child_samples=self.params["min_child_samples"],
            random_state=ctx.seed,
            deterministic=True,
            force_col_wise=True,
            verbose=-1,
        )
        model.fit(matrix, labels)
        ctx.model_store.save(self.params["model_key"], model)

    def decide(
        self, candle: "Candle", features: dict[str, Any], model: Any, ctx: Any
    ) -> list["Signal"]:
        import numpy as np

        x = np.asarray([[features[k] for k in self.FEATURE_KEYS]], dtype=float)
        # The store returns a native LightGBM Booster; predict gives P(class=1).
        proba = float(model.predict(x)[0])
        desired = "long" if proba > self.params["threshold"] else "flat"
        return rebalance_signals(
            ctx, candle.market, self.params["venue"], desired, self.params["notional_usd"]
        )
