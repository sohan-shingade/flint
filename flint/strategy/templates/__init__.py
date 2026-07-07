"""strategy.templates — the built-in strategies, one registry (§8.4).

Importing this package registers every built-in template into the single
:mod:`.registry`. Surfaces (backtest/paper/MCP) read the registry — they never
hardcode a template list — so a template added here shows up everywhere at once.
Perp-native first (funding harvest, dislocation, momentum, SVD, basis, OI momentum),
then the classic technical set (MA/EMA cross, RSI, Bollinger, breakout, VWAP
reversion, Keltner, MACD), plus one ML template (LightGBM). Every template emits
Signals only — never ``ctx.submit_order``.
"""

from __future__ import annotations

from .funding_svd import FundingSvdStrategy
from .ml_template import LightGbmTrendStrategy
from .perp import (
    BasisTradeStrategy,
    FundingDislocationStrategy,
    FundingHarvestStrategy,
    FundingMomentumStrategy,
    OiMomentumStrategy,
)
from .registry import (
    TemplateNotFound,
    TemplateSpec,
    get_template,
    list_templates,
    register,
    template_names,
)
from .technical import (
    BollingerStrategy,
    BreakoutStrategy,
    KeltnerStrategy,
    MacdStrategy,
    MaCrossStrategy,
    RsiReversionStrategy,
    VwapReversionStrategy,
)

# --- register the built-ins (single source of truth, §8.4) -------------------

register(TemplateSpec(
    "funding_harvest", FundingHarvestStrategy,
    "Hold the side that receives funding when the hourly rate clears a deadband.",
    "funding",
))
register(TemplateSpec(
    "funding_dislocation", FundingDislocationStrategy,
    "Trade the HL leg when its funding dislocates from a benchmark rate.",
    "funding",
))
register(TemplateSpec(
    "funding_svd", FundingSvdStrategy,
    "Rank-1 SVD funding factor: fade each market's residual funding dislocation.",
    "funding",
))
register(TemplateSpec(
    "basis_trade", BasisTradeStrategy,
    "Fade the perp premium: short a rich basis, long a cheap one.",
    "basis",
))
register(TemplateSpec(
    "funding_momentum", FundingMomentumStrategy,
    "Trade the drift of predicted funding: short a richening rate, long a cheapening one.",
    "funding",
))
register(TemplateSpec(
    "oi_momentum", OiMomentumStrategy,
    "Trade with price when rising open interest confirms the move.",
    "flow",
))
register(TemplateSpec(
    "ma_cross", MaCrossStrategy,
    "Moving-average cross trend follower (SMA or EMA).",
    "technical",
))
register(TemplateSpec(
    "rsi_reversion", RsiReversionStrategy,
    "RSI mean reversion: long oversold, short overbought.",
    "technical",
))
register(TemplateSpec(
    "bollinger", BollingerStrategy,
    "Bollinger-band mean reversion around a moving average.",
    "technical",
))
register(TemplateSpec(
    "breakout", BreakoutStrategy,
    "Donchian channel breakout of the prior high/low.",
    "technical",
))
register(TemplateSpec(
    "vwap_reversion", VwapReversionStrategy,
    "Fade closes stretched beyond a band around rolling VWAP.",
    "technical",
))
register(TemplateSpec(
    "keltner", KeltnerStrategy,
    "Keltner-channel trend rider: enter on an ATR-band break, hold inside it.",
    "technical",
))
register(TemplateSpec(
    "macd", MacdStrategy,
    "MACD trend follower: long above the signal line, short below.",
    "technical",
))
register(TemplateSpec(
    "lgbm_trend", LightGbmTrendStrategy,
    "LightGBM trend classifier on bounded momentum features (ML).",
    "ml", is_ml=True,
))

__all__ = [
    # registry API
    "TemplateSpec",
    "TemplateNotFound",
    "register",
    "get_template",
    "list_templates",
    "template_names",
    # template classes
    "FundingHarvestStrategy",
    "FundingDislocationStrategy",
    "FundingMomentumStrategy",
    "FundingSvdStrategy",
    "BasisTradeStrategy",
    "OiMomentumStrategy",
    "MaCrossStrategy",
    "RsiReversionStrategy",
    "BollingerStrategy",
    "BreakoutStrategy",
    "VwapReversionStrategy",
    "KeltnerStrategy",
    "MacdStrategy",
    "LightGbmTrendStrategy",
]
