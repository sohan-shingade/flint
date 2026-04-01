"""FundingArbStrategy — delta-neutral cross-venue funding rate arbitrage.

Exploits funding rate divergence between venues. Long on the venue paying you
(low/negative funding), short on the venue charging you (high/positive funding).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..models import Candle, Signal, Side
from .base import Strategy

logger = logging.getLogger("flint.strategy.funding_arb")


class FundingArbStrategy(Strategy):
    def __init__(
        self,
        min_spread_bps: float = 5.0,
        exit_spread_bps: float = 1.0,
        max_hold_hours: int = 24,
        position_size_usd: float = 1000.0,
        min_spread_duration: int = 1,
        venues: Optional[List[str]] = None,
        candle_resolution_s: int = 60,
    ):
        self._min_spread_bps = min_spread_bps
        self._exit_spread_bps = exit_spread_bps
        self._max_hold_hours = max_hold_hours
        self._position_size_usd = position_size_usd
        self._min_spread_duration = min_spread_duration
        self._venues = venues or ["drift", "hyperliquid"]
        self._candle_resolution_s = candle_resolution_s
        self._entry_ts: int = 0
        self._long_venue: str = ""
        self._short_venue: str = ""
        self._spread_above_since: int = 0

    @property
    def name(self) -> str:
        return "funding_arb"

    def reset(self) -> None:
        self._entry_ts = 0
        self._long_venue = ""
        self._short_venue = ""
        self._spread_above_since = 0

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None:
            return Signal.HOLD
        market = candle.market
        venue_data = ctx.get_funding_by_venue(market, lookback=24)
        if len(venue_data) < 2:
            return Signal.HOLD

        venue_rates = {}
        for venue in self._venues:
            rates = venue_data.get(venue, [])
            if rates:
                venue_rates[venue] = rates[-1][1]  # (ts, rate) -> rate

        if len(venue_rates) < 2:
            return Signal.HOLD

        has_position = self._entry_ts > 0
        if has_position:
            return self._check_exit(candle, ctx, venue_rates)
        else:
            return self._check_entry(candle, ctx, venue_rates)

    def _check_entry(self, candle, ctx, venue_rates):
        venues = list(venue_rates.keys())
        best_spread = 0.0
        best_long = ""
        best_short = ""

        for i in range(len(venues)):
            for j in range(i + 1, len(venues)):
                spread = abs(venue_rates[venues[i]] - venue_rates[venues[j]])
                if spread > best_spread:
                    best_spread = spread
                    if venue_rates[venues[i]] < venue_rates[venues[j]]:
                        best_long = venues[i]
                        best_short = venues[j]
                    else:
                        best_long = venues[j]
                        best_short = venues[i]

        spread_bps = best_spread * 10000
        if spread_bps < self._min_spread_bps:
            self._spread_above_since = 0
            return Signal.HOLD

        if self._spread_above_since == 0:
            self._spread_above_since = candle.ts
        hours_above = (candle.ts - self._spread_above_since) / 3600
        if hours_above < self._min_spread_duration:
            return Signal.HOLD

        size = self._position_size_usd / candle.close if candle.close > 0 else 0
        if size <= 0:
            return Signal.HOLD

        ctx.market_order(candle.market, Side.LONG, size, venue=best_long)
        ctx.market_order(candle.market, Side.SHORT, size, venue=best_short)

        self._entry_ts = candle.ts
        self._long_venue = best_long
        self._short_venue = best_short
        self._spread_above_since = 0
        return Signal.HOLD

    def _check_exit(self, candle, ctx, venue_rates):
        hold_hours = (candle.ts - self._entry_ts) / 3600
        if hold_hours >= self._max_hold_hours:
            self._close_both(candle, ctx, "max hold")
            return Signal.HOLD

        long_rate = venue_rates.get(self._long_venue, 0)
        short_rate = venue_rates.get(self._short_venue, 0)
        spread_bps = abs(short_rate - long_rate) * 10000

        if spread_bps < self._exit_spread_bps:
            self._close_both(candle, ctx, "spread converged")
            return Signal.HOLD

        return Signal.HOLD

    def _close_both(self, candle, ctx, reason):
        ctx.close_position(candle.market, venue=self._long_venue)
        ctx.close_position(candle.market, venue=self._short_venue)
        self._entry_ts = 0
        self._long_venue = ""
        self._short_venue = ""

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "min_spread_bps": {"type": "float", "low": 3.0, "high": 20.0, "default": 5.0},
            "exit_spread_bps": {"type": "float", "low": 0.5, "high": 5.0, "default": 1.0},
            "max_hold_hours": {"type": "int", "low": 4, "high": 72, "default": 24},
            "position_size_usd": {"type": "float", "low": 100, "high": 10000, "default": 1000},
            "min_spread_duration": {"type": "int", "low": 0, "high": 6, "default": 1},
            "candle_resolution_s": {"type": "int", "low": 60, "high": 3600, "default": 60},
        }
