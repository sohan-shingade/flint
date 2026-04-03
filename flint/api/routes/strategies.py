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
        "type": "single_venue",
        "venues": [],
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
        "type": "single_venue",
        "venues": [],
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
        "type": "single_venue",
        "venues": [],
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
        "type": "single_venue",
        "venues": [],
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
        "type": "single_venue",
        "venues": [],
    },
    {
        "name": "momentum_breakout",
        "display_name": "Momentum Breakout",
        "description": "Breakout above N-bar high/low with optional Pyth oracle confirmation.",
        "params": {
            "breakout_lookback": {"type": "int", "default": 20, "min": 10, "max": 50},
            "trailing_stop_pct": {"type": "float", "default": 0.02, "min": 0.01, "max": 0.05},
        },
        "markets": ["SOL-PERP", "BTC-PERP", "ETH-PERP"],
        "type": "single_venue",
        "venues": [],
    },
    {
        "name": "funding_mean_reversion",
        "display_name": "Funding Mean Reversion",
        "description": "Bollinger bands on hourly funding rate. Needs funding data downloaded.",
        "params": {
            "bb_lookback": {"type": "int", "default": 24, "min": 12, "max": 48},
            "bb_std": {"type": "float", "default": 2.0, "min": 1.5, "max": 3.0},
            "max_hold_hours": {"type": "int", "default": 12, "min": 4, "max": 24},
        },
        "markets": ["SOL-PERP", "BTC-PERP", "ETH-PERP"],
        "type": "single_venue",
        "venues": [],
        "needs_funding": True,
    },
    {
        "name": "funding_arb",
        "display_name": "Funding Arb (Cross-Venue)",
        "description": "Delta-neutral cross-venue funding arbitrage. Long low-rate venue, short high-rate.",
        "params": {
            "min_spread_bps": {"type": "float", "default": 5.0, "min": 3.0, "max": 20.0},
            "exit_spread_bps": {"type": "float", "default": 1.0, "min": 0.5, "max": 5.0},
            "position_size_usd": {"type": "float", "default": 1000, "min": 100, "max": 10000},
        },
        "markets": ["SOL-PERP", "BTC-PERP", "ETH-PERP"],
        "type": "multi_venue",
        "venues": ["drift", "hyperliquid"],
        "needs_funding": True,
    },
    {
        "name": "basis_trade",
        "display_name": "Basis Trade (Cross-Venue)",
        "description": "Cross-venue price basis arbitrage. Long cheap venue, short expensive.",
        "params": {
            "entry_basis_bps": {"type": "float", "default": 30.0, "min": 10.0, "max": 100.0},
            "exit_basis_bps": {"type": "float", "default": 5.0, "min": 2.0, "max": 20.0},
            "position_size_usd": {"type": "float", "default": 1000, "min": 100, "max": 10000},
        },
        "markets": ["SOL-PERP", "BTC-PERP"],
        "type": "multi_venue",
        "venues": ["drift", "hyperliquid"],
    },
    {
        "name": "mev_arb_monitor",
        "display_name": "MEV Arb Monitor",
        "description": "Scans for DEX arb opportunities. Monitoring only — no trades placed.",
        "params": {
            "min_profit_bps": {"type": "float", "default": 10.0, "min": 5.0, "max": 50.0},
            "max_hops": {"type": "int", "default": 3, "min": 2, "max": 4},
        },
        "markets": ["SOL-PERP"],
        "type": "monitor",
        "venues": [],
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
    from fastapi import HTTPException
    raise HTTPException(404, f"Strategy '{name}' not found")
