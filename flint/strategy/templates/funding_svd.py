"""Cross-sectional funding factor template: rank-1 SVD on normalized funding (§8.4).

Every bar the strategy snapshots the predicted hourly funding rate of each
market in the run's universe, z-scores each stream over a rolling window, and
takes the rank-1 SVD of the resulting market×time panel. The first singular
component is the market-wide funding factor (the level everything loads on);
the *residual* — observed minus factor-implied — is each market's idiosyncratic
funding. Abnormally rich residual funding is shorted (collect the excess carry
and fade the dislocation); abnormally cheap residual funding is bought. The
book is capped per side and hysteresis-banded so legs don't churn on noise.

Causality: only ``ctx.funding_rate`` (last *published predicted* rate knowable
at bar start, §6.4/§8.2) is read, and the decision panel at bar ``ts`` uses
readings strictly before ``ts`` so every market in the universe trades off the
same information set regardless of intra-bar candle order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from ._base import TemplateStrategy, template_params

if TYPE_CHECKING:
    from flint.core.models import Candle, Signal

_EPS = 1e-12


class FundingSvdStrategy(TemplateStrategy):
    """Trade residual funding vs the SVD market-wide funding factor (§8.4).

    ``window`` bars of hourly funding per market form the panel; a panel
    needs ``min_markets`` streams before the factor means anything. Entries
    open past ``entry_z`` residual z-score (capped at ``n_legs`` per side,
    strongest dislocations first) and close only back inside ``exit_z``.
    """

    params = template_params(
        window=168,      # rolling funding panel depth in bars (168 = 7d hourly)
        n_legs=2,        # max simultaneous longs and max simultaneous shorts
        entry_z=1.25,    # residual z-score to open a leg
        exit_z=0.5,      # residual z-score band inside which a leg closes
        min_markets=4,   # minimum universe width before the factor is trusted
    )

    def __init__(self, **overrides: Any) -> None:
        super().__init__(**overrides)
        self._streams: dict[str, dict[int, float]] = {}  # market -> ts -> rate_hourly
        self._targets: dict[str, str] = {}  # market -> "long" | "short" (absent = flat)
        self._last_ts: int | None = None

    def on_candle(self, candle: "Candle", history: list["Candle"], ctx: Any) -> list["Signal"]:
        stream = self._streams.setdefault(candle.market, {})
        rate = ctx.funding_rate(candle.market, self.params["venue"])
        if rate is not None:
            stream[candle.ts] = rate.rate_hourly
        if candle.ts != self._last_ts:  # first candle of a new bar: refresh the book
            self._last_ts = candle.ts
            self._recompute(candle.ts)
        return self._rebalance(candle.market, ctx, self._targets.get(candle.market, "flat"))

    # --- factor model -------------------------------------------------------

    def _panel(self, now_ts: int) -> tuple[list[str], np.ndarray] | None:
        """The aligned market×time panel of readings strictly before ``now_ts``."""
        window = int(self.params["window"])
        eligible = {m: s for m, s in self._streams.items() if len(s) >= window}
        if len(eligible) < int(self.params["min_markets"]):
            return None
        common = set.intersection(*(set(s) for s in eligible.values()))
        ts_axis = sorted(t for t in common if t < now_ts)[-window:]
        if len(ts_axis) < window:
            return None
        markets = sorted(eligible)
        return markets, np.array([[eligible[m][t] for t in ts_axis] for m in markets])

    def _recompute(self, now_ts: int) -> None:
        """Rebuild ``self._targets`` from the residuals of the rank-1 factor fit."""
        panel = self._panel(now_ts)
        if panel is None:
            return
        markets, raw = panel
        sd = raw.std(axis=1, keepdims=True)
        z = (raw - raw.mean(axis=1, keepdims=True)) / np.where(sd < _EPS, 1.0, sd)
        u, s, vt = np.linalg.svd(z, full_matrices=False)
        resid = z - s[0] * np.outer(u[:, 0], vt[0])
        # the residual is already in per-stream σ units — threshold it directly.
        # (dividing by the residual's own std would rescale a microscopic but
        # last-bar-shaped residual up to a fake dislocation: magnitude matters.)
        rz = resid[:, -1].copy()
        rz[sd.squeeze(axis=1) < _EPS] = 0.0  # a flat stream has no dislocation to trade

        entry_z, exit_z = float(self.params["entry_z"]), float(self.params["exit_z"])
        n_legs = int(self.params["n_legs"])
        score = dict(zip(markets, rz))

        # held legs persist until the residual re-enters the exit band
        targets: dict[str, str] = {}
        for market, side in self._targets.items():
            zval = score.get(market)
            if zval is None:
                continue
            if (side == "short" and zval >= exit_z) or (side == "long" and zval <= -exit_z):
                targets[market] = side

        # fresh entries by dislocation rank, respecting the per-side cap
        for side, sign in (("short", 1.0), ("long", -1.0)):
            room = n_legs - sum(1 for v in targets.values() if v == side)
            fresh = sorted(
                (m for m in markets if m not in targets and sign * score[m] >= entry_z),
                key=lambda m: -sign * score[m],
            )
            for market in fresh[: max(room, 0)]:
                targets[market] = side

        self._targets = targets
