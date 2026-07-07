"""The classic technical templates: MA/EMA cross, RSI, Bollinger, breakout (§8.4).

Each reads only the ``history`` the engine hands it (closed bars, current just-closed
bar last) through the bounded indicators in :mod:`.indicators`, so there is no
look-ahead. All express a desired state via ``self._rebalance`` and emit Signals only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import indicators as ind
from ._base import TemplateStrategy, template_params

if TYPE_CHECKING:
    from flint.core.models import Candle, Signal


class MaCrossStrategy(TemplateStrategy):
    """Trend-following moving-average cross: long above, short below (§8.4).

    Long when the fast average is above the slow one, short when below — a reversal
    system that is always in the market once both averages have warmed up.
    """

    params = template_params(fast=10, slow=30, ma_type="ema")

    def on_candle(self, candle: "Candle", history: list["Candle"], ctx: Any) -> list["Signal"]:
        series = ind.closes(history)
        avg = ind.ema if self.params["ma_type"] == "ema" else ind.sma
        fast = avg(series, self.params["fast"])
        slow = avg(series, self.params["slow"])
        if fast is None or slow is None or fast == slow:
            return []
        return self._rebalance(candle.market, ctx, "long" if fast > slow else "short")


class RsiReversionStrategy(TemplateStrategy):
    """Mean-reversion on RSI: long when oversold, short when overbought (§8.4)."""

    params = template_params(period=14, oversold=30.0, overbought=70.0)

    def on_candle(self, candle: "Candle", history: list["Candle"], ctx: Any) -> list["Signal"]:
        value = ind.rsi(ind.closes(history), self.params["period"])
        if value is None:
            return []
        if value < self.params["oversold"]:
            desired = "long"
        elif value > self.params["overbought"]:
            desired = "short"
        else:
            desired = "flat"
        return self._rebalance(candle.market, ctx, desired)


class BollingerStrategy(TemplateStrategy):
    """Bollinger-band mean reversion: long below the lower band, short above upper (§8.4)."""

    params = template_params(period=20, k=2.0)

    def on_candle(self, candle: "Candle", history: list["Candle"], ctx: Any) -> list["Signal"]:
        series = ind.closes(history)
        mid = ind.sma(series, self.params["period"])
        sd = ind.stdev(series, self.params["period"])
        if mid is None or sd is None:
            return []
        upper = mid + self.params["k"] * sd
        lower = mid - self.params["k"] * sd
        close = series[-1]
        if close < lower:
            desired = "long"
        elif close > upper:
            desired = "short"
        else:
            desired = "flat"
        return self._rebalance(candle.market, ctx, desired)


class BreakoutStrategy(TemplateStrategy):
    """Donchian breakout: long above the prior ``lookback`` high, short below its low (§8.4).

    The channel is built from the bars *before* the current one, so the current
    close breaking it is a genuine breakout, not the bar defining its own channel.
    """

    params = template_params(lookback=20)

    def on_candle(self, candle: "Candle", history: list["Candle"], ctx: Any) -> list["Signal"]:
        lookback = self.params["lookback"]
        if len(history) < lookback + 1:
            return []
        prior = history[:-1]  # exclude the current bar from the channel
        prior_high = ind.highest([c.high for c in prior], lookback)
        prior_low = ind.lowest([c.low for c in prior], lookback)
        if prior_high is None or prior_low is None:
            return []
        close = history[-1].close
        if close > prior_high:
            desired = "long"
        elif close < prior_low:
            desired = "short"
        else:
            desired = "flat"
        return self._rebalance(candle.market, ctx, desired)


class VwapReversionStrategy(TemplateStrategy):
    """Mean-reversion to rolling VWAP: fade closes stretched beyond a band (§8.4).

    VWAP is the volume-weighted typical price over the trailing ``period`` bars;
    a close more than ``band_pct`` percent away is faded back toward it. Inside
    the band, flat — the stretch does not clear costs.
    """

    params = template_params(period=20, band_pct=1.0)

    def on_candle(self, candle: "Candle", history: list["Candle"], ctx: Any) -> list["Signal"]:
        anchor = ind.vwap(history, self.params["period"])
        if anchor is None or anchor <= 0:
            return []
        stretch_pct = (history[-1].close - anchor) / anchor * 100.0
        if stretch_pct < -self.params["band_pct"]:
            desired = "long"
        elif stretch_pct > self.params["band_pct"]:
            desired = "short"
        else:
            desired = "flat"
        return self._rebalance(candle.market, ctx, desired)


class KeltnerStrategy(TemplateStrategy):
    """Keltner-channel trend rider: enter on a band break, hold until the flip (§8.4).

    Bands are an EMA midline ± ``k`` ATRs. Unlike Bollinger (stdev of closes,
    faded), Keltner bands are range-based and *followed*: a close beyond a band
    opens with the move, and inside the channel the position is simply held
    (``[]`` = no churn) rather than flattened — chop stays un-traded.
    """

    params = template_params(period=20, k=2.0)

    def on_candle(self, candle: "Candle", history: list["Candle"], ctx: Any) -> list["Signal"]:
        period = self.params["period"]
        mid = ind.ema(ind.closes(history), period)
        band = ind.atr(history, period)
        if mid is None or band is None:
            return []
        close = history[-1].close
        if close > mid + self.params["k"] * band:
            return self._rebalance(candle.market, ctx, "long")
        if close < mid - self.params["k"] * band:
            return self._rebalance(candle.market, ctx, "short")
        return []  # inside the channel: hold what is held


class MacdStrategy(TemplateStrategy):
    """MACD trend follower: long when the MACD line is above its signal line (§8.4)."""

    params = template_params(fast=12, slow=26, signal=9)

    def on_candle(self, candle: "Candle", history: list["Candle"], ctx: Any) -> list["Signal"]:
        pair = ind.macd(
            ind.closes(history),
            fast=self.params["fast"],
            slow=self.params["slow"],
            signal=self.params["signal"],
        )
        if pair is None:
            return []
        line, signal_line = pair
        if line == signal_line:
            return []
        desired = "long" if line > signal_line else "short"
        return self._rebalance(candle.market, ctx, desired)
