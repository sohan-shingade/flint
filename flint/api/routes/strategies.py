"""Strategy listing API."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

_STRATEGIES = [
    {
        "name": "ma_crossover",
        "display_name": "MA Crossover",
        "description": "Goes long when fast SMA crosses above slow SMA, exits when it crosses below. Classic trend-following.",
        "params": {
            "fast_period": {"type": "int", "default": 10, "min": 2, "max": 200},
            "slow_period": {"type": "int", "default": 30, "min": 5, "max": 500},
        },
        "markets": ["SOL-PERP", "BTC-PERP", "ETH-PERP"],
    },
    {
        "name": "ema_crossover",
        "display_name": "EMA Crossover",
        "description": "Exponential moving-average crossover. Reacts faster to recent price changes than SMA. Good for trending markets.",
        "params": {
            "fast_period": {"type": "int", "default": 12, "min": 2, "max": 200},
            "slow_period": {"type": "int", "default": 26, "min": 5, "max": 500},
        },
        "markets": ["SOL-PERP", "BTC-PERP", "ETH-PERP"],
    },
    {
        "name": "rsi",
        "display_name": "RSI Mean Reversion",
        "description": "Buys when RSI drops below oversold threshold, sells when it rises above overbought. Mean-reversion style.",
        "params": {
            "period": {"type": "int", "default": 14, "min": 2, "max": 100},
            "oversold": {"type": "float", "default": 30, "min": 5, "max": 50},
            "overbought": {"type": "float", "default": 70, "min": 50, "max": 95},
        },
        "markets": ["SOL-PERP", "BTC-PERP", "ETH-PERP"],
    },
    {
        "name": "bollinger",
        "display_name": "Bollinger Bands",
        "description": "Buys at the lower band (oversold), sells at the upper band (overbought). Works best in ranging markets.",
        "params": {
            "period": {"type": "int", "default": 20, "min": 5, "max": 200},
            "num_std": {"type": "float", "default": 2.0, "min": 0.5, "max": 4.0},
        },
        "markets": ["SOL-PERP", "BTC-PERP", "ETH-PERP"],
    },
    {
        "name": "momentum",
        "display_name": "Momentum",
        "description": "Buys when price is up X% over a lookback window, sells when down X%. Rides strong moves.",
        "params": {
            "lookback": {"type": "int", "default": 24, "min": 2, "max": 200},
            "threshold_pct": {"type": "float", "default": 5.0, "min": 0.5, "max": 50.0},
        },
        "markets": ["SOL-PERP", "BTC-PERP", "ETH-PERP"],
    },
]


@router.get("")
def list_strategies():
    return {"strategies": _STRATEGIES}


@router.get("/{name}")
def get_strategy(name: str):
    for s in _STRATEGIES:
        if s["name"] == name:
            return s
    return {"error": f"Strategy '{name}' not found"}, 404
