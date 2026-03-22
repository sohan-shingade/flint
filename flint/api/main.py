"""Flint FastAPI application."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .routes import backtest, strategies, data, mev, user_strategies, collector, paper, optimization, journal
from ..config import FlintConfig, load_config
from ..store import FlintStore
from ..collector.service import CollectorService
from ..paper.engine import PaperTradingEngine
from .websocket import ConnectionManager

logger = logging.getLogger("flint.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init shared store and collector. Shutdown: clean up."""
    store = None
    collector_svc = None
    task = None

    try:
        config = load_config()
        app.state.config = config

        store = FlintStore(config.db_path)
        app.state.store = store

        if config.collector_enabled:
            collector_svc = CollectorService(store, config=config)
            app.state.collector = collector_svc
            task = asyncio.create_task(collector_svc.run())

        paper_engine = PaperTradingEngine(store)
        app.state.paper_engine = paper_engine

        ws_manager = ConnectionManager()
        app.state.ws_manager = ws_manager

        logger.info("Flint API started (collector=%s)", config.collector_enabled)
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
app.include_router(paper.router, prefix="/api/v1/paper", tags=["paper"])
app.include_router(optimization.router, prefix="/api/v1/optimize", tags=["optimize"])
app.include_router(journal.router, prefix="/api/v1/journal", tags=["journal"])


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "service": "flint"}


@app.websocket("/ws/{channel}")
async def websocket_endpoint(websocket: WebSocket, channel: str = "all"):
    manager = getattr(websocket.app.state, "ws_manager", None)
    if manager is None:
        await websocket.close()
        return
    await manager.connect(websocket, channel)
    try:
        while True:
            await websocket.receive_text()  # keep alive
    except WebSocketDisconnect:
        manager.disconnect(websocket, channel)
