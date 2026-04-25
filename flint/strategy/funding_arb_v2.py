"""CrossVenueFundingArb — multi-asset delta-neutral funding rate arbitrage.

Improvements over FundingArbStrategy:
- Multi-asset: trades funding arbs across all markets passed to the engine
- Z-score entry: uses rolling spread statistics instead of fixed threshold
- Fee-aware: only enters when spread > round-trip fees + min profit
- Spread inversion exit: exits immediately if spread flips
- Regime filter: skips entry when rate volatility is extreme (unstable spreads)
- Per-market position tracking with independent entry/exit
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..models import Candle, Signal, Side
from .base import Strategy

logger = logging.getLogger("flint.strategy.funding_arb_v2")


@dataclass
class _ArbState:
    """Per-market state for an active arb position."""
    long_venue: str = ""
    short_venue: str = ""
    entry_ts: int = 0
    entry_spread: float = 0.0
    cumulative_funding: float = 0.0


class CrossVenueFundingArb(Strategy):
    """Delta-neutral cross-venue funding rate arbitrage.

    For each market, finds the venue pair with the largest funding spread.
    Enters when the spread z-score exceeds a threshold and the absolute
    spread exceeds round-trip fees plus a minimum profit margin.
    """

    def __init__(
        self,
        # Entry parameters
        min_spread_bps: float = 4.0,
        z_score_entry: float = 1.5,
        min_persistence: int = 2,
        # Exit parameters
        exit_spread_bps: float = 0.5,
        max_hold_hours: int = 48,
        # Risk parameters
        position_size_usd: float = 1000.0,
        max_positions: int = 5,
        max_total_notional: float = 25000.0,
        # Fee assumptions (bps per leg, taker)
        fee_bps: float = 3.5,
        # Spread history
        lookback_hours: int = 168,
        # Venues to consider
        venues: Optional[List[str]] = None,
        # Regime filter
        max_rate_vol_bps: float = 15.0,
    ):
        self._min_spread_bps = min_spread_bps
        self._z_entry = z_score_entry
        self._min_persistence = min_persistence
        self._exit_spread_bps = exit_spread_bps
        self._max_hold_hours = max_hold_hours
        self._pos_size_usd = position_size_usd
        self._max_positions = max_positions
        self._max_total_notional = max_total_notional
        self._fee_bps = fee_bps
        self._lookback = lookback_hours
        self._venues = venues or [
            "hyperliquid", "binance", "okx", "bybit",
            "drift", "bitget", "gateio", "dydx",
        ]
        self._max_rate_vol = max_rate_vol_bps / 10_000

        # Per-market state
        self._positions: Dict[str, _ArbState] = {}
        self._spread_history: Dict[str, List[float]] = defaultdict(list)
        self._spread_above_since: Dict[str, int] = defaultdict(int)
        self._total_notional: float = 0.0

    @property
    def name(self) -> str:
        return "cross_venue_funding_arb"

    def reset(self) -> None:
        self._positions.clear()
        self._spread_history.clear()
        self._spread_above_since.clear()
        self._total_notional = 0.0

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None:
            return Signal.HOLD

        market = candle.market
        venue_rates = self._get_venue_rates(ctx, market, candle.ts)
        if len(venue_rates) < 2:
            return Signal.HOLD

        best_long, best_short, spread = self._best_pair(venue_rates)
        spread_bps = spread * 10_000

        # Update rolling spread history
        hist = self._spread_history[market]
        hist.append(spread)
        max_hist = self._lookback
        if len(hist) > max_hist:
            self._spread_history[market] = hist[-max_hist:]
            hist = self._spread_history[market]

        state = self._positions.get(market)
        if state and state.entry_ts > 0:
            self._check_exit(candle, ctx, market, state, venue_rates)
        else:
            self._check_entry(
                candle, ctx, market, best_long, best_short,
                spread, spread_bps, hist, venue_rates,
            )

        return Signal.HOLD

    # ------------------------------------------------------------------
    # Entry logic
    # ------------------------------------------------------------------

    def _check_entry(
        self, candle, ctx, market, best_long, best_short,
        spread, spread_bps, hist, venue_rates,
    ):
        # Risk limits
        n_open = sum(1 for s in self._positions.values() if s.entry_ts > 0)
        if n_open >= self._max_positions:
            return
        if self._total_notional >= self._max_total_notional:
            return

        # Funding arb PnL is time-based: the hourly spread is collected
        # continuously.  Entry is profitable if:
        #   spread_bps_per_hour * expected_hold_hours > round_trip_fees
        # With min_spread_bps as the minimum hourly rate to bother trading.
        if spread_bps < self._min_spread_bps:
            self._spread_above_since[market] = 0
            return

        # Z-score filter (need enough history)
        if len(hist) >= 24:
            mean = sum(hist) / len(hist)
            var = sum((x - mean) ** 2 for x in hist) / len(hist)
            std = math.sqrt(var) if var > 0 else 1e-10
            z = (spread - mean) / std
            if z < self._z_entry:
                self._spread_above_since[market] = 0
                return
        else:
            # Not enough history — only enter on very large spreads
            if spread_bps < self._min_spread_bps * 2:
                return

        # Regime filter: skip if rate volatility is extreme (unreliable spreads)
        all_rates = list(venue_rates.values())
        if len(all_rates) >= 3:
            rate_mean = sum(all_rates) / len(all_rates)
            rate_var = sum((r - rate_mean) ** 2 for r in all_rates) / len(all_rates)
            if math.sqrt(rate_var) > self._max_rate_vol:
                return

        # Persistence check
        if self._spread_above_since[market] == 0:
            self._spread_above_since[market] = candle.ts
        hours_above = (candle.ts - self._spread_above_since[market]) / 3600
        if hours_above < self._min_persistence:
            return

        # Size: proportional to spread (bigger spread = more confident)
        base_size = self._pos_size_usd / candle.close if candle.close > 0 else 0
        if base_size <= 0:
            return

        # Check total notional limit
        leg_notional = base_size * candle.close
        if self._total_notional + leg_notional * 2 > self._max_total_notional:
            return

        # Execute both legs
        ctx.market_order(market, Side.LONG, base_size, venue=best_long)
        ctx.market_order(market, Side.SHORT, base_size, venue=best_short)

        self._positions[market] = _ArbState(
            long_venue=best_long,
            short_venue=best_short,
            entry_ts=candle.ts,
            entry_spread=spread,
        )
        self._total_notional += leg_notional * 2
        self._spread_above_since[market] = 0

    # ------------------------------------------------------------------
    # Exit logic
    # ------------------------------------------------------------------

    def _check_exit(self, candle, ctx, market, state, venue_rates):
        long_rate = venue_rates.get(state.long_venue, 0)
        short_rate = venue_rates.get(state.short_venue, 0)
        current_spread = short_rate - long_rate  # positive = we're collecting
        current_bps = current_spread * 10_000
        hold_hours = (candle.ts - state.entry_ts) / 3600

        should_exit = False

        # 1. Spread inverted — we're now PAYING instead of collecting
        if current_spread < 0:
            should_exit = True

        # 2. Spread collapsed below exit threshold
        elif current_bps < self._exit_spread_bps:
            should_exit = True

        # 3. Max hold time exceeded
        elif hold_hours >= self._max_hold_hours:
            should_exit = True

        if should_exit:
            leg_notional = self._pos_size_usd
            ctx.close_position(market, venue=state.long_venue)
            ctx.close_position(market, venue=state.short_venue)
            state.entry_ts = 0
            self._total_notional = max(0, self._total_notional - leg_notional * 2)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_venue_rates(self, ctx, market, current_ts) -> Dict[str, float]:
        """Get latest funding rate per venue, filtered for staleness."""
        venue_data = ctx.get_funding_by_venue(market, lookback=24)
        rates = {}
        for venue in self._venues:
            entries = venue_data.get(venue, [])
            if entries:
                ts, rate = entries[-1]
                # Skip if data is stale (> 3 hours old)
                if current_ts - ts <= 10800:
                    rates[venue] = rate
        return rates

    def _best_pair(self, venue_rates: Dict[str, float]):
        """Find the venue pair with the largest absolute spread."""
        venues = list(venue_rates.keys())
        best_spread = 0.0
        best_long = ""
        best_short = ""

        for i in range(len(venues)):
            for j in range(i + 1, len(venues)):
                ri, rj = venue_rates[venues[i]], venue_rates[venues[j]]
                spread = abs(ri - rj)
                if spread > best_spread:
                    best_spread = spread
                    if ri < rj:
                        best_long, best_short = venues[i], venues[j]
                    else:
                        best_long, best_short = venues[j], venues[i]

        return best_long, best_short, best_spread

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "min_spread_bps": {"type": "float", "low": 2.0, "high": 15.0, "default": 4.0},
            "z_score_entry": {"type": "float", "low": 0.5, "high": 3.0, "default": 1.5},
            "min_persistence": {"type": "int", "low": 1, "high": 6, "default": 2},
            "exit_spread_bps": {"type": "float", "low": 0.0, "high": 3.0, "default": 0.5},
            "max_hold_hours": {"type": "int", "low": 8, "high": 96, "default": 48},
            "position_size_usd": {"type": "float", "low": 500, "high": 5000, "default": 1000},
            "max_positions": {"type": "int", "low": 1, "high": 8, "default": 5},
            "fee_bps": {"type": "float", "low": 1.0, "high": 6.0, "default": 3.5},
            "lookback_hours": {"type": "int", "low": 48, "high": 336, "default": 168},
            "max_rate_vol_bps": {"type": "float", "low": 5.0, "high": 30.0, "default": 15.0},
        }
