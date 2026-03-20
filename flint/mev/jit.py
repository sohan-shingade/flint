"""JIT (Just-In-Time) liquidity detection for Drift protocol.

Drift uses a JIT auction system where makers can fill taker orders
in the ~5-second window before they hit the AMM. JIT fillers earn the
maker rebate while providing better execution for the taker.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..models import OrderbookLevel, OrderbookSnapshot, Side


@dataclass(frozen=True)
class JitOpportunity:
    """A detected JIT fill opportunity."""
    market: str
    taker_side: Side
    taker_size: float
    taker_price: float  # worst-case price for taker (AMM price)
    jit_price: float  # price the JIT filler would offer
    spread_bps: float  # spread between JIT price and AMM price
    estimated_rebate: float  # maker rebate earned


class JitDetector:
    """Detects JIT liquidity opportunities from orderbook state.

    In production, this would monitor Drift's event stream for incoming
    taker orders and compute whether filling them as a JIT maker is
    profitable. For backtesting, it simulates this against historical
    orderbook data.

    Usage::

        detector = JitDetector(maker_rebate=0.0002)
        opps = detector.scan_orderbook(snapshot, vamm_price=89.50)
    """

    def __init__(
        self,
        maker_rebate: float = 0.0002,  # 2 bps rebate
        min_spread_bps: float = 5.0,
        min_size: float = 0.1,
    ) -> None:
        self.maker_rebate = maker_rebate
        self.min_spread_bps = min_spread_bps
        self.min_size = min_size

    def scan_orderbook(
        self,
        snapshot: OrderbookSnapshot,
        vamm_price: float,
    ) -> List[JitOpportunity]:
        """Check if there are JIT opportunities given current orderbook and vAMM price.

        A JIT opportunity exists when the vAMM price is significantly worse
        than the DLOB price, meaning a JIT filler can offer a better price
        than the AMM and still earn the maker rebate.
        """
        opportunities: List[JitOpportunity] = []

        # Check bid side: taker wants to sell, vAMM bid is lower
        for bid in snapshot.bids:
            if bid.size < self.min_size:
                continue
            # vAMM bid price would be lower → JIT can bid higher than vAMM
            spread_bps = (bid.price - vamm_price) / vamm_price * 10_000
            if spread_bps >= self.min_spread_bps:
                jit_price = (bid.price + vamm_price) / 2  # split the spread
                rebate = jit_price * bid.size * self.maker_rebate
                opportunities.append(JitOpportunity(
                    market=snapshot.market,
                    taker_side=Side.SHORT,  # taker is selling
                    taker_size=bid.size,
                    taker_price=vamm_price,
                    jit_price=jit_price,
                    spread_bps=spread_bps,
                    estimated_rebate=rebate,
                ))

        # Check ask side: taker wants to buy, vAMM ask is higher
        for ask in snapshot.asks:
            if ask.size < self.min_size:
                continue
            spread_bps = (vamm_price - ask.price) / vamm_price * 10_000
            if spread_bps >= self.min_spread_bps:
                jit_price = (ask.price + vamm_price) / 2
                rebate = jit_price * ask.size * self.maker_rebate
                opportunities.append(JitOpportunity(
                    market=snapshot.market,
                    taker_side=Side.LONG,  # taker is buying
                    taker_size=ask.size,
                    taker_price=vamm_price,
                    jit_price=jit_price,
                    spread_bps=spread_bps,
                    estimated_rebate=rebate,
                ))

        opportunities.sort(key=lambda o: o.estimated_rebate, reverse=True)
        return opportunities
