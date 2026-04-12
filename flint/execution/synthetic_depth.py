"""Synthetic orderbook depth model.

Generates realistic orderbook snapshots from per-venue depth profiles.
Fallback when real data (Drift S3, Hyperliquid archive, Tardis) is unavailable.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Dict
from ..models import OrderbookLevel, OrderbookSnapshot


@dataclass(frozen=True)
class DepthProfile:
    """Venue depth characteristics for BTC-PERP baseline.
    Other markets scale linearly by relative volume.
    """
    bid_depth_1pct: float    # USD liquidity within 1% of mid (bid side)
    ask_depth_1pct: float    # USD liquidity within 1% of mid (ask side)
    concentration: float     # How concentrated at top-of-book (0-1)
    spread_bps: float        # Typical bid-ask spread in basis points


VENUE_PROFILES: Dict[str, DepthProfile] = {
    "binance": DepthProfile(20_000_000, 20_000_000, 0.7, 0.5),
    "coinbase": DepthProfile(15_000_000, 15_000_000, 0.65, 0.8),
    "okx": DepthProfile(10_000_000, 10_000_000, 0.6, 1.0),
    "bybit": DepthProfile(8_000_000, 8_000_000, 0.6, 1.0),
    "kraken": DepthProfile(4_000_000, 4_000_000, 0.55, 1.5),
    "kucoin": DepthProfile(6_000_000, 6_000_000, 0.55, 1.2),
    "bitget": DepthProfile(6_000_000, 6_000_000, 0.55, 1.2),
    "gate": DepthProfile(3_000_000, 3_000_000, 0.5, 2.0),
    "mexc": DepthProfile(2_000_000, 2_000_000, 0.45, 2.5),
    "htx": DepthProfile(3_000_000, 3_000_000, 0.5, 2.0),
    "hyperliquid": DepthProfile(5_000_000, 5_000_000, 0.5, 1.5),
    "drift": DepthProfile(2_000_000, 2_000_000, 0.4, 3.0),
}


def generate_synthetic_book(
    mid_price: float, profile: DepthProfile, levels: int = 20,
    market: str = "", ts: int = 0,
) -> OrderbookSnapshot:
    """Generate a synthetic orderbook from a depth profile.

    Uses exponential decay controlled by concentration parameter.
    """
    half_spread = (profile.spread_bps / 10_000) * mid_price / 2
    step = (mid_price * 0.01) / levels  # 1% range / N levels

    decay = 1.0 + profile.concentration * 4  # 0→1 maps to 1→5
    weights = [math.exp(-decay * i / levels) for i in range(levels)]
    total_weight = sum(weights)

    bids, asks = [], []
    for i in range(levels):
        frac = weights[i] / total_weight
        bid_price = mid_price - half_spread - i * step
        bid_usd = profile.bid_depth_1pct * frac
        bid_size = bid_usd / bid_price if bid_price > 0 else 0
        bids.append(OrderbookLevel(price=round(bid_price, 6), size=round(bid_size, 4)))

        ask_price = mid_price + half_spread + i * step
        ask_usd = profile.ask_depth_1pct * frac
        ask_size = ask_usd / ask_price if ask_price > 0 else 0
        asks.append(OrderbookLevel(price=round(ask_price, 6), size=round(ask_size, 4)))

    return OrderbookSnapshot(market=market, ts=ts, bids=tuple(bids), asks=tuple(asks))
