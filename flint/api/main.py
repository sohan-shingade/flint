"""Flint FastAPI application."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import backtest, strategies, data, mev, user_strategies, collector
from ..store import FlintStore
from ..collector.service import CollectorService

logger = logging.getLogger("flint.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init shared store and collector. Shutdown: clean up."""
    store = None
    collector_svc = None
    task = None

    try:
        store = FlintStore("./data/flint.duckdb")
        app.state.store = store

        collector_svc = CollectorService(store)
        app.state.collector = collector_svc
        task = asyncio.create_task(collector_svc.run())
        logger.info("Flint API started with collector")
    except Exception as exc:
        logger.warning("Lifespan startup failed (non-fatal): %s", exc)

    yield

    if collector_svc is not None:
        collector_svc.stop()
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if store is not None:
        store.close()
    logger.info("Flint API shutdown complete")


app = FastAPI(
    title="Flint",
    description="Algorithmic trading, backtesting, and MEV research platform for Solana",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(backtest.router, prefix="/api/v1/backtest", tags=["backtest"])
app.include_router(strategies.router, prefix="/api/v1/strategies", tags=["strategies"])
app.include_router(user_strategies.router, prefix="/api/v1/user-strategies", tags=["user-strategies"])
app.include_router(data.router, prefix="/api/v1/data", tags=["data"])
app.include_router(mev.router, prefix="/api/v1/mev", tags=["mev"])
app.include_router(collector.router, prefix="/api/v1/collector", tags=["collector"])


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "service": "flint"}
