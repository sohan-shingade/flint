"""Strategy listing API — generated dynamically from backtest builders."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .backtest import _build_strategy, _DEFAULTS

router = APIRouter()

# Static metadata per strategy.  Keys that are present here will be merged
# into the catalog entry; everything else (name, params) is derived at
# import time from the strategy class itself.
_METADATA: dict[str, dict] = {
    "ma_crossover": {
        "display_name": "MA Crossover",
        "description": "Goes long when fast SMA crosses above slow SMA, exits when it crosses below. Classic trend-following.",
        "type": "single_venue",
        "venues": [],
    },
    "ema_crossover": {
        "display_name": "EMA Crossover",
        "description": "Exponential moving-average crossover. Reacts faster to recent price changes than SMA. Good for trending markets.",
        "type": "single_venue",
        "venues": [],
    },
    "rsi": {
        "display_name": "RSI Mean Reversion",
        "description": "Buys when RSI drops below oversold threshold, sells when it rises above overbought. Mean-reversion style.",
        "type": "single_venue",
        "venues": [],
    },
    "bollinger": {
        "display_name": "Bollinger Bands",
        "description": "Buys at the lower band (oversold), sells at the upper band (overbought). Works best in ranging markets.",
        "type": "single_venue",
        "venues": [],
    },
    "momentum": {
        "display_name": "Momentum",
        "description": "Buys when price is up X% over a lookback window, sells when down X%. Rides strong moves.",
        "type": "single_venue",
        "venues": [],
    },
    "momentum_breakout": {
        "display_name": "Momentum Breakout",
        "description": "Breakout above N-bar high/low with optional Pyth oracle confirmation.",
        "type": "single_venue",
        "venues": [],
    },
    "funding_harvest": {
        "display_name": "Funding Harvest",
        "description": "Captures funding payments by holding positions when funding rate is favorable.",
        "type": "single_venue",
        "venues": [],
        "needs_funding": True,
    },
    "mean_reversion": {
        "display_name": "Mean Reversion",
        "description": "Z-score based mean reversion. Enters when price deviates from moving average by entry_z std devs.",
        "type": "single_venue",
        "venues": [],
    },
    "breakout_momentum": {
        "display_name": "Breakout Momentum",
        "description": "Momentum strategy with breakout detection and trailing stops.",
        "type": "single_venue",
        "venues": [],
    },
    "grid_trader": {
        "display_name": "Grid Trader",
        "description": "Places a grid of buy/sell orders around current price. Profits from range-bound markets.",
        "type": "single_venue",
        "venues": [],
    },
    "dual_timeframe": {
        "display_name": "Dual Timeframe",
        "description": "Combines two timeframe signals for higher-confidence entries.",
        "type": "single_venue",
        "venues": [],
    },
    "vwap_reversion": {
        "display_name": "VWAP Reversion",
        "description": "Mean reversion to VWAP. Buys below VWAP, sells above.",
        "type": "single_venue",
        "venues": [],
    },
    "macd_divergence": {
        "display_name": "MACD Divergence",
        "description": "Trades MACD histogram divergences from price for reversal signals.",
        "type": "single_venue",
        "venues": [],
    },
    "atr_breakout": {
        "display_name": "ATR Breakout",
        "description": "Breakout strategy using ATR-based volatility bands for dynamic entry/exit levels.",
        "type": "single_venue",
        "venues": [],
    },
    "multi_venue_funding": {
        "display_name": "Multi-Venue Funding",
        "description": "Harvests funding rate differences across multiple venues.",
        "type": "multi_venue",
        "venues": ["drift", "hyperliquid"],
        "needs_funding": True,
    },
    "rsi_macd_combo": {
        "display_name": "RSI + MACD Combo",
        "description": "Combines RSI and MACD signals for higher-confidence entries.",
        "type": "single_venue",
        "venues": [],
    },
    "funding_mean_reversion": {
        "display_name": "Funding Mean Reversion",
        "description": "Bollinger bands on hourly funding rate. Needs funding data downloaded.",
        "type": "single_venue",
        "venues": [],
        "needs_funding": True,
    },
    "funding_arb": {
        "display_name": "Funding Arb (Cross-Venue)",
        "description": "Delta-neutral cross-venue funding arbitrage. Long low-rate venue, short high-rate.",
        "type": "multi_venue",
        "venues": ["drift", "hyperliquid"],
        "needs_funding": True,
    },
    "basis_trade": {
        "display_name": "Basis Trade (Cross-Venue)",
        "description": "Cross-venue price basis arbitrage. Long cheap venue, short expensive.",
        "type": "multi_venue",
        "venues": ["drift", "hyperliquid"],
    },
    "mev_arb_monitor": {
        "display_name": "MEV Arb Monitor",
        "description": "Scans for DEX arb opportunities. Monitoring only \u2014 no trades placed.",
        "type": "monitor",
        "venues": [],
    },
}


def _build_catalog() -> list[dict]:
    """Build the strategy catalog from _DEFAULTS + _METADATA + strategy classes."""
    catalog: list[dict] = []
    for name, defaults in _DEFAULTS.items():
        # Instantiate the strategy to read its parameters()
        strat = _build_strategy(name, defaults)
        if strat is None:
            continue

        # Read Optuna-style parameters from the class
        try:
            raw_params = strat.parameters()
        except Exception:
            raw_params = {}

        # Convert parameters to the API format (type, default, min, max)
        params: dict[str, dict] = {}
        for pname, pinfo in raw_params.items():
            entry: dict = {"type": pinfo.get("type", "float")}
            if "default" in pinfo:
                entry["default"] = pinfo["default"]
            if "low" in pinfo:
                entry["min"] = pinfo["low"]
            if "high" in pinfo:
                entry["max"] = pinfo["high"]
            params[pname] = entry

        meta = _METADATA.get(name, {})
        item: dict = {
            "name": name,
            "display_name": meta.get("display_name", name.replace("_", " ").title()),
            "description": meta.get("description", ""),
            "params": params,
            "markets": meta.get("markets", ["SOL-PERP", "BTC-PERP", "ETH-PERP"]),
            "type": meta.get("type", "single_venue"),
            "venues": meta.get("venues", []),
        }
        if meta.get("needs_funding"):
            item["needs_funding"] = True

        catalog.append(item)
    return catalog


# Build once at import time
_STRATEGIES = _build_catalog()


@router.get("")
def list_strategies():
    return {"strategies": _STRATEGIES}


@router.get("/{name}")
def get_strategy(name: str):
    for s in _STRATEGIES:
        if s["name"] == name:
            return s
    raise HTTPException(404, f"Strategy '{name}' not found")
