"""Abstract base class for protocol connectors."""
from __future__ import annotations

import abc
from typing import AsyncIterator, Dict, List

from ..models import Fill, MarketInfo, Order, OrderbookSnapshot, Side


class BaseConnector(abc.ABC):
    """Unified interface for interacting with Solana protocols.

    All methods are async to support live streaming and concurrent operations.
    """

    @property
    @abc.abstractmethod
    def protocol(self) -> str:
        """Protocol name, e.g. 'drift', 'jupiter'."""
        ...

    @abc.abstractmethod
    async def get_markets(self) -> List[MarketInfo]:
        ...

    @abc.abstractmethod
    async def get_orderbook(self, market: str) -> OrderbookSnapshot:
        ...

    @abc.abstractmethod
    async def get_price(self, market: str) -> float:
        ...

    @abc.abstractmethod
    async def place_order(self, order: Order) -> Order:
        ...

    @abc.abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        ...

    @abc.abstractmethod
    async def get_positions(self) -> List[Dict]:
        ...

    @abc.abstractmethod
    async def get_balances(self) -> Dict[str, float]:
        ...

    async def close(self) -> None:
        """Clean up resources."""
        pass
