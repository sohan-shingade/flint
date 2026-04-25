"""Funding Dislocation Arb — Phase 6 T6.6 reference implementation.

Evolution of FundingArbStrategy with:
- Tighter entry filter: BOTH magnitude spread AND mean-reversion signal
  (spread at least K std devs from its rolling mean).
- Exits on spread mean-reversion rather than fixed-timer, so winning trades
  run and losers snap back.
- Multi-venue aware (Drift + HL by default; extensible via `venues`).
- Position sizing scales with spread magnitude (Kelly-lite — never exceeds
  max_position_usd cap).

This is THE reference strategy Phase 6 acceptance tests run through backtest
→ paper → mainnet (tiny size) under the D-3.5-orchestrator + D-6.5-api
work. It exists now so Phase 6 downstream work (proof notebooks, live
deployment checklist, parity reports) has a concrete target.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, List, Optional

from ..models import Candle, Side, Signal
from .base import Strategy


class FundingDislocationArbStrategy(Strategy):
    """Cross-venue funding dislocation capture.

    Parameters
    ----------
    min_spread_bps:
        Enter only when `|funding_hi - funding_lo|` (in bps, hourly) is at
        least this wide.
    entry_z_threshold:
        Spread must ALSO be at least this many std devs above its trailing
        window mean — filters out ambient noise.
    exit_spread_bps:
        Close positions when spread tightens below this.
    zscore_window:
        Bars of history used to compute the z-score.
    max_position_usd:
        Hard cap per leg; strategy never exceeds this regardless of Kelly
        output.
    """

    def __init__(
        self,
        min_spread_bps: float = 10.0,
        entry_z_threshold: float = 1.5,
        exit_spread_bps: float = 2.0,
        zscore_window: int = 48,
        max_position_usd: float = 2_000.0,
        venues: Optional[List[str]] = None,
    ):
        self._min_spread_bps = float(min_spread_bps)
        self._entry_z = float(entry_z_threshold)
        self._exit_spread_bps = float(exit_spread_bps)
        self._zscore_window = int(zscore_window)
        self._max_position_usd = float(max_position_usd)
        self._venues = venues or ["drift", "hyperliquid"]

        self._spread_history: Deque[float] = deque(maxlen=zscore_window)
        self._long_venue: str = ""
        self._short_venue: str = ""
        self._entry_spread_bps: float = 0.0
        self._entry_ts: int = 0

    # ---------------------------------------------------------------- meta

    @property
    def name(self) -> str:
        return (
            f"funding_dislocation_arb(min={self._min_spread_bps:.0f}bps,"
            f"z={self._entry_z:.1f})"
        )

    @classmethod
    def parameters(cls) -> dict:
        return {
            "min_spread_bps": {"type": "float", "low": 2.0, "high": 30.0, "default": 10.0},
            "entry_z_threshold": {"type": "float", "low": 0.5, "high": 3.0, "default": 1.5},
            "exit_spread_bps": {"type": "float", "low": 0.5, "high": 5.0, "default": 2.0},
            "zscore_window": {"type": "int", "low": 12, "high": 168, "default": 48},
            "max_position_usd": {"type": "float", "low": 500.0, "high": 10_000.0, "default": 2_000.0},
        }

    def reset(self) -> None:
        self._spread_history.clear()
        self._long_venue = ""
        self._short_venue = ""
        self._entry_spread_bps = 0.0
        self._entry_ts = 0

    # --------------------------------------------------------------- logic

    def _latest_rate_bps(self, ctx, market: str, venue: str) -> Optional[float]:
        """Most recent funding rate for (market, venue) in bps. None if
        venue has no data in the lookback window."""
        by_venue = ctx.get_funding_by_venue(market, lookback=12)
        rates = by_venue.get(venue, [])
        if not rates:
            return None
        latest = rates[-1]
        # rate is a decimal (0.0001 = 1bp); convert to bps.
        return float(getattr(latest, "rate", 0.0)) * 10_000.0

    def _position_size(self, ctx, candle: Candle, spread_bps: float) -> float:
        """Spread-scaled sizing; capped by max_position_usd and cash."""
        notional = min(
            self._max_position_usd,
            ctx.account.cash * 0.5,
            abs(spread_bps) / 20.0 * self._max_position_usd,  # Kelly-lite
        )
        return max(0.0, notional / max(candle.close, 1e-9))

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None:
            return Signal.HOLD
        market = candle.market

        # Collect current per-venue funding bps.
        venue_rates = {}
        for v in self._venues:
            r = self._latest_rate_bps(ctx, market, v)
            if r is not None:
                venue_rates[v] = r
        if len(venue_rates) < 2:
            return Signal.HOLD

        hi_venue = max(venue_rates, key=venue_rates.get)
        lo_venue = min(venue_rates, key=venue_rates.get)
        spread = venue_rates[hi_venue] - venue_rates[lo_venue]
        self._spread_history.append(spread)

        has_position = bool(ctx.positions)

        # ---- Exit path ---------------------------------------------------
        if has_position:
            if spread <= self._exit_spread_bps:
                ctx.cancel_all()
                ctx.close_position(market, venue=self._long_venue)
                ctx.close_position(market, venue=self._short_venue)
                self._long_venue = ""
                self._short_venue = ""
                self._entry_spread_bps = 0.0
                self._entry_ts = 0
            return Signal.HOLD

        # ---- Entry path --------------------------------------------------
        if abs(spread) < self._min_spread_bps:
            return Signal.HOLD
        if len(self._spread_history) < self._zscore_window // 2:
            return Signal.HOLD  # warm-up

        import statistics as _stats
        mu = _stats.fmean(self._spread_history)
        sd = _stats.pstdev(self._spread_history) or 1e-9
        z = (spread - mu) / sd
        if abs(z) < self._entry_z:
            return Signal.HOLD

        size = self._position_size(ctx, candle, spread)
        if size <= 0:
            return Signal.HOLD

        # Long the venue paying you (low funding), short the venue charging (high).
        ctx.market_order(market, Side.LONG, size, venue=lo_venue)
        ctx.market_order(market, Side.SHORT, size, venue=hi_venue)
        self._long_venue = lo_venue
        self._short_venue = hi_venue
        self._entry_spread_bps = spread
        self._entry_ts = candle.ts
        return Signal.HOLD
