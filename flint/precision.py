"""Token precision, order size management, and Drift integer math.

Three layers:
1. Internal engine: float (fast, good enough for backtesting)
2. Boundary conversion: Decimal (exact, no float drift)
3. On-chain: integer (Drift protocol format)

Rules:
- Amounts are TRUNCATED (round down) — never buy more than intended
- Prices are ROUNDED (half-up) — nearest valid tick
- PnL uses round(x, 8) at accumulation points to prevent float drift
- On-chain conversions always go through Decimal to avoid float→int errors
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, Optional, Tuple

# ─── Drift Protocol integer precision constants ────────────

BASE_PRECISION = 10**9       # base_asset_amount
QUOTE_PRECISION = 10**6      # quote_asset_amount, USD values
PRICE_PRECISION = 10**6      # oracle prices, fill prices
FUNDING_PRECISION = 10**9    # funding rate
MARGIN_PRECISION = 10**4     # margin ratio

# ─── Solana token decimals ─────────────────────────────────

TOKEN_DECIMALS: Dict[str, int] = {
    "SOL": 9, "wSOL": 9,
    "USDC": 6, "USDT": 6,
    "BONK": 5, "1MBONK": 5,
    "PEPE": 5, "1MPEPE": 5,
    "JTO": 9, "JUP": 6, "WIF": 6,
    "DRIFT": 6, "PYTH": 6,
    "RENDER": 8, "RAY": 6,
    "POPCAT": 9, "HNT": 8,
}

# ─── Drift perp market specs: (tick_size, min_order_size, step_size) ──

PERP_SPECS: Dict[str, Tuple[float, float, float]] = {
    "SOL-PERP":     (0.001,   0.1,    0.1),
    "BTC-PERP":     (0.01,    0.001,  0.001),
    "ETH-PERP":     (0.01,    0.01,   0.01),
    "APT-PERP":     (0.001,   0.1,    0.1),
    "1MBONK-PERP":  (0.000001, 1000,  1000),
    "DOGE-PERP":    (0.00001,  1,     1),
    "SUI-PERP":     (0.0001,   0.1,   0.1),
    "ARB-PERP":     (0.0001,   1,     1),
    "LINK-PERP":    (0.001,    0.1,   0.1),
    "AVAX-PERP":    (0.001,    0.1,   0.1),
    "XRP-PERP":     (0.0001,   1,     1),
    "WIF-PERP":     (0.0001,   0.1,   0.1),
    "JUP-PERP":     (0.0001,   1,     1),
    "INJ-PERP":     (0.001,    0.1,   0.1),
    "RENDER-PERP":  (0.001,    0.1,   0.1),
    "DRIFT-PERP":   (0.0001,   1,     1),
    "PYTH-PERP":    (0.0001,   1,     1),
    "OP-PERP":      (0.001,    0.1,   0.1),
    "BNB-PERP":     (0.01,     0.01,  0.01),
    "1MPEPE-PERP":  (0.000001, 1000,  1000),
    "TIA-PERP":     (0.001,    0.1,   0.1),
    "SEI-PERP":     (0.0001,   1,     1),
    "JTO-PERP":     (0.001,    0.1,   0.1),
    "TAO-PERP":     (0.01,     0.01,  0.01),
    "POPCAT-PERP":  (0.0001,   1,     1),
}


# ─── Amount & price precision ─────────────────────────────

def amount_to_precision(size: float, step_size: float) -> float:
    """Truncate order size to valid step size (always round DOWN).

    >>> amount_to_precision(1.23456, 0.01)
    1.23
    >>> amount_to_precision(0.0078, 0.001)
    0.007
    """
    d_size = Decimal(str(size))
    d_step = Decimal(str(step_size))
    if d_step == 0:
        return size
    truncated = (d_size / d_step).to_integral_value(rounding=ROUND_DOWN) * d_step
    return float(truncated)


def price_to_precision(price: float, tick_size: float) -> float:
    """Round price to nearest valid tick (round half-up).

    >>> price_to_precision(142.3456, 0.001)
    142.346
    """
    d_price = Decimal(str(price))
    d_tick = Decimal(str(tick_size))
    if d_tick == 0:
        return price
    rounded = (d_price / d_tick).to_integral_value(rounding=ROUND_HALF_UP) * d_tick
    return float(rounded)


def round_pnl(value: float, decimals: int = 8) -> float:
    """Round PnL/equity values to prevent float drift accumulation."""
    return round(value, decimals)


# ─── Drift integer conversions ────────────────────────────

def to_drift_base(size: float) -> int:
    """Convert human-readable size to Drift base_asset_amount (1e9).

    Uses Decimal to avoid float→int precision errors.
    >>> to_drift_base(1.5)
    1500000000
    """
    return int(Decimal(str(size)) * BASE_PRECISION)


def from_drift_base(raw: int) -> float:
    """Convert Drift base_asset_amount to human-readable size."""
    return float(Decimal(raw) / BASE_PRECISION)


def to_drift_price(price: float) -> int:
    """Convert human-readable price to Drift price integer (1e6).

    >>> to_drift_price(150.25)
    150250000
    """
    return int(Decimal(str(price)) * PRICE_PRECISION)


def from_drift_price(raw: int) -> float:
    """Convert Drift price integer to human-readable price."""
    return float(Decimal(raw) / PRICE_PRECISION)


def to_drift_funding(rate: float) -> int:
    """Convert human funding rate to Drift integer (1e9)."""
    return int(Decimal(str(rate)) * FUNDING_PRECISION)


def from_drift_funding(raw: int) -> float:
    """Convert Drift funding integer to human rate."""
    return float(Decimal(raw) / FUNDING_PRECISION)


# ─── SPL token conversions ───────────────────────────────

def to_base_units(amount: float, token: str) -> int:
    """Convert human token amount to on-chain base units.

    >>> to_base_units(1.5, "SOL")
    1500000000
    >>> to_base_units(100.0, "USDC")
    100000000
    """
    decimals = TOKEN_DECIMALS.get(token, 6)
    return int(Decimal(str(amount)) * 10**decimals)


def from_base_units(raw: int, token: str) -> float:
    """Convert on-chain base units to human token amount."""
    decimals = TOKEN_DECIMALS.get(token, 6)
    return float(Decimal(raw) / 10**decimals)


# ─── Order validation ────────────────────────────────────

def validate_order_size(
    size: float,
    market: str,
    price: Optional[float] = None,
    min_notional: float = 0.0,
) -> float:
    """Validate and truncate order size for a Drift market.

    Returns adjusted size. Raises ValueError if below minimum.
    """
    spec = PERP_SPECS.get(market)
    if spec is None:
        return size

    tick_size, min_size, step_size = spec
    adjusted = amount_to_precision(size, step_size)

    if adjusted < min_size:
        raise ValueError(f"Order size {adjusted} below minimum {min_size} for {market}")

    if price and min_notional > 0:
        notional = adjusted * price
        if notional < min_notional:
            raise ValueError(f"Notional ${notional:.2f} below minimum ${min_notional:.2f}")

    return adjusted


def get_market_specs(market: str) -> Optional[Tuple[float, float, float]]:
    """Get (tick_size, min_order_size, step_size) for a market, or None."""
    return PERP_SPECS.get(market)
