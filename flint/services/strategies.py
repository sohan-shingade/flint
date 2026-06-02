"""Shared strategy builder — single source of truth.

Both `flint.api.routes.backtest._build_strategy` and the paper route had
near-identical builder maps before D-4.7-full; this module is the one
they (and MCP tools) now share. Adding a new built-in template means
editing one dict here, not three.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional

from ..strategy import (
    ATRBreakoutStrategy,
    BasisTradeStrategy,
    BollingerStrategy,
    BreakoutMomentumStrategy,
    CryptoEvoStrategy,
    DualTimeframeStrategy,
    EMACrossoverStrategy,
    FundingArbStrategy,
    FundingHarvestStrategy,
    FundingMeanReversionStrategy,
    GridTraderStrategy,
    MACDDivergenceStrategy,
    MACrossoverStrategy,
    MeanReversionStrategy,
    MevArbMonitor,
    MomentumBreakoutStrategy,
    MomentumStrategy,
    MultiVenueFundingStrategy,
    RSIMACDComboStrategy,
    RSIStrategy,
    VWAPReversionStrategy,
)
from ..strategy.loader import load_user_strategy

_BUILTIN_BUILDERS = {
    "ma_crossover": lambda p: MACrossoverStrategy(
        fast_period=int(p.get("fast_period", 10)),
        slow_period=int(p.get("slow_period", 30)),
    ),
    "ema_crossover": lambda p: EMACrossoverStrategy(
        fast_period=int(p.get("fast_period", 12)),
        slow_period=int(p.get("slow_period", 26)),
    ),
    "rsi": lambda p: RSIStrategy(
        period=int(p.get("period", 14)),
        oversold=float(p.get("oversold", 30)),
        overbought=float(p.get("overbought", 70)),
    ),
    "bollinger": lambda p: BollingerStrategy(
        period=int(p.get("period", 20)),
        num_std=float(p.get("num_std", 2.0)),
    ),
    "momentum": lambda p: MomentumStrategy(
        lookback=int(p.get("lookback", 24)),
        threshold_pct=float(p.get("threshold_pct", 5.0)),
    ),
    "funding_harvest": lambda p: FundingHarvestStrategy(
        entry_threshold=float(p.get("entry_threshold", 0.001)),
        exit_threshold=float(p.get("exit_threshold", 0.0002)),
        stop_loss_pct=float(p.get("stop_loss_pct", 0.05)),
        lookback=int(p.get("lookback", 8)),
    ),
    "mean_reversion": lambda p: MeanReversionStrategy(
        period=int(p.get("period", 20)),
        entry_z=float(p.get("entry_z", 2.0)),
        exit_z=float(p.get("exit_z", 0.5)),
        stop_loss_pct=float(p.get("stop_loss_pct", 0.05)),
    ),
    "breakout_momentum": lambda p: BreakoutMomentumStrategy(),
    "grid_trader": lambda p: GridTraderStrategy(),
    "dual_timeframe": lambda p: DualTimeframeStrategy(),
    "vwap_reversion": lambda p: VWAPReversionStrategy(
        period=int(p.get("period", 20)),
        entry_pct=float(p.get("entry_pct", 2.0)),
        exit_pct=float(p.get("exit_pct", 0.5)),
    ),
    "macd_divergence": lambda p: MACDDivergenceStrategy(
        fast=int(p.get("fast", 12)),
        slow=int(p.get("slow", 26)),
        signal=int(p.get("signal", 9)),
    ),
    "atr_breakout": lambda p: ATRBreakoutStrategy(
        period=int(p.get("period", 20)),
        atr_period=int(p.get("atr_period", 14)),
        multiplier=float(p.get("multiplier", 2.0)),
    ),
    "multi_venue_funding": lambda p: MultiVenueFundingStrategy(
        entry_threshold=float(p.get("entry_threshold", 0.0005)),
        exit_threshold=float(p.get("exit_threshold", 0.0001)),
        lookback=int(p.get("lookback", 12)),
    ),
    "rsi_macd_combo": lambda p: RSIMACDComboStrategy(
        rsi_period=int(p.get("rsi_period", 14)),
        macd_fast=int(p.get("macd_fast", 12)),
        macd_slow=int(p.get("macd_slow", 26)),
        macd_signal=int(p.get("macd_signal", 9)),
        rsi_oversold=float(p.get("rsi_oversold", 30)),
        rsi_overbought=float(p.get("rsi_overbought", 70)),
    ),
    "funding_mean_reversion": lambda p: FundingMeanReversionStrategy(
        bb_lookback=int(p.get("bb_lookback", 24)),
        bb_std=float(p.get("bb_std", 2.0)),
        max_hold_hours=int(p.get("max_hold_hours", 12)),
        position_size_pct=float(p.get("position_size_pct", 0.5)),
        candle_resolution_s=int(p.get("candle_resolution_s", 3600)),
    ),
    "momentum_breakout": lambda p: MomentumBreakoutStrategy(
        breakout_lookback=int(p.get("breakout_lookback", 20)),
        trailing_stop_pct=float(p.get("trailing_stop_pct", 0.02)),
        oracle_confirmation=int(p.get("oracle_confirmation", 1)),
        candle_resolution_s=int(p.get("candle_resolution_s", 3600)),
    ),
    "funding_arb": lambda p: FundingArbStrategy(
        min_spread_bps=float(p.get("min_spread_bps", 5.0)),
        exit_spread_bps=float(p.get("exit_spread_bps", 1.0)),
        max_hold_hours=int(p.get("max_hold_hours", 24)),
        position_size_usd=float(p.get("position_size_usd", 1000.0)),
        min_spread_duration=int(p.get("min_spread_duration", 1)),
        candle_resolution_s=int(p.get("candle_resolution_s", 60)),
    ),
    "basis_trade": lambda p: BasisTradeStrategy(
        entry_basis_bps=float(p.get("entry_basis_bps", 30.0)),
        exit_basis_bps=float(p.get("exit_basis_bps", 5.0)),
        max_hold_hours=int(p.get("max_hold_hours", 12)),
        position_size_usd=float(p.get("position_size_usd", 1000.0)),
        candle_resolution_s=int(p.get("candle_resolution_s", 3600)),
    ),
    "crypto_evo": lambda p: CryptoEvoStrategy(
        fast_ema=int(p.get("fast_ema", 12)),
        slow_ema=int(p.get("slow_ema", 26)),
        signal_period=int(p.get("signal_period", 9)),
        bb_period=int(p.get("bb_period", 20)),
        bb_std_dev=float(p.get("bb_std_dev", 2.5)),
        signal_weight=float(p.get("signal_weight", 0.75)),
        vol_filter_on=int(p.get("vol_filter_on", 0)),
        vol_filter_regime=int(p.get("vol_filter_regime", 0)),
        atr_filter_on=int(p.get("atr_filter_on", 0)),
        atr_filter_regime=int(p.get("atr_filter_regime", 0)),
        trend_filter_on=int(p.get("trend_filter_on", 1)),
        trend_filter_regime=int(p.get("trend_filter_regime", 1)),
        max_position_size=float(p.get("max_position_size", 0.6)),
    ),
    "mev_arb_monitor": lambda p: MevArbMonitor(
        min_profit_bps=float(p.get("min_profit_bps", 10.0)),
        max_hops=int(p.get("max_hops", 3)),
        alert_enabled=int(p.get("alert_enabled", 0)),
        candle_resolution_s=int(p.get("candle_resolution_s", 60)),
    ),
}


def list_builtin_strategies() -> list[str]:
    """Names of every built-in strategy template."""
    return sorted(_BUILTIN_BUILDERS.keys())


def build_strategy(name: str, params: Optional[Dict] = None, code: Optional[str] = None):
    """Resolve a strategy reference into a live Strategy instance.

    Resolution order:
      1. `code` non-empty → compile and load as user strategy
      2. `name` starts with `user:` → load `strategies/user/{slug}.py`
      3. `name` matches a built-in → instantiate from the registry

    Returns None if the name cannot be resolved (route layers translate
    that into a 404). Callers must handle StrategyLoadError raised by
    `load_user_strategy` themselves — surfacing the validation message
    is a route concern.
    """
    p = params or {}

    if code:
        return load_user_strategy(code, params or None)

    if name.startswith("user:"):
        slug = name[5:]
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$", slug):
            return None
        user_dir = Path(__file__).resolve().parents[2] / "strategies" / "user"
        path = (user_dir / f"{slug}.py").resolve()
        if not str(path).startswith(str(user_dir.resolve())):
            return None
        if not path.exists():
            return None
        return load_user_strategy(path.read_text(encoding="utf-8"), params or None)

    builder = _BUILTIN_BUILDERS.get(name)
    return builder(p) if builder else None
