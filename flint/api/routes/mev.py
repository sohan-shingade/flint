"""MEV API routes."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from ...mev.arb import ArbDetector
from ...mev.liquidation import LiquidationScanner
from ...models import PoolState

router = APIRouter()


class PoolInput(BaseModel):
    pool_address: str
    dex: str
    token_a_mint: str
    token_b_mint: str
    reserve_a: float
    reserve_b: float
    fee_rate: float = 0.003


class ArbScanRequest(BaseModel):
    pools: List[PoolInput]
    start_token: str
    amount: float = 1.0
    max_hops: int = 4
    min_profit_bps: float = 10.0


class PositionInput(BaseModel):
    user_account: str
    market: str
    size: float
    entry_price: float
    collateral: float
    protocol: str = "drift"


class LiqScanRequest(BaseModel):
    positions: List[PositionInput]
    oracle_prices: dict


@router.post("/scan/arb")
def scan_arb(req: ArbScanRequest):
    pools = [
        PoolState(
            pool_address=p.pool_address, dex=p.dex,
            token_a_mint=p.token_a_mint, token_b_mint=p.token_b_mint,
            reserve_a=p.reserve_a, reserve_b=p.reserve_b, fee_rate=p.fee_rate,
        )
        for p in req.pools
    ]
    detector = ArbDetector(min_profit_bps=req.min_profit_bps)
    detector.update_pools(pools)
    routes = detector.find_arb_routes(req.start_token, req.amount, req.max_hops)
    return {
        "routes": [
            {
                "pools": list(r.pools), "tokens": list(r.tokens),
                "input_amount": r.input_amount, "output_amount": r.output_amount,
                "profit": r.profit, "profit_bps": r.profit_bps, "hops": r.hops,
            }
            for r in routes
        ]
    }


@router.post("/scan/liquidations")
def scan_liquidations(req: LiqScanRequest):
    scanner = LiquidationScanner()
    positions = [p.model_dump() for p in req.positions]
    opps = scanner.scan_positions(positions, req.oracle_prices)
    return {
        "opportunities": [
            {
                "protocol": o.protocol, "user_account": o.user_account,
                "market": o.market, "side": o.side.value,
                "position_size": o.position_size,
                "liquidation_price": o.liquidation_price,
                "oracle_price": o.oracle_price,
                "estimated_profit": o.estimated_profit,
            }
            for o in opps
        ]
    }
