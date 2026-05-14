"""Flint MCP Server — exposes Flint's trading tools to AI models.

Run with: python -m flint.mcp_server
Or add to Claude Code: claude mcp add flint -- python -m flint.mcp_server

Provides tools for:
- Running backtests with 20 built-in or custom strategies
- Paper trading (start, stop, monitor sessions)
- Querying market data (OHLCV, funding, OI, correlation)
- Downloading market data from Drift + 7 funding venues
- Hyperparameter optimization (Optuna) and walk-forward validation
- Journal: browse and compare past backtest runs
- Checking data freshness and provider status
"""
from __future__ import annotations

import json
import os
import time
import logging

from mcp.server.fastmcp import FastMCP


def _api_base() -> str:
    """D-4.7-mcp-inprocess (partial) — HTTP base URL for paper/journal tools.

    Full in-process service layer (flint/services/*.py called directly by
    MCP tools) is a larger refactor deferred as a sibling PR. This MVP at
    least makes the URL configurable so MCP can talk to any local server
    port, not just the hardcoded 8000. Set FLINT_API_URL to override.
    """
    return os.environ.get("FLINT_API_URL", "http://127.0.0.1:8000").rstrip("/")

logger = logging.getLogger("flint.mcp")

# ─── Initialize Flint components ──────────────────────────────

_store = None
_config = None


def _get_store():
    global _store
    if _store is None:
        from flint.config import load_config
        from flint.store import FlintStore
        global _config
        _config = load_config()
        _store = FlintStore(_config.db_path)
    return _store


def _get_config():
    global _config
    if _config is None:
        _get_store()
    return _config


# ─── Create MCP Server ───────────────────────────────────────

mcp = FastMCP(
    "Flint",
    instructions=(
        "Flint is a local-first algorithmic trading, backtesting, and MEV research "
        "platform for Solana. Use these tools to run backtests, manage paper trading "
        "sessions, query market data, download historical prices, optimize strategy "
        "parameters, and explore Drift/Hyperliquid/CEX markets. "
        "All data is free from Drift Protocol — no API keys needed for data."
    ),
)


# ═══════════════════════════════════════════════════════════════
# BACKTESTING TOOLS
# ═══════════════════════════════════════════════════════════════


_ALL_STRATEGIES = {
    "ma_crossover": {"category": "trend", "description": "SMA golden/death cross", "params": {"fast_period": 10, "slow_period": 30}},
    "ema_crossover": {"category": "trend", "description": "EMA crossover, more responsive", "params": {"fast_period": 12, "slow_period": 26}},
    "momentum": {"category": "trend", "description": "Rate-of-change over lookback", "params": {"lookback": 24, "threshold_pct": 5.0}},
    "breakout_momentum": {"category": "trend", "description": "N-period high breakout + volume spike", "params": {}},
    "momentum_breakout": {"category": "trend", "description": "Volume-confirmed breakout with ATR stops", "params": {}},
    "dual_timeframe": {"category": "trend", "description": "Long SMA trend + short momentum entry", "params": {}},
    "macd_divergence": {"category": "trend", "description": "MACD/signal line crossover", "params": {"fast": 12, "slow": 26, "signal": 9}},
    "atr_breakout": {"category": "trend", "description": "SMA ± N×ATR channel breakout", "params": {"period": 20, "atr_period": 14, "multiplier": 2.0}},
    "rsi": {"category": "mean-rev", "description": "Buy oversold RSI<30, sell overbought RSI>70", "params": {"period": 14, "oversold": 30, "overbought": 70}},
    "bollinger": {"category": "mean-rev", "description": "Buy lower band, sell upper band", "params": {"period": 20, "num_std": 2.0}},
    "mean_reversion": {"category": "mean-rev", "description": "Z-score deviation from rolling mean", "params": {"period": 20, "entry_z": 2.0}},
    "vwap_reversion": {"category": "mean-rev", "description": "Buy below VWAP, sell at reversion", "params": {"period": 20, "entry_pct": 2.0}},
    "rsi_macd_combo": {"category": "multi-signal", "description": "Only trades when RSI AND MACD agree", "params": {"rsi_period": 14, "macd_fast": 12, "macd_slow": 26}},
    "funding_harvest": {"category": "defi", "description": "Trade against funding rate direction", "params": {"entry_threshold": 0.001, "lookback": 8}},
    "funding_arb": {"category": "defi", "description": "Cross-venue funding rate arbitrage", "params": {"entry_threshold": 0.0005}},
    "funding_mean_reversion": {"category": "defi", "description": "Bollinger bands on funding rates", "params": {"period": 20}},
    "multi_venue_funding": {"category": "defi", "description": "Cross-venue funding arbitrage (7 venues)", "params": {"entry_threshold": 0.0005, "lookback": 12}},
    "basis_trade": {"category": "defi", "description": "Spot-futures basis capture", "params": {}},
    "grid_trader": {"category": "defi", "description": "Limit order grid for ranging markets", "params": {}},
    "mev_arb_monitor": {"category": "monitor", "description": "Monitors MEV arb opportunities (no trades)", "params": {}},
}


