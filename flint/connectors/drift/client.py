"""Drift protocol connector.

Uses the public DLOB and Data APIs for read operations.
Write operations (place_order, cancel_order) require a wallet keypair
and the ``solders``/``solana-py`` stack — they raise NotImplementedError
in the current phase (backtesting only).
"""
from __future__ import annotations

from typing import Dict, List, Optional

import httpx

from ...models import (
    Fill,
    FundingRate,
    MarketInfo,
    Order,
    OrderbookLevel,
    OrderbookSnapshot,
    Side,
)
from ...providers.drift_api import DriftDataProvider
from ..base import BaseConnector
from .types import DriftPerpMarket, DriftUserPosition

# Drift constants
_PRICE_PRECISION = 1e6
_BASE_PRECISION = 1e9

# Known perp markets (can be extended from on-chain discovery)
_KNOWN_PERP_MARKETS = [
    DriftPerpMarket(0, "SOL-PERP", "H6ARHf6YXhGYeQfUzQNGk6rDNnLBQKrenN712K4AQJEG",
                    9, 6, 0.001, 0.1, -0.0002, 0.001, 0, 0),
    DriftPerpMarket(1, "BTC-PERP", "GVXRSBjFk6e6J3NbVPXohDJwXHonaP4DHDwUkP99CJXG",
                    9, 6, 0.01, 0.001, -0.0002, 0.001, 0, 0),
    DriftPerpMarket(2, "ETH-PERP", "JBu1AL4obBcCMqKBBxhpWCNUt136ijcuMZLFvTP7iWdB",
                    9, 6, 0.01, 0.01, -0.0002, 0.001, 0, 0),
]

_MARKET_INDEX_BY_SYMBOL = {m.symbol: m.market_index for m in _KNOWN_PERP_MARKETS}
_MARKET_BY_SYMBOL = {m.symbol: m for m in _KNOWN_PERP_MARKETS}


class DriftConnector(BaseConnector):
    """Drift connector backed by public REST APIs.

    Read operations (orderbook, price, funding) work out of the box.
    Write operations require additional Solana tooling.
    """

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._data = DriftDataProvider(client=client)
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    @property
    def protocol(self) -> str:
        return "drift"

    # -- read operations (public APIs) -----------------------------------------

    async def get_markets(self) -> List[MarketInfo]:
        return [
            MarketInfo(
                symbol=m.symbol,
                market_index=m.market_index,
                oracle=m.oracle,
                tick_size=m.tick_size,
                min_order_size=m.min_order_size,
                maker_fee=m.maker_fee,
                taker_fee=m.taker_fee,
            )
            for m in _KNOWN_PERP_MARKETS
        ]

    async def get_orderbook(self, market: str) -> OrderbookSnapshot:
        return self._data.fetch_orderbook(market_name=market)

    async def get_price(self, market: str) -> float:
        return self._data.fetch_mid_price(market_name=market)

    async def get_funding_rates(
        self,
        market: str = "SOL-PERP",
        limit: int = 100,
    ) -> List[FundingRate]:
        idx = _MARKET_INDEX_BY_SYMBOL.get(market, 0)
        return self._data.fetch_funding_rates(
            market_index=idx, market_name=market, limit=limit,
        )

    # -- write operations (require wallet) ------------------------------------

    async def place_order(self, order: Order) -> Order:
        raise NotImplementedError(
            "Live order placement requires solders/solana-py. "
            "Use the backtest engine for simulated execution."
        )

    async def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("Live order cancellation requires solders/solana-py.")

    async def get_positions(self) -> List[Dict]:
        raise NotImplementedError("Reading positions requires an RPC connection + wallet pubkey.")

    async def get_balances(self) -> Dict[str, float]:
        raise NotImplementedError("Reading balances requires an RPC connection + wallet pubkey.")

    # -- convenience -----------------------------------------------------------

    def get_market_info(self, symbol: str) -> Optional[DriftPerpMarket]:
        return _MARKET_BY_SYMBOL.get(symbol)

    async def close(self) -> None:
        self._data.close()
