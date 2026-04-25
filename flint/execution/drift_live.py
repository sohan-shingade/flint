"""LiveDriftContext — ExecutionContext for real order execution on Drift Protocol.

Requires `driftpy` package: pip install driftpy
Uses the same strategy code as backtest — backtest-live symmetry.

Environment variables:
    FLINT_PRIVATE_KEY: Base58-encoded Solana private key
    FLINT_RPC_URL: Solana RPC endpoint (overrides network default)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List, Optional, Tuple

from ..models import (
    Order, OrderState, OrderType, PositionInfo, Side,
)
from ..precision import from_drift_base, from_drift_price, to_drift_base, to_drift_price
from .live_base import LiveExecutionContext
from .wallet import KeypairAdapter

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

INDEX_TO_MARKET = {v: k for k, v in MARKET_TO_INDEX.items()}

_NETWORK_RPC = {
    "devnet": "https://api.devnet.solana.com",
    "mainnet": "https://api.mainnet-beta.solana.com",
}


async def _retry_with_backoff(coro_fn, max_retries=3, base_delay=1.0):
    """Retry an async operation with exponential backoff.

    Args:
        coro_fn: Async callable (no args) to retry.
        max_retries: Max attempts.
        base_delay: Initial delay in seconds (doubles each retry).
    Returns:
        Result of coro_fn().
    Raises:
        Last exception if all retries fail.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            return await coro_fn()
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning("Attempt %d/%d failed: %s. Retrying in %.1fs",
                             attempt + 1, max_retries, e, delay)
                await asyncio.sleep(delay)
    raise last_error


def _check_driftpy():
    """Check if driftpy is installed."""
    try:
        import driftpy  # noqa: F401
        return True
    except ImportError:
        return False