@mcp.tool()
def run_backtest(
    market: str = "SOL-PERP",
    strategy: str = "ma_crossover",
    start_date: str = "2025-01-01",
    end_date: str = "2025-06-01",
    initial_capital: float = 10000.0,
    fee_rate: float = 0.001,
    resolution_s: int = 3600,
    fast_period: int = 10,
    slow_period: int = 30,
    code: str = "",
) -> str:
    """Run a backtest on Solana market data. Returns PnL, Sharpe ratio, win rate, and trade details.

    Use built-in strategies or provide custom Python strategy code.
    20 built-in strategies: ma_crossover, ema_crossover, rsi, bollinger, momentum,
    rsi_macd_combo, funding_harvest, funding_arb, basis_trade, and more.

    Args:
        market: Market to backtest (e.g. SOL-PERP, BTC-PERP, ETH-PERP)
        strategy: Strategy name (use list_strategies() to see all 20)
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD
        initial_capital: Starting capital in USD
        fee_rate: Trading fee rate (0.001 = 10 bps, Drift taker default)
        resolution_s: Candle resolution in seconds (3600=1h, 86400=1d)
        fast_period: Fast moving average period (for MA/EMA strategies)
        slow_period: Slow moving average period (for MA/EMA strategies)
        code: Custom strategy Python code (overrides strategy name if provided)
    """
    from datetime import datetime, timezone
    from flint.services.backtest import BacktestRunRequest, run_backtest_sync

    store = _get_store()

    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())

    # Auto-fetch data if missing — service raises ValueError otherwise.
    if not store.query_candles(market, resolution_s, start_ts, end_ts):
        try:
            from flint.providers.drift_candles import DriftCandleProvider
            provider = DriftCandleProvider()
            fetched = provider.fetch_candles(market, resolution_s, start_ts, end_ts)
            provider.close()
            if fetched:
                store.upsert_candles(fetched)
        except Exception as e:
            return json.dumps({"error": f"Failed to download data for {market}: {e}"})

    params = {"fast_period": fast_period, "slow_period": slow_period}
    use_code = bool(code and code.strip())

    try:
        result_dict = run_backtest_sync(
            BacktestRunRequest(
                market=market,
                start_ts=start_ts,
                end_ts=end_ts,
                resolution_s=resolution_s,
                strategy=strategy,
                code=code if use_code else None,
                params=params,
                initial_capital=initial_capital,
                fee_rate=fee_rate,
            ),
            store,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})

    metrics = result_dict.get("metrics", {})
    return json.dumps({
        "market": market,
        "strategy": "custom" if use_code else strategy,
        "period": f"{start_date} to {end_date}",
        "total_pnl": round(metrics.get("total_pnl", 0), 2),
        "total_return_pct": round(metrics.get("total_return_pct", 0), 2),
        "sharpe_ratio": round(metrics.get("sharpe_ratio", 0), 3),
        "max_drawdown_pct": round(metrics.get("max_drawdown", 0) * 100, 2),
        "total_trades": metrics.get("total_trades", 0),
        "winning_trades": result_dict.get("winning_trades", 0),
        "losing_trades": result_dict.get("losing_trades", 0),
        "win_rate_pct": round(metrics.get("win_rate", 0) * 100, 1),
        "total_fees": round(result_dict.get("total_fees", 0), 2),
        "engine_used": result_dict.get("engine_used"),
        "params": {} if use_code else params,
    })


