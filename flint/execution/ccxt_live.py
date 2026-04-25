"""LiveCCXTContext — ExecutionContext for live trading on CEX venues via CCXT.

Extends LiveExecutionContext with the same interface as LiveDriftContext and
LiveHyperliquidContext. Strategies deploy to any CCXT-supported exchange with
zero code changes.

Supported exchanges: Binance, OKX, Bybit (and any CCXT-compatible exchange).

Environment variables:
    FLINT_CCXT_EXCHANGE: Exchange name (e.g. "binance", "okx", "bybit")
    FLINT_CCXT_API_KEY: API key
    FLINT_CCXT_SECRET: API secret
    FLINT_CCXT_PASSWORD: Passphrase (OKX requires this)
    FLINT_CCXT_SANDBOX: "true" for testnet (default: "true")
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Tuple

from ..models import (
    Order, OrderState, OrderType, PositionInfo, Side,
)
from .live_base import LiveExecutionContext

logger = logging.getLogger("flint.ccxt_live")

# Flint symbol -> CCXT unified symbol mapping
_FLINT_TO_CCXT: Dict[str, str] = {
    "SOL-PERP": "SOL/USDT:USDT", "BTC-PERP": "BTC/USDT:USDT",
    "ETH-PERP": "ETH/USDT:USDT", "DOGE-PERP": "DOGE/USDT:USDT",
    "AVAX-PERP": "AVAX/USDT:USDT", "LINK-PERP": "LINK/USDT:USDT",
    "ARB-PERP": "ARB/USDT:USDT", "SUI-PERP": "SUI/USDT:USDT",
    "XRP-PERP": "XRP/USDT:USDT", "OP-PERP": "OP/USDT:USDT",
    "INJ-PERP": "INJ/USDT:USDT", "TIA-PERP": "TIA/USDT:USDT",
    "SEI-PERP": "SEI/USDT:USDT", "WIF-PERP": "WIF/USDT:USDT",
    "JUP-PERP": "JUP/USDT:USDT", "BNB-PERP": "BNB/USDT:USDT",
    "MATIC-PERP": "MATIC/USDT:USDT", "ADA-PERP": "ADA/USDT:USDT",
}

_CCXT_TO_FLINT: Dict[str, str] = {v: k for k, v in _FLINT_TO_CCXT.items()}


def _to_ccxt_symbol(flint_market: str) -> str:
    """Convert Flint market symbol to CCXT unified symbol."""
    if flint_market in _FLINT_TO_CCXT:
        return _FLINT_TO_CCXT[flint_market]
    # Fallback: assume PERP format
    if flint_market.endswith("-PERP"):
        base = flint_market.replace("-PERP", "")
        return f"{base}/USDT:USDT"
    # Spot
    return f"{flint_market}/USDT"


def _to_flint_symbol(ccxt_symbol: str) -> str:
    """Convert CCXT unified symbol back to Flint format."""
    if ccxt_symbol in _CCXT_TO_FLINT:
        return _CCXT_TO_FLINT[ccxt_symbol]
    # Fallback parse
    if ":USDT" in ccxt_symbol:
        base = ccxt_symbol.split("/")[0]
        return f"{base}-PERP"
    return ccxt_symbol.split("/")[0]


class LiveCCXTContext(LiveExecutionContext):
    """ExecutionContext that submits real orders to CEX exchanges via CCXT.

    Same interface as LiveDriftContext and LiveHyperliquidContext —
    strategies work identically across all venues.

    Usage:
        ctx = LiveCCXTContext(
            exchange="binance",
            api_key="...",
            secret="...",
            sandbox=True,
        )
        await ctx.connect()
        await ctx.run(strategy, market="BTC-PERP")
    """

    def __init__(
        self,
        exchange: Optional[str] = None,
        api_key: Optional[str] = None,
        secret: Optional[str] = None,
        password: Optional[str] = None,
        sandbox: Optional[bool] = None,
        market_order_slippage: float = 0.002,
        initial_capital: float = 0,
        risk_manager=None,
        store=None,
        session_id: str = "",
        max_retries: int = 3,
        on_failure: str = "drop",
        **kwargs,
    ):
        self._exchange_name = (
            exchange
            or os.environ.get("FLINT_CCXT_EXCHANGE", "binance")
        ).lower()

        self._api_key = api_key or os.environ.get("FLINT_CCXT_API_KEY", "")
        self._secret = secret or os.environ.get("FLINT_CCXT_SECRET", "")
        self._password = password or os.environ.get("FLINT_CCXT_PASSWORD", "")

        if sandbox is None:
            self._sandbox = os.environ.get("FLINT_CCXT_SANDBOX", "true").lower() == "true"
        else:
            self._sandbox = sandbox

        if not self._api_key or not self._secret:
            raise ValueError(
                f"No API credentials for {self._exchange_name}. Set FLINT_CCXT_API_KEY "
                "and FLINT_CCXT_SECRET environment variables, or pass api_key/secret."
            )

        self._market_order_slippage = market_order_slippage
        self._exchange = None  # Created in _connect()
        self._venue_order_map: Dict[str, str] = {}  # venue_id -> flint_market

        super().__init__(
            venue=self._exchange_name,
            initial_capital=initial_capital,
            risk_manager=risk_manager,
            store=store,
            session_id=session_id,
            max_retries=max_retries,
            on_failure=on_failure,
            **kwargs,
        )

        logger.info(
            "LiveCCXTContext initialized (exchange=%s, sandbox=%s)",
            self._exchange_name, self._sandbox,
        )

    # --- Abstract method implementations ---

    async def _connect(self) -> None:
        import ccxt.async_support as ccxt_async

        exchange_cls = getattr(ccxt_async, self._exchange_name, None)
        if exchange_cls is None:
            raise ValueError(f"Unknown CCXT exchange: {self._exchange_name}")

        config = {
            "apiKey": self._api_key,
            "secret": self._secret,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
        if self._password:
            config["password"] = self._password

        self._exchange = exchange_cls(config)

        if self._sandbox:
            self._exchange.set_sandbox_mode(True)

        await self._exchange.load_markets()
        logger.info(
            "Connected to %s (%s), %d markets loaded",
            self._exchange_name,
            "sandbox" if self._sandbox else "live",
            len(self._exchange.markets),
        )

    async def _disconnect(self) -> None:
        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None
            logger.info("Disconnected from %s", self._exchange_name)

    async def _place_order(self, order: Order) -> Tuple[str, Optional[int]]:
        if self._exchange is None:
            raise RuntimeError("Not connected — call connect() first")

        symbol = _to_ccxt_symbol(order.market)
        side_str = "buy" if order.side == Side.LONG else "sell"
        amount = order.size

        params = {}

        if order.order_type == OrderType.MARKET:
            result = await self._exchange.create_order(
                symbol=symbol, type="market", side=side_str, amount=amount,
                params=params,
            )
        elif order.order_type == OrderType.LIMIT:
            result = await self._exchange.create_order(
                symbol=symbol, type="limit", side=side_str,
                amount=amount, price=order.price, params=params,
            )
        elif order.order_type == OrderType.STOP_LOSS:
            params["stopLossPrice"] = order.price
            result = await self._exchange.create_order(
                symbol=symbol, type="market", side=side_str,
                amount=amount, params=params,
            )
        elif order.order_type == OrderType.TAKE_PROFIT:
            params["takeProfitPrice"] = order.price
            result = await self._exchange.create_order(
                symbol=symbol, type="market", side=side_str,
                amount=amount, params=params,
            )
        else:
            raise ValueError(f"Unsupported order type: {order.order_type}")

        venue_id = result.get("id", "")
        self._venue_order_map[venue_id] = order.market
        logger.info(
            "Order submitted to %s: %s %s %s @ %s, venue_id=%s",
            self._exchange_name, side_str, amount, symbol,
            order.price or "market", venue_id,
        )
        # CCXT returns string IDs; convert to int for the interface
        venue_order_id = int(venue_id) if venue_id and venue_id.isdigit() else hash(venue_id) & 0x7FFFFFFF
        return (venue_id, venue_order_id)

    async def _cancel_order(self, venue_order_id: int) -> bool:
        if self._exchange is None:
            return False
        try:
            # Find the original string ID
            str_id = str(venue_order_id)
            for vid, mkt in self._venue_order_map.items():
                if hash(vid) & 0x7FFFFFFF == venue_order_id or vid == str_id:
                    symbol = _to_ccxt_symbol(mkt)
                    await self._exchange.cancel_order(vid, symbol)
                    return True
            logger.warning("No mapping for venue_order_id %d", venue_order_id)
            return False
        except Exception as e:
            logger.error("Cancel order %d failed: %s", venue_order_id, e)
            return False

    async def _fetch_positions(self) -> List[PositionInfo]:
        if self._exchange is None:
            return []
        try:
            raw_positions = await self._exchange.fetch_positions()
            positions = []
            for pos in raw_positions:
                contracts = float(pos.get("contracts", 0))
                if contracts == 0:
                    continue
                ccxt_symbol = pos.get("symbol", "")
                flint_market = _to_flint_symbol(ccxt_symbol)
                pos_side = pos.get("side", "long")
                side = Side.LONG if pos_side == "long" else Side.SHORT
                entry_price = float(pos.get("entryPrice", 0) or 0)
                unrealized = float(pos.get("unrealizedPnl", 0) or 0)
                positions.append(PositionInfo(
                    market=flint_market, side=side, size=contracts,
                    entry_price=entry_price, unrealized_pnl=unrealized,
                    venue=self._exchange_name,
                ))
            return positions
        except Exception as e:
            logger.error("Position fetch from %s failed: %s", self._exchange_name, e)
            return []

    async def _fetch_balance(self) -> float:
        if self._exchange is None:
            return 0.0
        try:
            balance = await self._exchange.fetch_balance()
            # Use USDT total balance for perps accounts
            usdt = balance.get("USDT", {})
            total = float(usdt.get("total", 0) or 0)
            if total > 0:
                return total
            # Fallback: sum all total values
            return float(balance.get("total", {}).get("USDT", 0) or 0)
        except Exception as e:
            logger.error("Balance fetch from %s failed: %s", self._exchange_name, e)
            return 0.0

    async def _poll_order_status(self, venue_order_id: int) -> OrderState:
        if self._exchange is None:
            return OrderState.FAILED
        try:
            str_id = str(venue_order_id)
            symbol = None
            for vid, mkt in self._venue_order_map.items():
                if hash(vid) & 0x7FFFFFFF == venue_order_id or vid == str_id:
                    str_id = vid
                    symbol = _to_ccxt_symbol(mkt)
                    break

            if symbol is None:
                return OrderState.FAILED

            order_info = await self._exchange.fetch_order(str_id, symbol)
            status = order_info.get("status", "")

            if status == "closed":
                return OrderState.FILLED
            elif status == "canceled" or status == "cancelled" or status == "expired":
                return OrderState.CANCELLED
            elif status == "open":
                return OrderState.CONFIRMED
            else:
                return OrderState.SUBMITTED
        except Exception as e:
            logger.error("Poll order %d on %s failed: %s", venue_order_id, self._exchange_name, e)
            return OrderState.FAILED
