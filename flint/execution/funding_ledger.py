"""FundingLedger — recorded funding rates per (market, venue).

D-2.1.b Step 5 (Wave 2). Pulls the two funding-rate dictionaries
off BacktestContext into a single owner. The strategy-facing helpers
(`latest`, `recent`, `by_venue`, `venue_snapshots`) match the
pre-extraction `get_funding_rate*` API surface so the BacktestContext
methods become trivial delegations.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..models import FundingRate


class FundingLedger:
    """Append-only per-market and per-venue funding history."""

    __slots__ = ("_history", "_venue_history")

    def __init__(self) -> None:
        # market → flat list (legacy "default venue" view used by
        # `get_funding_rate(s)` when the caller doesn't care about venue).
        self._history: Dict[str, List[FundingRate]] = {}
        # market → venue → list (split view used by multi-venue
        # strategies like funding_arb / funding_dislocation_arb).
        self._venue_history: Dict[str, Dict[str, List[FundingRate]]] = {}

    # --- record ------------------------------------------------------------

    def add(self, fr: FundingRate) -> None:
        mkt = fr.market
        self._history.setdefault(mkt, []).append(fr)
        venue = getattr(fr, "source", "drift")
        self._venue_history.setdefault(mkt, {}).setdefault(venue, []).append(fr)

    # --- read --------------------------------------------------------------

    def latest(self, market: str) -> Optional[float]:
        history = self._history.get(market)
        return history[-1].rate if history else None

    def recent(self, market: str, lookback: int = 24) -> List[Tuple[int, float]]:
        history = self._history.get(market, [])
        return [(fr.ts, fr.rate) for fr in history[-lookback:]]

    def by_venue(self, market: str, lookback: int = 24) -> Dict[str, List[Tuple[int, float]]]:
        venues = self._venue_history.get(market, {})
        return {
            venue: [(fr.ts, fr.rate) for fr in rates[-lookback:]]
            for venue, rates in venues.items()
        }

    def venue_snapshots(self, market: str, lookback: int = 24) -> Dict[str, List[FundingRate]]:
        venues = self._venue_history.get(market, {})
        return {venue: list(rates[-lookback:]) for venue, rates in venues.items()}

    # --- raw access for legacy aliases on BacktestContext ----------------

    @property
    def history(self) -> Dict[str, List[FundingRate]]:
        return self._history

    @property
    def venue_history(self) -> Dict[str, Dict[str, List[FundingRate]]]:
        return self._venue_history