@mcp.tool()
def list_strategies() -> str:
    """List all 20 built-in trading strategies with their parameters and categories."""
    strategies = [{"name": k, **v} for k, v in _ALL_STRATEGIES.items()]
    return json.dumps({
        "strategies": strategies,
        "count": len(strategies),
        "categories": {"trend": 8, "mean-rev": 4, "multi-signal": 1, "defi": 6, "monitor": 1},
        "note": "All strategies support custom code via the 'code' parameter in run_backtest. "
                "Use optimize_strategy() to find best parameters.",
    })


# ═══════════════════════════════════════════════════════════════
# PAPER TRADING TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def start_paper_trading(
    market: str = "SOL-PERP",
    strategy: str = "rsi_macd_combo",
    initial_capital: float = 10000.0,
    code: str = "",
    max_drawdown_pct: float = 0.15,
) -> str:
    """Start a paper trading session. The strategy runs live on real market data with simulated fills.

    Paper trading replays recent history then goes live, polling for new candles.
    Monitor with get_paper_sessions() and get_paper_status().

    Args:
        market: Market to trade (e.g. SOL-PERP, BTC-PERP)
        strategy: Built-in strategy name (ignored if code is provided)
        initial_capital: Starting capital in USD
        code: Custom strategy Python code (overrides strategy name)
        max_drawdown_pct: Kill switch — stop trading if drawdown exceeds this (default 15%)
    """
    import requests
    try:
        body = {"market": market, "initial_capital": initial_capital, "resolution_s": 3600, "venue": "drift",
                "risk_config": {"max_drawdown_pct": max_drawdown_pct}}
        if code and code.strip():
            body["code"] = code
        else:
            body["strategy"] = strategy
        r = requests.post(f"{_api_base()}/api/v1/paper/start", json=body, timeout=120)
        return r.text
    except Exception as e:
        return json.dumps({"error": f"Failed to start paper trading: {e}. Is the Flint server running (flint serve)?"})


@mcp.tool()
def stop_paper_trading(session_id: str) -> str:
    """Stop a paper trading session gracefully. Closes positions and saves state.

    Args:
        session_id: The session ID from start_paper_trading or get_paper_sessions
    """
    import requests
    try:
        r = requests.post(f"{_api_base()}/api/v1/paper/stop",
                          json={"session_id": session_id}, timeout=30)
        return r.text
    except Exception as e:
        return json.dumps({"error": f"Failed to stop session: {e}"})


@mcp.tool()
def get_paper_sessions() -> str:
    """List all paper trading sessions with their current status, equity, and trade count."""
    import requests
    sessions: list = []
    try:
        r = requests.get(f"{_api_base()}/api/v1/paper/sessions", timeout=15)
        sessions = r.json().get("sessions", [])
    except Exception:
        # No live server — fall back to persisted store view (no in-memory PnL)
        from flint.services import paper as paper_service
        try:
            sessions = paper_service.list_sessions_from_store(_get_store())
        except Exception as e:
            return json.dumps({"error": f"Failed to list sessions: {e}"})

    summary = []
    for s in sessions:
        summary.append({
            "session_id": s["session_id"],
            "strategy": s.get("strategy", s.get("strategy_name", "?")),
            "market": s.get("market", "?"),
            "status": s.get("status", "?"),
            "equity": round(s.get("equity", 0), 2),
            "pnl": round(s.get("realized_pnl", 0) + s.get("unrealized_pnl", 0), 2),
            "trades": s.get("total_trades", 0),
        })
    return json.dumps({"sessions": summary, "count": len(summary)})


