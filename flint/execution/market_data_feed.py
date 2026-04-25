"""MarketDataFeed — orderbook + OI + cross-market candle history.

D-2.1.b Step 7 (final slice). Pulls the three market-data caches
(orderbook snapshots, open-interest snapshots, multi-market candle
histories) into a single owner. After this step, BacktestContext is
strictly an orchestrator — every piece of mutable state lives in one
of the seven extracted components.

Design choice: orderbook + OI + market_histories travel together
because all three answer "what was the market doing at this bar's
ts?" — the strategy reaches for whichever it has data for. Splitting
into three more managers would be ceremony without payoff.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..models import Candle


class MarketDataFeed:
    __slots__ = ("_market_histories", "_orderbook_history", "_oi_history")

    def __init__(self) -> None:
        self._market_histories: Dict[str, List[Candle]] = {}
        self._orderbook_history: Dict[str, List[Any]] = {}
        self._oi_history: Dict[str, List[Any]] = {}

    # --- multi-market candles ---------------------------------------------

    def set_histories(self, histories: Dict[str, List[Candle]]) -> None:
        self._market_histories = histories

    def candles(self, market: str, lookback: int = 50) -> List[Candle]:
        history = self._market_histories.get(market, [])
        return history[-lookback:] if history else []

    def history_for(self, market: str) -> List[Candle]:
        """Full history for a market — used by `set_candle` to look up
        the most recent close on a non-primary market."""
        return self._market_histories.get(market, [])

    def markets(self) -> List[str]:
        return list(self._market_histories.keys())

    @property
    def market_histories(self) -> Dict[str, List[Candle]]:
        return self._market_histories

    @market_histories.setter
    def market_histories(self, value: Dict[str, List[Candle]]) -> None:
        self._market_histories = value

    # --- orderbook snapshots ----------------------------------------------

    def add_orderbook(self, snapshot) -> None:
        self._orderbook_history.setdefault(snapshot.market, []).append(snapshot)

    def latest_orderbook(self, market: str):
        history = self._orderbook_history.get(market)
        return history[-1] if history else None

    @property
    def orderbook_history(self) -> Dict[str, List[Any]]:
        return self._orderbook_history

    # --- open interest -----------------------------------------------------

    def add_open_interest(self, oi) -> None:
        self._oi_history.setdefault(oi.market, []).append(oi)

    def latest_oi(self, market: str) -> Optional[Tuple[float, float]]:
        history = self._oi_history.get(market)
        if not history:
            return None
        oi = history[-1]
        return (oi.long_oi, oi.short_oi)

    def oi_recent(self, market: str, lookback: int = 24) -> List[Tuple[int, float, float]]:
        history = self._oi_history.get(market, [])
        return [(oi.ts, oi.long_oi, oi.short_oi) for oi in history[-lookback:]]

    @property
    def oi_history(self) -> Dict[str, List[Any]]:
        return self._oi_history
