"""Flint FastAPI application."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .routes import backtest, strategies, data, mev, user_strategies, collector, paper, optimization, journal
from ..config import FlintConfig, load_config
from ..store import FlintStore
from ..collector.service import CollectorService
from ..paper.engine import PaperTradingEngine
from .websocket import ConnectionManager

logger = logging.getLogger("flint.api")

# Locate UI build directory
_UI_DIST = Path(__file__).resolve().parent.parent.parent / "ui" / "dist"


async def _sync_funding_coverage(store: FlintStore):
    """Background task: purge bad funding data and fetch latest rates."""
    await asyncio.sleep(2)  # Let server start first
    try:
        import threading
        def _run():
            from .routes.data import _sync_funding_to_candle_range
            import logging as _log
            _logger = _log.getLogger("flint.startup")

            # ALWAYS purge corrupted funding rates on startup
            with store._lock:
                purged = store._conn.execute(
                    "DELETE FROM funding_rates WHERE ABS(rate) > 0.005"
                ).fetchone()
                bad_count = store._conn.execute(
                    "SELECT changes()"
                ).fetchone()
            _logger.info("Startup cleanup: purged corrupted funding rates")

            # Get all perp markets with candle data
            with store._lock:
                rows = store._conn.execute(
                    "SELECT DISTINCT market FROM candles WHERE market LIKE '%-PERP'"
                ).fetchall()

            if not rows:
                return

            for (market,) in rows:
                # Check if we have any funding data at all
                existing = store.query_funding_rates(market)
                if not existing:
                    _logger.info("No funding data for %s — fetching from Drift...", market)
                    try:
                        # Drift funding API only has ~30 days of history
                        import time as _time
                        now = int(_time.time())
                        _sync_funding_to_candle_range(store, market, now - 90 * 86400, now, _logger)
                    except Exception as e:
                        _logger.warning("Funding fetch failed for %s: %s", market, e)

            _logger.info("Funding coverage check complete")

        await asyncio.to_thread(_run)
    except Exception as e:
        logger.warning("Funding coverage sync error: %s", e)


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

        # Background task: sync funding coverage to match candle data
        asyncio.create_task(_sync_funding_coverage(store))
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


# ─── Serve built UI static files ───────────────────────────────
# If ui/dist exists (production build), serve it. All non-API routes
# fall through to index.html for client-side routing.
if _UI_DIST.exists() and (_UI_DIST / "index.html").exists():
    # Serve static assets (JS, CSS, images)
    app.mount("/assets", StaticFiles(directory=str(_UI_DIST / "assets")), name="ui-assets")

    # Serve other static files at root (favicon, etc.)
    @app.get("/favicon.svg")
    async def favicon():
        path = _UI_DIST / "favicon.svg"
        if path.exists():
            return FileResponse(str(path))

    # Catch-all: serve index.html for client-side routing
    @app.get("/{path:path}")
    async def serve_ui(path: str):
        # Don't serve index.html for API routes or WebSocket
        if path.startswith("api/") or path.startswith("ws"):
            return {"error": "not found"}
        # Check if it's a real file in dist
        file_path = _UI_DIST / path
        if file_path.is_file():
            return FileResponse(str(file_path))
        # Otherwise serve index.html (SPA routing)
        return FileResponse(str(_UI_DIST / "index.html"))

    logger.info("Serving UI from %s", _UI_DIST)


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