@mcp.tool()
def get_paper_status(session_id: str) -> str:
    """Get detailed status of a paper trading session including positions and equity history.

    Args:
        session_id: The session ID to query
    """
    import requests
    try:
        r = requests.get(f"{_api_base()}/api/v1/paper/status/{session_id}", timeout=15)
        return r.text
    except Exception as e:
        return json.dumps({"error": f"Failed to get session status: {e}"})


# ═══════════════════════════════════════════════════════════════
# JOURNAL TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def list_journal_runs(limit: int = 20) -> str:
    """List past backtest runs saved in the journal. Shows strategy, market, PnL, Sharpe, and more.

    Args:
        limit: Maximum number of runs to return (default 20, most recent first)
    """
    from flint.services import journal as journal_service
    try:
        runs = journal_service.list_runs(_get_store(), limit)
        summary = []
        for run in runs[:limit]:
            summary.append({
                "run_id": run.get("run_id", ""),
                "strategy": run.get("strategy_name", ""),
                "market": run.get("market", ""),
                "pnl": round(run.get("total_pnl", 0), 2),
                "return_pct": round(run.get("total_return_pct", 0), 1),
                "sharpe": round(run.get("sharpe_ratio", 0), 3),
                "max_dd": round(run.get("max_drawdown", 0) * 100, 1),
                "trades": run.get("total_trades", 0),
                "win_rate": round(run.get("win_rate", 0) * 100, 0),
            })
        return json.dumps({"runs": summary, "count": len(summary)})
    except Exception as e:
        return json.dumps({"error": f"Failed to list journal: {e}"})


@mcp.tool()
def compare_runs(run_ids: str) -> str:
    """Compare multiple backtest runs side-by-side. Shows metrics diff.

    Args:
        run_ids: Comma-separated run IDs from list_journal_runs (e.g. "abc123,def456")
    """
    from flint.services import journal as journal_service
    try:
        ids = [i.strip() for i in run_ids.split(",") if i.strip()]
        comparisons = journal_service.compare_runs(_get_store(), ids)
        return json.dumps({"comparisons": comparisons})
    except Exception as e:
        return json.dumps({"error": f"Failed to compare runs: {e}"})


# ═══════════════════════════════════════════════════════════════
# REPLAY TOOLS  (D-6.4-replay slice 5)
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def replay_summary(session_id: str) -> str:
    """Show replay-log summary for a backtest or paper session: total
    event count, latest seq, snapshot count.

    Args:
        session_id: Session id from a backtest run or paper trading session.
    """
    from flint.portfolio.event_log import EventLogReader
    from flint.portfolio.snapshots import SnapshotStore
    try:
        store = _get_store()
        reader = EventLogReader(store)
        snaps = SnapshotStore(store)
        return json.dumps({
            "session_id": session_id,
            "event_count": reader.count(session_id),
            "latest_seq": reader.latest_seq(session_id),
            "snapshot_count": snaps.count(session_id),
        })
    except Exception as e:
        return json.dumps({"error": f"replay_summary failed: {e}"})


@mcp.tool()
def replay_state(
    session_id: str,
    target_ts: int,
    initial_capital: float = 10_000.0,
) -> str:
    """Replay-fold the portfolio event log up to `target_ts` and
    return the BookState — cash, positions, realized PnL, fees,
    funding, borrow, fill counts. Useful for time-travel debugging.

    Args:
        session_id: Session id to replay.
        target_ts: Epoch seconds; events with `ts <= target_ts` are
            folded into the returned state.
        initial_capital: Seed capital for the fold (defaults to $10k).
    """
    from flint.portfolio.replay import replay
    try:
        state = replay(_get_store(), session_id,
                       target_ts=int(target_ts),
                       initial_capital=float(initial_capital))
        positions = {
            f"{venue}|{market}": {
                "market": pos.market, "venue": pos.venue,
                "side": pos.side, "size": pos.size,
                "entry_price": pos.entry_price,
            }
            for (venue, market), pos in state.positions.items()
        }
        return json.dumps({
            "session_id": session_id,
            "target_ts": int(target_ts),
            "cash": round(state.cash, 4),
            "realized_pnl": round(state.realized_pnl, 4),
            "total_fees": round(state.total_fees, 4),
            "total_funding": round(state.total_funding, 4),
            "total_borrow_paid": round(state.total_borrow_paid, 4),
            "fill_count": state.fill_count,
            "liquidation_count": state.liquidation_count,
            "positions": positions,
        })
    except Exception as e:
        return json.dumps({"error": f"replay_state failed: {e}"})


