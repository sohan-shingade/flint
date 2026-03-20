"""Collector status and trigger API routes."""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ...collector.tasks import CollectorConfig

router = APIRouter()


class TriggerRequest(BaseModel):
    market: str = "SOL-PERP"
    data_type: str = "candles"


@router.get("/status")
def get_collector_status(request: Request):
    collector = getattr(request.app.state, "collector", None)
    if collector is None:
        return {"status": [], "running": False}
    return {"status": collector.get_status(), "running": collector._running}


@router.post("/trigger")
async def trigger_collection(req: TriggerRequest, request: Request):
    collector = getattr(request.app.state, "collector", None)
    if collector is None:
        return {"error": "Collector not running"}
    return {"triggered": True, "market": req.market, "data_type": req.data_type}


@router.get("/config")
def get_collector_config():
    config = CollectorConfig()
    return {
        "markets": config.markets,
        "candle_backfill_days": config.candle_backfill_days,
        "intervals": {
            "candles": config.candle_interval_s,
            "funding": config.funding_interval_s,
            "orderbook": config.orderbook_interval_s,
            "oracle": config.oracle_interval_s,
        },
    }