class LiveDriftContext(LiveExecutionContext):
    """ExecutionContext that submits real orders to Drift Protocol.

    Uses driftpy SDK for on-chain order execution.
    Same interface as BacktestContext — strategies work identically.
    """

    def __init__(
        self,
        private_key: Optional[str] = None,
        rpc_url: Optional[str] = None,
        network: str = "devnet",
        initial_capital: float = 0,
        risk_manager=None,
        store=None,
        session_id: str = "",
        max_retries: int = 3,
        on_failure: str = "drop",
    ):
        if not _check_driftpy():
            raise ImportError(
                "driftpy is required for live trading. Install with: pip install driftpy\n"
                "Note: requires Python 3.10+ and Solana CLI tools."
            )

        # Resolve RPC URL: env override > param > network default
        self._rpc_url = (
            os.environ.get("FLINT_RPC_URL")
            or rpc_url
            or _NETWORK_RPC.get(network, _NETWORK_RPC["devnet"])
        )
        self._network = network

        # Create wallet adapter
        key = private_key or os.environ.get("FLINT_PRIVATE_KEY", "")
        if not key:
            raise ValueError(
                "No private key provided. Set FLINT_PRIVATE_KEY environment variable "
                "or pass private_key parameter."
            )
        self._wallet = KeypairAdapter(private_key=key)
        self._drift_client = None

        super().__init__(
            venue="drift",
            initial_capital=initial_capital,
            risk_manager=risk_manager,
            store=store,
            session_id=session_id,
            max_retries=max_retries,
            on_failure=on_failure,
        )

        logger.info("LiveDriftContext initialized (network=%s, RPC=%s)", network, self._rpc_url)

    # --- Abstract method implementations ---

    async def _connect(self) -> None:
        from driftpy.drift_client import DriftClient
        from solana.rpc.async_api import AsyncClient

        connection = AsyncClient(self._rpc_url)
        env = "devnet" if self._network == "devnet" else "mainnet"

        self._drift_client = DriftClient(
            connection=connection,
            wallet=self._wallet.keypair,
            env=env,
        )
        await self._drift_client.subscribe()
        logger.info("Connected to Drift Protocol (%s)", self._network)

    async def _disconnect(self) -> None:
        if self._drift_client is not None:
            await self._drift_client.unsubscribe()
            self._drift_client = None
            logger.info("Disconnected from Drift")

    async def _place_order(self, order: Order) -> Tuple[str, Optional[int]]:
        if self._drift_client is None:
            raise RuntimeError("Not connected to Drift — call connect() first")

        market_idx = MARKET_TO_INDEX.get(order.market)
        if market_idx is None:
            raise ValueError(f"Unknown Drift market: {order.market}")

        # Pre-flight: check collateral
        try:
            balance = await self._fetch_balance()
            if balance <= 0:
                raise ValueError(f"Insufficient collateral: {balance:.2f} USDC available")
        except ValueError:
            raise
        except Exception as e:
            logger.warning("Collateral check failed, proceeding: %s", e)

        from driftpy.types import (
            OrderParams, OrderType as DriftOrderType,
            MarketType, PositionDirection,
        )

        direction = (
            PositionDirection.Long()
            if order.side == Side.LONG
            else PositionDirection.Short()
        )
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
        elif order.order_type == OrderType.STOP_LOSS:
            trigger_price_int = to_drift_price(order.price)
            order_params = OrderParams(
                order_type=DriftOrderType.TriggerMarket(),
                market_index=market_idx,
                market_type=MarketType.Perp(),
                direction=direction,
                base_asset_amount=size_base,
                trigger_price=trigger_price_int,
            )
        elif order.order_type == OrderType.TAKE_PROFIT:
            trigger_price_int = to_drift_price(order.price)
            order_params = OrderParams(
                order_type=DriftOrderType.TriggerLimit(),
                market_index=market_idx,
                market_type=MarketType.Perp(),
                direction=direction,
                base_asset_amount=size_base,
                trigger_price=trigger_price_int,
            )
        else:
            raise ValueError(f"Unsupported order type: {order.order_type}")

        # Submit with retry + exponential backoff
        async def _submit():
            return await self._drift_client.place_perp_order(order_params)

        tx_sig = await _retry_with_backoff(_submit, max_retries=3, base_delay=1.0)
        tx_sig_str = str(tx_sig) if tx_sig else ""
        logger.info("Order submitted: %s tx=%s", order.order_id, tx_sig_str)

        return (tx_sig_str, None)

    async def _cancel_order(self, venue_order_id: int) -> bool:
        if self._drift_client is None:
            return False
        try:
            await self._drift_client.cancel_order(venue_order_id)
            return True
        except Exception as e:
            logger.error("Cancel order %d failed: %s", venue_order_id, e)
            return False

    async def _fetch_positions(self) -> List[PositionInfo]:
        if self._drift_client is None:
            return []
        try:
            async def _fetch():
                user = self._drift_client.get_user()
                return user.get_perp_positions()
            perp_positions = await _retry_with_backoff(_fetch, max_retries=2, base_delay=0.5)
            result = []
            for pos in perp_positions:
                if pos.base_asset_amount == 0:
                    continue
                market_name = INDEX_TO_MARKET.get(pos.market_index)
                if market_name is None:
                    continue
                size = from_drift_base(abs(pos.base_asset_amount))
                side = Side.LONG if pos.base_asset_amount > 0 else Side.SHORT
                entry = from_drift_price(pos.entry_price) if hasattr(pos, 'entry_price') else 0

                unrealized = 0.0
                try:
                    market_account = self._drift_client.get_perp_market_account(pos.market_index)
                    oracle_price = from_drift_price(
                        market_account.amm.historical_oracle_data.last_oracle_price
                    )
                    if side == Side.LONG:
                        unrealized = (oracle_price - entry) * size
                    else:
                        unrealized = (entry - oracle_price) * size
                except Exception:
                    pass

                result.append(PositionInfo(
                    market=market_name, side=side, size=size,
                    entry_price=entry, unrealized_pnl=unrealized, venue="drift",
                ))
            return result
        except Exception as e:
            logger.error("Position fetch failed: %s", e)
            return []

    async def _fetch_balance(self) -> float:
        if self._drift_client is None:
            return 0.0
        try:
            async def _fetch():
                user = self._drift_client.get_user()
                return user.get_free_collateral()
            free_collateral = await _retry_with_backoff(_fetch, max_retries=2, base_delay=0.5)
            from ..precision import QUOTE_PRECISION
            return float(free_collateral) / QUOTE_PRECISION
        except Exception as e:
            logger.error("Balance fetch failed: %s", e)
            return 0.0

    async def _poll_order_status(self, venue_order_id: int) -> OrderState:
        if self._drift_client is None:
            return OrderState.FAILED
        try:
            async def _fetch():
                user = self._drift_client.get_user()
                return user.get_order(venue_order_id)
            order = await _retry_with_backoff(_fetch, max_retries=2, base_delay=0.5)
            if order is None:
                return OrderState.CANCELLED

            filled = from_drift_base(order.base_asset_amount_filled)
            total = from_drift_base(order.base_asset_amount)

            if filled >= total:
                return OrderState.FILLED
            elif filled > 0:
                return OrderState.PARTIALLY_FILLED
            else:
                return OrderState.CONFIRMED
        except Exception as e:
            logger.error("Order status poll failed for %d: %s", venue_order_id, e)
            return OrderState.CONFIRMED
