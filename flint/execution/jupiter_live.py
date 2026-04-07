"""Live execution context for Jupiter Perps.

Communicates with the Jupiter Perps Node.js sidecar to open/close
positions, read prices, and pull balances. Borrow-rate history is
read from the local FlintStore.
"""
import logging
import time
from typing import List, Optional

import httpx

from ..models import PositionInfo, Side

logger = logging.getLogger(__name__)


class LiveJupiterContext:
    def __init__(self, sidecar_url="http://127.0.0.1:8401", store=None):
        self._sidecar_url = sidecar_url.rstrip("/")
        self._http = httpx.Client(timeout=30)
        self._store = store

    def close(self):
        self._http.close()

    def _validate_collateral(self, market: str, side: str) -> bool:
        return True

    def get_mark_price(self, market: str) -> float:
        resp = self._http.get(f"{self._sidecar_url}/oracle/{market}", timeout=10)
        resp.raise_for_status()
        return resp.json()["price"]

    def get_positions(self) -> List[PositionInfo]:
        resp = self._http.get(f"{self._sidecar_url}/positions", timeout=10)
        resp.raise_for_status()
        return [
            PositionInfo(
                market=p["market"],
                side=Side.LONG if p["side"] == "long" else Side.SHORT,
                size=p["size"],
                entry_price=p["entry_price"],
                unrealized_pnl=p.get("unrealized_pnl", 0.0),
                venue="jupiter",
            )
            for p in resp.json().get("positions", [])
        ]

    def get_balances(self) -> dict:
        resp = self._http.get(f"{self._sidecar_url}/balances", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def market_order(self, market: str, side: str, size: float, **kwargs) -> str:
        endpoint = "/increase" if side == "buy" else "/decrease"
        resp = self._http.post(
            f"{self._sidecar_url}{endpoint}",
            json={"market": market, "side": side, "size": size, **kwargs},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["request_id"]

    def get_borrow_rate(self, market=None, venue=None):
        if not self._store or not market:
            return None
        now = int(time.time())
        rates = self._store.query_borrow_rates(market, now - 3600, now)
        return rates[-1].rate_hourly if rates else None

    def get_borrow_rates(self, market=None, venue=None, lookback=24):
        if not self._store or not market:
            return []
        now = int(time.time())
        rates = self._store.query_borrow_rates(market, now - lookback * 3600, now)
        return [(r.ts, r.rate_hourly) for r in rates]
