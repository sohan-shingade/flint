"""Perp-native templates: funding harvest, funding dislocation, basis, OI momentum (§8.4).

These are the strategies §8.4 exists for — the ones where funding/basis/flow *is* the
edge, not a cost footnote. Each reads the causal ctx accessors (funding is
predicted-only, never the settled leak — §6.4/§8.2) and expresses a desired state via
``self._rebalance``; none touches ``ctx.submit_order``. Funding rates are compared as
``rate_hourly`` (venue-interval-normalized, §5) so thresholds mean one thing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._base import TemplateStrategy, template_params

if TYPE_CHECKING:
    from flint.core.models import Candle, Signal


class FundingHarvestStrategy(TemplateStrategy):
    """Collect funding by holding the side that *receives* it (§8.4, §6.4).

    When the hourly rate is meaningfully positive, longs pay shorts — so go short to
    receive; when meaningfully negative, go long. Inside the deadband, stay flat (the
    carry does not clear costs). Directional price risk is the known tradeoff of a
    single-leg harvester; the dislocation and basis templates hedge differently.
    """

    params = template_params(rate_threshold=1e-5)

    def on_candle(self, candle: "Candle", history: list["Candle"], ctx: Any) -> list["Signal"]:
        fr = ctx.funding_rate(candle.market, self.params["venue"])
        if fr is None:
            return []
        threshold = self.params["rate_threshold"]
        if fr.rate_hourly > threshold:
            desired = "short"
        elif fr.rate_hourly < -threshold:
            desired = "long"
        else:
            desired = "flat"
        return self._rebalance(candle.market, ctx, desired)


class FundingDislocationStrategy(TemplateStrategy):
    """Trade the HL leg when its funding dislocates from a benchmark rate (§8.4).

    ``dislocation = hl_rate - benchmark_rate``. When HL funding is richer than the
    benchmark by more than the threshold, short the HL leg to collect the rich carry;
    when cheaper, go long. The benchmark is a param in v1 (a second executable venue
    to source it live is a later expansion — HL is the only executable venue, D28).
    """

    params = template_params(benchmark_rate=0.0, threshold=1e-5)

    def on_candle(self, candle: "Candle", history: list["Candle"], ctx: Any) -> list["Signal"]:
        fr = ctx.funding_rate(candle.market, self.params["venue"])
        if fr is None:
            return []
        dislocation = fr.rate_hourly - self.params["benchmark_rate"]
        threshold = self.params["threshold"]
        if dislocation > threshold:
            desired = "short"
        elif dislocation < -threshold:
            desired = "long"
        else:
            desired = "flat"
        return self._rebalance(candle.market, ctx, desired)


class BasisTradeStrategy(TemplateStrategy):
    """Fade the perp premium: short when perp trades rich to index, long when cheap (§8.4).

    Uses the last-known perp-vs-index basis in bps (causal, never stale-synthetic).
    A rich basis (perp above index) is shorted expecting convergence; a cheap basis
    is bought. Inside the deadband, flat.
    """

    params = template_params(basis_threshold_bps=20.0)

    def on_candle(self, candle: "Candle", history: list["Candle"], ctx: Any) -> list["Signal"]:
        basis = ctx.basis_bps(candle.market, self.params["venue"])
        if basis is None:
            return []
        threshold = self.params["basis_threshold_bps"]
        if basis > threshold:
            desired = "short"
        elif basis < -threshold:
            desired = "long"
        else:
            desired = "flat"
        return self._rebalance(candle.market, ctx, desired)


class OiMomentumStrategy(TemplateStrategy):
    """Open-interest momentum: rising OI confirms the price move to trade with (§8.4).

    Fresh capital entering (OI rising) alongside a rising price is a long; rising OI
    into a falling price is a short (new shorts pressing). Flat when OI is flat/falling
    (no conviction) or history is too short. OI is level-only in ctx, so the prior
    reading is held on the instance across bars — a legitimate stateful template.
    """

    params = template_params(lookback=5)

    def __init__(self, **overrides: Any) -> None:
        super().__init__(**overrides)
        self._prev_oi: float | None = None

    def on_candle(self, candle: "Candle", history: list["Candle"], ctx: Any) -> list["Signal"]:
        lookback = self.params["lookback"]
        oi_now = ctx.open_interest(candle.market, self.params["venue"])
        prev_oi = self._prev_oi
        if oi_now is not None:
            self._prev_oi = oi_now

        if oi_now is None or prev_oi is None or len(history) < lookback + 1:
            return []
        price_now = history[-1].close
        price_then = history[-1 - lookback].close
        oi_rising = oi_now > prev_oi
        if not oi_rising or price_now == price_then:
            desired = "flat"
        elif price_now > price_then:
            desired = "long"
        else:
            desired = "short"
        return self._rebalance(candle.market, ctx, desired)
