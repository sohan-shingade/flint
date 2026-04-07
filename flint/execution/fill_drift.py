"""DriftFillModel — 3-tier execution pipeline for Drift protocol.

Execution order for a single market order:

  Tier 1 — JIT Dutch Auction (~60% of flow)
      Market makers compete in a short auction and fill at 1-3 bps better
      than oracle price.  Controlled by `jit_fill_probability`.

  Tier 2 — DLOB Walk (~30% of flow)
      Remaining size is walked against resting orders from Drift's
      Decentralised Limit Order Book.  Uses a real `OrderbookSnapshot`
      when provided via `set_orderbook()`; falls back to a synthetic
      depth profile calibrated to Drift's typical liquidity.

  Tier 3 — vAMM Backstop (~10% of flow)
      Any size not absorbed by Tiers 1-2 is filled against Drift's
      constant-product virtual AMM.  Uses the existing `VammCurve` with
      per-market `sqrt_k` values from `DEFAULT_SQRT_K`.

The final fill price is the volume-weighted average across all tiers.
"""
from __future__ import annotations

import random
from typing import Optional

from ..models import Candle, Fill, Order, OrderbookSnapshot, Side
from .fill_models import FillModel
from .synthetic_depth import VENUE_PROFILES, generate_synthetic_book
from .vamm import DEFAULT_SQRT_K, VammCurve

_FALLBACK_SQRT_K = 1_000_000


class DriftFillModel(FillModel):
    """Simulate Drift's 3-tier execution: JIT auction -> DLOB -> vAMM.

    Parameters
    ----------
    jit_fill_probability:
        Probability [0, 1] that the JIT auction activates for a given order.
        Default 0.6 reflects Drift's empirical ~60% JIT participation rate.
    auction_slots:
        Number of Solana slots the JIT auction runs.  Kept for future
        latency modelling; not used in the fill-price calculation today.
    auction_price_improvement_bps:
        Basis-point improvement over oracle price awarded by JIT MMs.
        Default 2.0 bps (midpoint of the observed 1-3 bps range).
    seed:
        Optional RNG seed for reproducible simulations.
    """

    def __init__(
        self,
        jit_fill_probability: float = 0.6,
        auction_slots: int = 20,
        auction_price_improvement_bps: float = 2.0,
        seed: Optional[int] = None,
    ) -> None:
        self._jit_prob = jit_fill_probability
        self._auction_slots = auction_slots
        self._auction_improvement_bps = auction_price_improvement_bps
        self._rng = random.Random(seed)
        self._current_book: Optional[OrderbookSnapshot] = None
        self._profile = VENUE_PROFILES["drift"]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_orderbook(self, book: Optional[OrderbookSnapshot]) -> None:
        """Provide a real DLOB snapshot for Tier 2.  Called by the engine
        each bar before `fill_market` is invoked."""
        self._current_book = book

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        """Fill a market order through the 3-tier Drift pipeline."""
        remaining = order.size
        total_cost = 0.0

        # ----------------------------------------------------------------
        # Tier 1: JIT Dutch Auction
        # ----------------------------------------------------------------
        if self._rng.random() < self._jit_prob:
            # JIT fills a random fraction (30-80%) of the order size.
            jit_fraction = self._rng.uniform(0.3, 0.8)
            jit_size = remaining * jit_fraction
            jit_price = self._jit_price(candle, order.side)
            total_cost += jit_size * jit_price
            remaining -= jit_size

        # ----------------------------------------------------------------
        # Tier 2: DLOB Walk
        # ----------------------------------------------------------------
        if remaining > 0:
            book = self._current_book
            if book is None or book.market != order.market:
                # Fall back to synthetic depth when no real data is available.
                book = generate_synthetic_book(
                    mid_price=candle.close,
                    profile=self._profile,
                    market=order.market,
                    ts=candle.ts,
                )

            # Buys walk the ask side; sells walk the bid side.
            levels = book.asks if order.side == Side.LONG else book.bids
            for level in levels:
                if remaining <= 0:
                    break
                take = min(remaining, level.size)
                total_cost += take * level.price
                remaining -= take

        # ----------------------------------------------------------------
        # Tier 3: vAMM Backstop
        # ----------------------------------------------------------------
        if remaining > 0:
            sqrt_k = DEFAULT_SQRT_K.get(candle.market, _FALLBACK_SQRT_K)
            curve = VammCurve.from_oracle_price(sqrt_k, candle.close)
            direction = "long" if order.side == Side.LONG else "short"
            vamm_price = curve.fill_price(remaining, direction)
            total_cost += remaining * vamm_price
            remaining = 0.0

        filled = order.size - remaining
        if filled <= 0:
            return None

        avg_price = total_cost / filled
        return Fill(
            market=order.market,
            side=order.side,
            price=avg_price,
            size=filled,
            fee=0.0,
            ts=candle.ts,
            order_id=order.order_id,
            venue="drift",
        )

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        """Limit orders: fill when price crosses the limit (passive maker)."""
        if order.side == Side.LONG and candle.low <= order.price:
            fill_price = order.price
        elif order.side == Side.SHORT and candle.high >= order.price:
            fill_price = order.price
        else:
            return None
        return Fill(
            market=order.market,
            side=order.side,
            price=fill_price,
            size=order.size,
            fee=0.0,
            ts=candle.ts,
            order_id=order.order_id,
            venue="drift",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _jit_price(self, candle: Candle, side: Side) -> float:
        """Compute the JIT auction fill price.

        JIT MMs offer a price improvement over oracle (candle.close):
          - Buys  fill *below* the oracle (cheaper for the taker).
          - Sells fill *above* the oracle (better for the taker).
        """
        improvement = (self._auction_improvement_bps / 10_000) * candle.close
        if side == Side.LONG:
            return candle.close - improvement
        return candle.close + improvement
