"""LiveDriftContext — ExecutionContext for real order execution on Drift Protocol.

Requires `driftpy` package: pip install driftpy
Uses the same strategy code as backtest — backtest-live symmetry.

Environment variables:
    FLINT_PRIVATE_KEY: Base58-encoded Solana private key
    FLINT_RPC_URL: Solana RPC endpoint (default: mainnet public)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional

from ..models import (
    AccountState, Candle, Fill, Order, OrderType, OrderStatus,
    PositionInfo, Side,
)
from .context import ExecutionContext

logger = logging.getLogger("flint.drift_live")

# Drift market index → symbol mapping
MARKET_TO_INDEX = {
    "SOL-PERP": 0, "BTC-PERP": 1, "ETH-PERP": 2, "APT-PERP": 3,
    "1MBONK-PERP": 4, "POL-PERP": 5, "ARB-PERP": 6, "DOGE-PERP": 7,
    "BNB-PERP": 8, "SUI-PERP": 9, "1MPEPE-PERP": 10, "OP-PERP": 11,
    "RENDER-PERP": 12, "XRP-PERP": 13, "HNT-PERP": 14, "INJ-PERP": 15,
    "LINK-PERP": 16, "RLB-PERP": 17, "PYTH-PERP": 18, "TIA-PERP": 19,
    "JTO-PERP": 20, "SEI-PERP": 21, "AVAX-PERP": 22, "WIF-PERP": 23,
    "JUP-PERP": 24, "DYM-PERP": 25, "TAO-PERP": 26, "W-PERP": 27,
    "KMNO-PERP": 28, "TNSR-PERP": 29, "DRIFT-PERP": 30,
}


def _check_driftpy():
    """Check if driftpy is installed."""
    try:
        import driftpy  # noqa: F401
        return True
    except ImportError:
        return False


class LiveDriftContext(ExecutionContext):
    """ExecutionContext that submits real orders to Drift Protocol.

    Uses driftpy SDK for on-chain order execution.
    Same interface as BacktestContext — strategies work identically.
    """

    def __init__(
        self,
        private_key: Optional[str] = None,
        rpc_url: Optional[str] = None,
        initial_capital: float = 0,
    ):
        if not _check_driftpy():
            raise ImportError(
                "driftpy is required for live trading. Install with: pip install driftpy\n"
                "Note: requires Python 3.10+ and Solana CLI tools."
            )

        self._private_key = private_key or os.environ.get("FLINT_PRIVATE_KEY", "")
        self._rpc_url = rpc_url or os.environ.get(
            "FLINT_RPC_URL", "https://api.mainnet-beta.solana.com"
        )

        if not self._private_key:
            raise ValueError(
                "No private key provided. Set FLINT_PRIVATE_KEY environment variable "
                "or pass private_key parameter."
            )

        self._drift_client = None
        self._positions_cache: Dict[str, PositionInfo] = {}
        self._pending: List[Order] = []
        self._fills: List[Fill] = []
        self._current_candle: Optional[Candle] = None
        self._order_counter = 0
        self._initial_capital = initial_capital
        self._cash = initial_capital

        logger.info("LiveDriftContext initialized (RPC: %s)", self._rpc_url)

    async def connect(self) -> None:
        """Initialize driftpy client and connect to Drift."""
        from driftpy.drift_client import DriftClient
        from driftpy.keypair import load_keypair
        from solders.keypair import Keypair  # type: ignore
        from solana.rpc.async_api import AsyncClient

        # Load keypair
        kp = Keypair.from_base58_string(self._private_key)
        connection = AsyncClient(self._rpc_url)

        self._drift_client = DriftClient(
            connection=connection,
            wallet=kp,
        )
        await self._drift_client.subscribe()
        logger.info("Connected to Drift Protocol")

        # Sync positions
        await self._sync_positions()

    async def _sync_positions(self) -> None:
        """Reconcile local state with on-chain positions."""
        if self._drift_client is None:
            return

        try:
            user = self._drift_client.get_user()
            perp_positions = user.get_perp_positions()

            self._positions_cache.clear()
            for pos in perp_positions:
                if pos.base_asset_amount == 0:
                    continue
                market_idx = pos.market_index
                # Reverse lookup market name
                market_name = None
                for name, idx in MARKET_TO_INDEX.items():
                    if idx == market_idx:
                        market_name = name
                        break
                if market_name is None:
                    continue

                from ..precision import from_drift_base, from_drift_price
                size = from_drift_base(abs(pos.base_asset_amount))
                side = Side.LONG if pos.base_asset_amount > 0 else Side.SHORT
                entry = from_drift_price(pos.entry_price) if hasattr(pos, 'entry_price') else 0

                self._positions_cache[market_name] = PositionInfo(
                    market=market_name,
                    side=side,
                    size=size,
                    entry_price=entry,
                    unrealized_pnl=0,  # TODO: compute from oracle
                )
            logger.info("Synced %d positions from Drift", len(self._positions_cache))
        except Exception as e:
            logger.error("Position sync failed: %s", e)

    def _next_id(self) -> str:
        self._order_counter += 1
        return f"live-{self._order_counter}"

    # --- ExecutionContext interface ---

    @property
    def account(self) -> AccountState:
        unrealized = sum(p.unrealized_pnl for p in self._positions_cache.values())
        return AccountState(
            equity=self._cash + unrealized,
            cash=self._cash,
            unrealized_pnl=unrealized,
        )

    @property
    def positions(self) -> List[PositionInfo]:
        return list(self._positions_cache.values())

    @property
    def pending_orders(self) -> List[Order]:
        return list(self._pending)

    @property
    def current_candle(self) -> Optional[Candle]:
        return self._current_candle

    @property
    def timestamp(self) -> int:
        return int(time.time())

    def market_order(self, market, side, size, reduce_only=False, tag=""):
        oid = self._next_id()
        order = Order(
            market=market, side=side, order_type=OrderType.MARKET,
            size=size, order_id=oid, ts=self.timestamp,
        )
        self._pending.append(order)
        logger.info("LIVE ORDER: %s %s %.4f %s (id=%s)", side.value, market, size,
                     "reduce_only" if reduce_only else "", oid)
        return oid

    def limit_order(self, market, side, size, price, reduce_only=False, tag=""):
        oid = self._next_id()
        order = Order(
            market=market, side=side, order_type=OrderType.LIMIT,
            size=size, price=price, order_id=oid, ts=self.timestamp,
        )
        self._pending.append(order)
        logger.info("LIVE LIMIT: %s %s %.4f @ %.2f (id=%s)", side.value, market, size, price, oid)
        return oid

    def stop_order(self, market, side, size, trigger_price, tag=""):
        oid = self._next_id()
        order = Order(
            market=market, side=side, order_type=OrderType.STOP_LOSS,
            size=size, price=trigger_price, order_id=oid, ts=self.timestamp,
        )
        self._pending.append(order)
        logger.info("LIVE STOP: %s %s %.4f trigger=%.2f (id=%s)", side.value, market, size, trigger_price, oid)
        return oid

    def take_profit_order(self, market, side, size, trigger_price, tag=""):
        oid = self._next_id()
        order = Order(
            market=market, side=side, order_type=OrderType.TAKE_PROFIT,
            size=size, price=trigger_price, order_id=oid, ts=self.timestamp,
        )
        self._pending.append(order)
        return oid

    def cancel(self, order_id):
        for i, o in enumerate(self._pending):
            if o.order_id == order_id:
                self._pending.pop(i)
                return True
        return False

    def cancel_all(self, market=None):
        if market is None:
            count = len(self._pending)
            self._pending.clear()
            return count
        before = len(self._pending)
        self._pending = [o for o in self._pending if o.market != market]
        return before - len(self._pending)

    async def submit_pending_orders(self) -> List[Fill]:
        """Submit all pending orders to Drift on-chain.

        Called by the live trading engine after each strategy tick.
        """
        if self._drift_client is None:
            logger.error("Not connected to Drift — call connect() first")
            return []

        fills = []
        remaining = []

        for order in self._pending:
            try:
                market_idx = MARKET_TO_INDEX.get(order.market)
                if market_idx is None:
                    logger.error("Unknown market: %s", order.market)
                    remaining.append(order)
                    continue

                # Convert to driftpy order
                from driftpy.types import (
                    OrderParams, OrderType as DriftOrderType,
                    MarketType, PositionDirection,
                )

                direction = (
                    PositionDirection.Long()
                    if order.side == Side.LONG
                    else PositionDirection.Short()
                )

                from ..precision import to_drift_base, to_drift_price
                size_base = to_drift_base(order.size)

                if order.order_type == OrderType.MARKET:
                    order_params = OrderParams(
                        order_type=DriftOrderType.Market(),
                        market_index=market_idx,
                        market_type=MarketType.Perp(),
                        direction=direction,
                        base_asset_amount=size_base,
                    )
                elif order.order_type == OrderType.LIMIT:
                    price_int = to_drift_price(order.price)
                    order_params = OrderParams(
                        order_type=DriftOrderType.Limit(),
                        market_index=market_idx,
                        market_type=MarketType.Perp(),
                        direction=direction,
                        base_asset_amount=size_base,
                        price=price_int,
                    )
                else:
                    # Stop/TP orders — use trigger orders
                    trigger_price_int = to_drift_price(order.price)
                    order_params = OrderParams(
                        order_type=DriftOrderType.TriggerMarket(),
                        market_index=market_idx,
                        market_type=MarketType.Perp(),
                        direction=direction,
                        base_asset_amount=size_base,
                        trigger_price=trigger_price_int,
                    )

                # Submit to Drift
                tx_sig = await self._drift_client.place_perp_order(order_params)
                logger.info("Order submitted: %s tx=%s", order.order_id, tx_sig)

                fill = Fill(
                    market=order.market,
                    side=order.side,
                    price=order.price if order.price > 0 else 0,
                    size=order.size,
                    fee=0,  # actual fee comes from on-chain
                    ts=int(time.time()),
                    order_id=order.order_id,
                    tx_sig=str(tx_sig) if tx_sig else "",
                )
                fills.append(fill)
                self._fills.append(fill)

            except Exception as e:
                logger.error("Order failed: %s — %s", order.order_id, e)
                remaining.append(order)

        self._pending = remaining

        # Sync positions after order submission
        await self._sync_positions()

        return fills

    async def disconnect(self) -> None:
        """Disconnect from Drift."""
        if self._drift_client is not None:
            await self._drift_client.unsubscribe()
            self._drift_client = None
            logger.info("Disconnected from Drift")