@mcp.tool()
def list_replay_events(
    session_id: str,
    since: int = -1,
    limit: int = 50,
) -> str:
    """Page through portfolio events for a session in seq order.

    Args:
        session_id: Session id to read.
        since: Return events with seq > since. -1 (default) reads from
            the start.
        limit: Max events to return (default 50, capped at 5000).
    """
    from flint.portfolio.event_log import EventLogReader
    try:
        reader = EventLogReader(_get_store())
        if since >= 0:
            events = reader.read_after_seq(session_id, after_seq=since, target_ts=2**62)
        else:
            events = reader.read_all(session_id)
        capped = min(max(1, limit), 5000)
        page = events[:capped]
        return json.dumps({
            "session_id": session_id,
            "since": since if since >= 0 else None,
            "count": len(page),
            "has_more": len(events) > capped,
            "events": [
                {"seq": e.seq, "ts": e.ts, "kind": e.kind, "payload": e.payload}
                for e in page
            ],
        })
    except Exception as e:
        return json.dumps({"error": f"list_replay_events failed: {e}"})


# ═══════════════════════════════════════════════════════════════
# DATA TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def get_candles(
    market: str = "SOL-PERP",
    resolution_s: int = 3600,
    start_date: str = "",
    end_date: str = "",
    limit: int = 100,
) -> str:
    """Get OHLCV candle data for a market. Returns price and volume history.

    Args:
        market: Market symbol (e.g. SOL-PERP, BTC-PERP, WIF-PERP, SOL, JUP)
        resolution_s: Candle width: 60 (1m), 300 (5m), 3600 (1h), 86400 (1d)
        start_date: Start date YYYY-MM-DD (optional, defaults to recent)
        end_date: End date YYYY-MM-DD (optional, defaults to now)
        limit: Max candles to return (default 100)
    """
    from datetime import datetime, timezone

    store = _get_store()

    start_ts = None
    end_ts = None
    if start_date:
        start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    if end_date:
        end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())

    candles = store.query_candles(market, resolution_s, start_ts, end_ts, limit=limit)

    if not candles:
        return json.dumps({"market": market, "candles": [], "count": 0,
                           "hint": "No local data. Use download_market_data to fetch from Drift."})

    data = [{"ts": c.ts, "date": datetime.fromtimestamp(c.ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
             "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": round(c.volume, 2)}
            for c in candles[-limit:]]

    return json.dumps({
        "market": market,
        "resolution": f"{resolution_s}s",
        "count": len(data),
        "first": data[0]["date"] if data else None,
        "last": data[-1]["date"] if data else None,
        "current_price": data[-1]["close"] if data else None,
        "candles": data,
    })


@mcp.tool()
def download_market_data(
    market: str = "SOL-PERP",
    days: int = 90,
    resolution_s: int = 3600,
    funding_venues: str = "drift,hyperliquid,okx,bybit,dydx,gateio,bitget",
) -> str:
    """Download historical market data from Drift and funding rates from multiple venues.

    For perp markets, also downloads funding rates from 7 venues:
    Drift, Hyperliquid, OKX, Bybit, dYdX, Gate.io, Bitget.
    All 8h funding rates are forward-filled to hourly resolution.

    Args:
        market: Market to download (e.g. SOL-PERP, BTC-PERP, WIF-PERP)
        days: Number of days of history to download (default 90)
        resolution_s: Candle resolution in seconds (default 3600 = 1 hour)
        funding_venues: Comma-separated venue names for funding rates
    """
    store = _get_store()

    end_ts = int(time.time())
    start_ts = end_ts - days * 86400

    # Check if range is already well-covered (skip redundant downloads)
    if store.is_range_synced(market, resolution_s, start_ts, end_ts):
        existing = store.query_candles(market, resolution_s, start_ts, end_ts)
        return json.dumps({
            "market": market, "days": days, "downloaded": 0, "cached": 0,
            "previously_existing": len(existing), "total": len(existing),
            "funding_fetched": 0, "funding_venues": [],
            "source": "local", "skipped": True,
            "note": "Data already covers this range. Use force=True to re-download.",
        })

    existing = store.query_candles(market, resolution_s, start_ts, end_ts)
    existing_count = len(existing)

    gaps = []
    if not existing:
        gaps.append((start_ts, end_ts))
    else:
        first_ts = existing[0].ts
        last_ts = existing[-1].ts
        if first_ts > start_ts + resolution_s:
            gaps.append((start_ts, first_ts))
        if last_ts < end_ts - resolution_s:
            gaps.append((last_ts, end_ts))

    total_fetched = 0
    total_cached = 0
    source = "local"

    for gap_start, gap_end in gaps:
        fetched = _download_range_mcp(market, resolution_s, gap_start, gap_end)
        if fetched:
            total_fetched += len(fetched)
            total_cached += store.upsert_candles(fetched)
            source = "drift_api"

    final_count = len(store.query_candles(market, resolution_s, start_ts, end_ts))

    funding_fetched = 0
    if "-PERP" in market:
        venue_list = [v.strip() for v in funding_venues.split(",") if v.strip()]
        try:
            from flint.services.data import download_funding_all_venues
            funding_fetched = download_funding_all_venues(
                store, market, start_ts, end_ts, logger, venues=venue_list
            )
        except Exception as e:
            logger.warning("Funding download failed for %s: %s", market, e)

    return json.dumps({
        "market": market, "days": days,
        "downloaded": total_fetched, "cached": total_cached,
        "previously_existing": existing_count, "total": final_count,
        "funding_fetched": funding_fetched,
        "funding_venues": venue_list if "-PERP" in market else [],
        "source": source,
    })


@mcp.tool()
def list_available_markets() -> str:
    """List all markets available for download — Drift perpetuals, Drift spot, and CoinGecko spot."""
    from flint.collector.tasks import MARKET_INDEX, SPOT_WITH_CANDLES

    perps = sorted(MARKET_INDEX.keys())
    drift_spots = sorted(SPOT_WITH_CANDLES)
    coingecko_spots = ["BTC", "ETH"]

    all_spots = sorted(set(drift_spots) | set(coingecko_spots))

    return json.dumps({
        "perpetuals": perps,
        "perp_count": len(perps),
        "spot_all": all_spots,
        "spot_count": len(all_spots),
        "total": len(perps) + len(all_spots),
        "note": "All data is free. Drift for perps + most spot. CoinGecko for BTC/ETH spot.",
    })


@mcp.tool()
def list_local_markets() -> str:
    """Show what market data is cached in the local database."""
    from datetime import datetime, timezone

    store = _get_store()

    rows = store.list_markets_with_data()

    markets = []
    for r in rows:
        markets.append({
            "market": r["market"],
            "resolution": f"{r['resolution_s']}s",
            "candle_count": r["candle_count"],
            "from": datetime.fromtimestamp(r["first_ts"], tz=timezone.utc).strftime("%Y-%m-%d"),
            "to": datetime.fromtimestamp(r["last_ts"], tz=timezone.utc).strftime("%Y-%m-%d"),
            "type": "perp" if "-PERP" in r["market"] else "spot",
        })

    return json.dumps({"markets": markets, "total_markets": len(markets),
                        "total_candles": sum(m["candle_count"] for m in markets)})


@mcp.tool()
def get_funding_rates(
    market: str = "SOL-PERP",
    venue: str = "",
    limit: int = 50,
) -> str:
    """Get funding rate history for a perpetual market, grouped by venue.

    Flint stores funding from 7 venues: drift, hyperliquid, okx, bybit, dydx, gateio, bitget.

    Args:
        market: Perp market (e.g. SOL-PERP, BTC-PERP)
        venue: Filter to a specific venue (optional, empty = all venues)
        limit: Max records per venue to return
    """
    from datetime import datetime, timezone

    store = _get_store()
    by_venue = store.query_funding_by_venue(market)

    if not by_venue:
        return json.dumps({"market": market, "venues": {}, "count": 0,
                           "hint": "No funding data. Use download_market_data to fetch."})

    if venue:
        by_venue = {v: rates for v, rates in by_venue.items() if v == venue}

    result_venues = {}
    total = 0
    for v, rates in by_venue.items():
        recent = rates[-limit:]
        avg = sum(r["rate"] for r in recent) / len(recent) if recent else 0
        result_venues[v] = {
            "count": len(rates),
            "avg_rate_bps": round(avg * 10000, 4),
            "annualized_pct": round(avg * 8760 * 100, 2),
            "latest": recent[-1] if recent else None,
            "recent_rates": [
                {"ts": r["ts"], "date": datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                 "rate_bps": round(r["rate"] * 10000, 4)}
                for r in recent[-10:]
            ],
        }
        total += len(rates)

    return json.dumps({
        "market": market,
        "venues": list(result_venues.keys()),
        "total_points": total,
        "venue_data": result_venues,
    })


@mcp.tool()
def get_open_interest(market: str = "SOL-PERP") -> str:
    """Get open interest data for a Drift perpetual market.

    Args:
        market: Perp market (e.g. SOL-PERP)
    """
    store = _get_store()
    records = store.query_open_interest(market)

    if not records:
        return json.dumps({"market": market, "data": [], "note": "No OI data. Run the collector first."})

    latest = records[-1]
    return json.dumps({
        "market": market,
        "latest_long_oi": latest.long_oi,
        "latest_short_oi": latest.short_oi,
        "net_oi": latest.net_oi,
        "total_oi": latest.total_oi,
        "records": len(records),
    })


@mcp.tool()
def get_correlation(
    markets: str = "SOL-PERP,BTC-PERP,ETH-PERP",
    resolution_s: int = 3600,
) -> str:
    """Compute cross-market correlation matrix from historical returns.

    Args:
        markets: Comma-separated market symbols
        resolution_s: Candle resolution (default 3600 = 1h)
    """
    from flint.analytics.correlation import compute_correlation_matrix

    store = _get_store()
    market_list = [m.strip() for m in markets.split(",")]

    candles_by_market = {}
    for m in market_list:
        c = store.query_candles(m, resolution_s)
        if c:
            candles_by_market[m] = c

    if len(candles_by_market) < 2:
        return json.dumps({"error": "Need at least 2 markets with data", "available": list(candles_by_market.keys())})

    matrix = compute_correlation_matrix(candles_by_market)
    rounded = {m1: {m2: round(v, 3) for m2, v in row.items()} for m1, row in matrix.items()}

    return json.dumps({"markets": list(candles_by_market.keys()), "correlation_matrix": rounded})


@mcp.tool()
def get_data_freshness() -> str:
    """Check how fresh the data is across all providers and markets."""
    store = _get_store()
    freshness = store.get_data_freshness()
    return json.dumps({"freshness": freshness[:50], "total_tracked": len(freshness)})


# ═══════════════════════════════════════════════════════════════
# OPTIMIZATION TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def optimize_strategy(
    market: str = "SOL-PERP",
    strategy: str = "ma_crossover",
    start_date: str = "2025-01-01",
    end_date: str = "2025-06-01",
    metric: str = "sharpe_ratio",
    trials: int = 30,
    resolution_s: int = 3600,
) -> str:
    """Run hyperparameter optimization using Optuna to find the best strategy parameters.

    Args:
        market: Market to optimize on
        strategy: Strategy name (must have a parameters() classmethod)
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD
        metric: Optimization metric: sharpe_ratio, total_pnl, win_rate, max_drawdown, sortino
        trials: Number of optimization trials (more = better but slower, recommend 30-100)
        resolution_s: Candle resolution (default 3600 = 1h)
    """
    from datetime import datetime, timezone
    from flint.optimization.optimizer import StrategyOptimizer

    store = _get_store()

    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())

    candles = store.query_candles(market, resolution_s, start_ts, end_ts)
    if not candles:
        return json.dumps({"error": f"No data for {market}. Use download_market_data first."})

    import flint.strategy as strats
    strategy_classes = {
        "ma_crossover": strats.MACrossoverStrategy,
        "ema_crossover": strats.EMACrossoverStrategy,
        "rsi": strats.RSIStrategy,
        "bollinger": strats.BollingerStrategy,
        "momentum": strats.MomentumStrategy,
        "funding_harvest": strats.FundingHarvestStrategy,
        "mean_reversion": strats.MeanReversionStrategy,
        "breakout_momentum": strats.BreakoutMomentumStrategy,
        "grid_trader": strats.GridTraderStrategy,
        "dual_timeframe": strats.DualTimeframeStrategy,
        "vwap_reversion": strats.VWAPReversionStrategy,
        "macd_divergence": strats.MACDDivergenceStrategy,
        "atr_breakout": strats.ATRBreakoutStrategy,
        "multi_venue_funding": strats.MultiVenueFundingStrategy,
        "rsi_macd_combo": strats.RSIMACDComboStrategy,
        "funding_arb": strats.FundingArbStrategy,
        "momentum_breakout": strats.MomentumBreakoutStrategy,
        "funding_mean_reversion": strats.FundingMeanReversionStrategy,
        "basis_trade": strats.BasisTradeStrategy,
    }

    cls = strategy_classes.get(strategy)
    if not cls:
        return json.dumps({"error": f"Unknown strategy: {strategy}. Use list_strategies() to see all 20."})

    optimizer = StrategyOptimizer(cls, candles, metric=metric, n_trials=trials)
    result = optimizer.optimize()

    return json.dumps({
        "market": market,
        "strategy": strategy,
        "metric": metric,
        "trials": trials,
        "best_params": result.best_params,
        "best_score": round(result.best_value, 4),
        "top_trials": [
            {"params": t.params, "score": round(t.values[0], 4) if t.values else None}
            for t in sorted(result.study.best_trials, key=lambda t: t.values[0] if t.values else 0, reverse=True)[:5]
        ] if hasattr(result, "study") else [],
    })


