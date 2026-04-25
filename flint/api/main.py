"""Flint FastAPI application."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .routes import backtest, strategies, data, mev, user_strategies, collector, paper, optimization, journal, system, replay
from .routes.live import router as live_router
from .routes.backtest import configure_concurrency
from ..config import load_config
from ..store import FlintStore
from ..collector.service import CollectorService
from ..paper.engine import PaperTradingEngine
from ..paper.price_ticker import PriceTicker
from ..paper.price_sources import default_chain
from .websocket import ConnectionManager

logger = logging.getLogger("flint.api")

# Locate UI build directory
_UI_DIST = Path(__file__).resolve().parent.parent.parent / "ui" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init shared store and collector. Shutdown: clean up."""
    store = None
    collector_svc = None
    task = None
    ticker = None
    ticker_task = None

    try:
        config = load_config()
        app.state.config = config

        store = FlintStore(config.db_path)
        app.state.store = store

        configure_concurrency(config.max_concurrent_backtests)

        from flint.migration import run_pyth_migration
        try:
            migration_result = run_pyth_migration(store)
            if migration_result["markets_migrated"]:
                logger.info(f"Pyth migration complete: {migration_result}")
        except Exception as e:
            logger.warning(f"Pyth migration failed (non-fatal): {e}")

        if config.collector_enabled:
            collector_svc = CollectorService(store, config=config)
            app.state.collector = collector_svc
            task = asyncio.create_task(collector_svc.run())

        paper_engine = PaperTradingEngine(store)
        paper_engine.set_event_loop(asyncio.get_running_loop())
        paper_engine.resume_sessions()
        app.state.paper_engine = paper_engine
        # ws_manager is constructed below; wire it onto the engine
        # after both exist so per-bar broadcasts can reach clients.
        # D-4.3-websocket slice 2.

        # Mark stale live sessions as interrupted (they can't auto-resume
        # because live trading requires venue credentials and connections)
        try:
            stale = store.mark_running_live_sessions_interrupted(int(time.time()))
            for s in stale:
                logger.warning(
                    "Live session %s (%s on %s/%s) marked interrupted — "
                    "redeploy manually via the Live Trading page",
                    s["session_id"], s["strategy"], s["market"], s["venue"],
                )
        except Exception as e:
            logger.debug("Live session cleanup: %s", e)

        # Start price ticker for live PnL updates. The chain wraps
        # HL (live) → Drift DLOB (offline placeholder) → DuckDB
        # last-candle (stale fallback). Drift is registered so the
        # chain auto-recovers when Drift comes back, but until then
        # HL serves and DuckDB catches double-outage windows.
        ticker = PriceTicker(
            config.default_markets,
            interval_s=5.0,
            sources=default_chain(store),
        )
        app.state.price_ticker = ticker
        ticker_task = asyncio.create_task(ticker.run())
        paper_engine.price_ticker = ticker

        ws_manager = ConnectionManager()
        app.state.ws_manager = ws_manager
        # D-4.3-websocket slice 2: thread the manager into the paper
        # engine so per-bar equity ticks reach `paper:{session_id}`
        # subscribers.
        paper_engine.ws_manager = ws_manager

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
    if ticker is not None:
        ticker.stop()
    if ticker_task is not None:
        ticker_task.cancel()
        try:
            await ticker_task
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
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
    ],
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
# D-6.4-replay slice 5: read surface for the portfolio event log + replay primitive.
app.include_router(replay.router, prefix="/api/v1/replay", tags=["replay"])
app.include_router(system.router, prefix="/api/v1/system", tags=["system"])
app.include_router(live_router)


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "service": "flint"}


@app.get("/api/v1/capabilities")
def capabilities_root(request: Request):
    """Phase 4 T4.6 — alias for GET /api/v1/system/capabilities at root.

    UI + MCP clients probe this to feature-flag missing surface.
    """
    from .routes.system import get_capabilities
    return get_capabilities(request)


# ─── Serve built UI static files ───────────────────────────────
# If ui/dist exists (production build), serve it. All non-API routes
# fall through to index.html for client-side routing.
# NOTE: The SPA fallback is mounted LAST via middleware, not a catch-all
# route, to avoid stealing API routes from sub-routers.
if _UI_DIST.exists() and (_UI_DIST / "index.html").exists():
    # Serve static assets (JS, CSS, images)
    app.mount("/assets", StaticFiles(directory=str(_UI_DIST / "assets")), name="ui-assets")

    # Serve other static files at root (favicon, etc.)
    @app.get("/favicon.svg")
    async def favicon():
        fpath = _UI_DIST / "favicon.svg"
        if fpath.exists():
            return FileResponse(str(fpath))

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import Response as StarletteResponse

    class _SPAFallbackMiddleware(BaseHTTPMiddleware):
        """Serve index.html for non-API, non-asset GET requests that 404."""
        async def dispatch(self, request: StarletteRequest, call_next) -> StarletteResponse:
            response = await call_next(request)
            # Only intercept 404 GETs that aren't API/WS/asset routes
            if (
                response.status_code == 404
                and request.method == "GET"
                and not request.url.path.startswith("/api/")
                and not request.url.path.startswith("/ws")
                and not request.url.path.startswith("/assets/")
            ):
                # Check if it's a real file in dist (path traversal protected)
                try:
                    file_path = (_UI_DIST / request.url.path.lstrip("/")).resolve()
                    if file_path.is_file() and str(file_path).startswith(str(_UI_DIST.resolve())):
                        return FileResponse(str(file_path))
                except (ValueError, OSError):
                    pass
                return FileResponse(str(_UI_DIST / "index.html"))
            return response

    app.add_middleware(_SPAFallbackMiddleware)

    logger.info("Serving UI from %s", _UI_DIST)


async def _ws_loop(websocket: WebSocket, channel: str) -> None:
    """Shared receive-loop for any /ws endpoint. Pulls `?since=<seq>`
    from the query string for replay-on-reconnect."""
    manager = getattr(websocket.app.state, "ws_manager", None)
    if manager is None:
        await websocket.close()
        return
    since_raw = websocket.query_params.get("since")
    since: int | None = None
    if since_raw is not None:
        try:
            since = int(since_raw)
        except ValueError:
            since = None
    await manager.connect(websocket, channel, since_seq=since)
    try:
        while True:
            await websocket.receive_text()  # keep alive
    except WebSocketDisconnect:
        manager.disconnect(websocket, channel)


@app.websocket("/ws/paper/{session_id}")
async def websocket_paper(websocket: WebSocket, session_id: str):
    """D-4.3-websocket: per-session paper-trading stream.
    Channel name = `paper:{session_id}`. Server emits equity ticks +
    last-trade events here; client subscribes via useWebSocket."""
    await _ws_loop(websocket, f"paper:{session_id}")


@app.websocket("/ws/live/{session_id}")
async def websocket_live(websocket: WebSocket, session_id: str):
    """D-4.3-websocket: per-session live-trading stream.
    Channel name = `live:{session_id}`."""
    await _ws_loop(websocket, f"live:{session_id}")


@app.websocket("/ws/{channel}")
async def websocket_endpoint(websocket: WebSocket, channel: str = "all"):
    """Legacy generic-channel endpoint. New clients should use the
    paper/live-specific routes above."""
    await _ws_loop(websocket, channel)
