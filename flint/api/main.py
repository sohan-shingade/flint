"""Flint FastAPI application."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import backtest, strategies, data, mev, user_strategies

app = FastAPI(
    title="Flint",
    description="Algorithmic trading, backtesting, and MEV research platform for Solana",
    version="0.1.0",
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
app.include_router(data.router, prefix="/api/v1/data", tags=["data"])
app.include_router(mev.router, prefix="/api/v1/mev", tags=["mev"])
app.include_router(user_strategies.router, prefix="/api/v1/user-strategies", tags=["user-strategies"])


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "service": "flint"}