# ═══════════════════════════════════════════════════════════════
# RESOURCES (context for AI models)
# ═══════════════════════════════════════════════════════════════


@mcp.resource("flint://guide")
def flint_guide() -> str:
    """Flint platform overview and usage guide for AI models.
    Reads from docs/guides/quickstart.md (single source of truth).
    """
    import pathlib
    guide_path = pathlib.Path(__file__).parent.parent / "docs" / "guides" / "quickstart.md"
    if guide_path.exists():
        return guide_path.read_text()
    return (
        "# Flint\n\nUse list_strategies() to see 20 built-in strategies.\n"
        "Workflow: download_market_data -> run_backtest -> optimize_strategy -> start_paper_trading\n"
    )


@mcp.resource("flint://markets")
def flint_markets() -> str:
    """Current list of available markets with types."""
    return list_available_markets()


def _download_range_mcp(market: str, resolution_s: int, start_ts: int, end_ts: int) -> list:
    """Try all providers for a specific time range."""
    try:
        from flint.providers.drift_candles import DriftCandleProvider
        p = DriftCandleProvider()
        fetched = p.fetch_candles(market, resolution_s, start_ts, end_ts)
        p.close()
        if fetched:
            return fetched
    except Exception:
        pass
    try:
        from flint.providers.drift_s3 import DriftS3Provider
        p = DriftS3Provider()
        fetched = p.fetch_candles(market, resolution_s, start_ts, end_ts)
        p.close()
        if fetched:
            return fetched
    except Exception:
        pass
    try:
        from flint.providers.coingecko import CoinGeckoProvider
        cg = CoinGeckoProvider()
        if cg.resolve_id(market):
            fetched = cg.fetch_candles(market, resolution_s, start_ts, end_ts)
            cg.close()
            if fetched:
                return fetched
    except Exception:
        pass
    return []


# ─── Entry point ──────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
