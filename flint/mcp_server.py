"""Flint MCP Server — exposes Flint's trading tools to AI models.

Run with: python -m flint.mcp_server
Or add to Claude Code: claude mcp add flint -- python -m flint.mcp_server

Provides tools for:
- Running backtests with built-in or custom strategies
- Querying market data (OHLCV, funding, OI)
- Downloading market data from Drift
- Listing available markets and strategies
- Running hyperparameter optimization
- Checking data freshness and provider status
"""
from __future__ import annotations

import json
import time
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP

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
        "platform for Solana. Use these tools to run backtests, query market data, "
        "download historical prices, optimize strategy parameters, and explore "
        "Drift Protocol markets. All data is free from Drift Protocol — no API keys needed."
    ),
)


# ═══════════════════════════════════════════════════════════════
# BACKTESTING TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
def run_backtest(
    market: str = "SOL-PERP",
    strategy: str = "ma_crossover",
    start_date: str = "2025-01-01",
    end_date: str = "2025-06-01",
    initial_capital: float = 10000.0,
    fee_rate: float = 0.0005,
    resolution_s: int = 3600,
    fast_period: int = 10,
    slow_period: int = 30,
    code: str = "",
) -> str:
    """Run a backtest on Solana market data. Returns PnL, Sharpe ratio, win rate, and trade details.

    Use built-in strategies (ma_crossover, ema_crossover, rsi, bollinger, momentum)
    or provide custom Python strategy code.

    Args:
        market: Market to backtest (e.g. SOL-PERP, BTC-PERP, ETH-PERP)
        strategy: Strategy name: ma_crossover, ema_crossover, rsi, bollinger, momentum
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD
        initial_capital: Starting capital in USD
        fee_rate: Trading fee rate (0.0005 = 5 bps)
        resolution_s: Candle resolution in seconds (3600=1h, 86400=1d)
        fast_period: Fast moving average period (for MA/EMA strategies)
        slow_period: Slow moving average period (for MA/EMA strategies)
        code: Custom strategy Python code (overrides strategy name if provided)
    """
    from datetime import datetime, timezone

    store = _get_store()

    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())

    # Load candles
    candles = store.query_candles(market, resolution_s, start_ts, end_ts)

    # Download if not available
    if not candles:
        try:
            from flint.providers.drift_candles import DriftCandleProvider
            provider = DriftCandleProvider()
            candles = provider.fetch_candles(market, resolution_s, start_ts, end_ts)
            provider.close()
            if candles:
                store.upsert_candles(candles)
        except Exception as e:
            return json.dumps({"error": f"Failed to download data for {market}: {e}"})

    if not candles:
        return json.dumps({"error": f"No data available for {market} from {start_date} to {end_date}"})

    # Build strategy
    params = {"fast_period": fast_period, "slow_period": slow_period}

    if code:
        from flint.strategy.loader import load_user_strategy
        strat = load_user_strategy(code, params)
    else:
        from flint.strategy.ma_crossover import MACrossoverStrategy
        from flint.strategy.ema_crossover import EMACrossoverStrategy
        from flint.strategy.rsi import RSIStrategy
        from flint.strategy.bollinger import BollingerStrategy
        from flint.strategy.momentum import MomentumStrategy

        builders = {
            "ma_crossover": lambda: MACrossoverStrategy(fast_period=fast_period, slow_period=slow_period),
            "ema_crossover": lambda: EMACrossoverStrategy(fast_period=fast_period, slow_period=slow_period),
            "rsi": lambda: RSIStrategy(),
            "bollinger": lambda: BollingerStrategy(),
            "momentum": lambda: MomentumStrategy(),
        }
        builder = builders.get(strategy)
        if not builder:
            return json.dumps({"error": f"Unknown strategy: {strategy}. Use: {list(builders.keys())}"})
        strat = builder()

    # Run backtest
    from flint.backtest.engine import BacktestEngine
    engine = BacktestEngine(strat, initial_capital, fee_rate)
    result = engine.run(candles)

    return json.dumps({
        "market": market,
        "strategy": strategy if not code else "custom",
        "period": f"{start_date} to {end_date}",
        "candles": len(candles),
        "total_pnl": round(result.total_pnl, 2),
        "total_return_pct": round(result.total_pnl / initial_capital * 100, 2),
        "sharpe_ratio": round(result.sharpe_ratio, 3),
        "max_drawdown_pct": round(result.max_drawdown * 100, 2),
        "total_trades": result.total_trades,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "win_rate_pct": round(result.win_rate * 100, 1),
        "total_fees": round(result.total_fees, 2),
        "params": params if not code else {},
    })


@mcp.tool()
def list_strategies() -> str:
    """List all built-in trading strategies with their parameters and descriptions."""
    strategies = [
        {
            "name": "ma_crossover",
            "description": "Moving Average Crossover — goes long when fast SMA crosses above slow SMA",
            "params": {"fast_period": {"type": "int", "default": 10, "range": "2-200"},
                       "slow_period": {"type": "int", "default": 30, "range": "5-500"}},
        },
        {
            "name": "ema_crossover",
            "description": "Exponential MA Crossover — faster reaction than SMA, same logic",
            "params": {"fast_period": {"type": "int", "default": 12}, "slow_period": {"type": "int", "default": 26}},
        },
        {
            "name": "rsi",
            "description": "RSI Mean Reversion — buys when RSI < oversold, sells when > overbought",
            "params": {"period": {"type": "int", "default": 14}, "oversold": {"default": 30}, "overbought": {"default": 70}},
        },
        {
            "name": "bollinger",
            "description": "Bollinger Bands — buys at lower band, sells at upper band",
            "params": {"period": {"type": "int", "default": 20}, "num_std": {"type": "float", "default": 2.0}},
        },
        {
            "name": "momentum",
            "description": "Momentum — buys if price is up X% over lookback period",
            "params": {"lookback": {"type": "int", "default": 24}, "threshold_pct": {"type": "float", "default": 5.0}},
        },
    ]
    return json.dumps({"strategies": strategies, "note": "You can also provide custom Python code via the 'code' parameter in run_backtest"})


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
) -> str:
    """Download historical market data from Drift Protocol and cache locally.

    Args:
        market: Market to download (e.g. SOL-PERP, BTC-PERP, WIF-PERP)
        days: Number of days of history to download (default 90)
        resolution_s: Candle resolution in seconds (default 3600 = 1 hour)
    """
    store = _get_store()

    end_ts = int(time.time())
    start_ts = end_ts - days * 86400

    # Check existing data and find gaps
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

    if not gaps:
        return json.dumps({
            "market": market, "days": days, "downloaded": 0, "cached": 0,
            "previously_existing": existing_count, "total": existing_count,
            "source": "local", "skipped": True,
            "note": "Data already cached — no download needed",
        })

    # Download only the missing gaps
    total_fetched = 0
    total_cached = 0
    source = "none"

    for gap_start, gap_end in gaps:
        fetched = _download_range_mcp(market, resolution_s, gap_start, gap_end)
        if fetched:
            total_fetched += len(fetched)
            total_cached += store.upsert_candles(fetched)
            source = "drift_api"

    final_count = len(store.query_candles(market, resolution_s, start_ts, end_ts))

    return json.dumps({
        "market": market, "days": days,
        "downloaded": total_fetched, "cached": total_cached,
        "previously_existing": existing_count, "total": final_count,
        "source": source,
    })


@mcp.tool()
def list_available_markets() -> str:
    """List all markets available for download — Drift perpetuals, Drift spot, and CoinGecko spot."""
    from flint.collector.tasks import MARKET_INDEX, SPOT_WITH_CANDLES

    perps = sorted(MARKET_INDEX.keys())
    drift_spots = sorted(SPOT_WITH_CANDLES)
    coingecko_spots = ["BTC", "ETH"]  # Not on Drift spot, sourced from CoinGecko

    all_spots = sorted(set(drift_spots) | set(coingecko_spots))

    return json.dumps({
        "perpetuals": perps,
        "perp_count": len(perps),
        "spot_drift": drift_spots,
        "spot_coingecko": coingecko_spots,
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

    # Query all market/resolution combos
    with store._lock:
        rows = store._conn.execute(
            "SELECT market, resolution_s, COUNT(*) as cnt, MIN(ts) as first_ts, MAX(ts) as last_ts "
            "FROM candles GROUP BY market, resolution_s ORDER BY market"
        ).fetchall()

    markets = []
    for r in rows:
        markets.append({
            "market": r[0],
            "resolution": f"{r[1]}s",
            "candle_count": r[2],
            "from": datetime.fromtimestamp(r[3], tz=timezone.utc).strftime("%Y-%m-%d"),
            "to": datetime.fromtimestamp(r[4], tz=timezone.utc).strftime("%Y-%m-%d"),
            "type": "perp" if "-PERP" in r[0] else "spot",
        })

    return json.dumps({"markets": markets, "total_markets": len(markets),
                        "total_candles": sum(m["candle_count"] for m in markets)})


@mcp.tool()
def get_funding_rates(
    market: str = "SOL-PERP",
    limit: int = 50,
) -> str:
    """Get funding rate history for a perpetual market.

    Args:
        market: Perp market (e.g. SOL-PERP, BTC-PERP)
        limit: Max records to return
    """
    from datetime import datetime, timezone

    store = _get_store()
    rates = store.query_funding_rates(market)

    if not rates:
        return json.dumps({"market": market, "rates": [], "count": 0})

    data = [{"ts": r.ts, "date": datetime.fromtimestamp(r.ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
             "rate": r.rate, "rate_bps": round(r.rate * 10000, 4),
             "oracle_price": r.oracle_price}
            for r in rates[-limit:]]

    avg_rate = sum(d["rate"] for d in data) / len(data) if data else 0

    return json.dumps({
        "market": market,
        "count": len(data),
        "avg_rate_bps": round(avg_rate * 10000, 4),
        "annualized_pct": round(avg_rate * 8760 * 100, 2),
        "rates": data,
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

    # Round for readability
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
        strategy: Strategy name (must have a parameters() method)
        start_date: Start date YYYY-MM-DD
        end_date: End date YYYY-MM-DD
        metric: Optimization metric: sharpe_ratio, total_pnl, win_rate, max_drawdown, sortino
        trials: Number of optimization trials (more = better but slower)
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

    # Get strategy class
    from flint.strategy.ma_crossover import MACrossoverStrategy
    from flint.strategy.ema_crossover import EMACrossoverStrategy
    from flint.strategy.rsi import RSIStrategy
    from flint.strategy.bollinger import BollingerStrategy
    from flint.strategy.momentum import MomentumStrategy

    strategy_classes = {
        "ma_crossover": MACrossoverStrategy,
        "ema_crossover": EMACrossoverStrategy,
        "rsi": RSIStrategy,
        "bollinger": BollingerStrategy,
        "momentum": MomentumStrategy,
    }

    cls = strategy_classes.get(strategy)
    if not cls:
        return json.dumps({"error": f"Unknown strategy: {strategy}"})

    optimizer = StrategyOptimizer(cls, candles, metric=metric, n_trials=trials)
    result = optimizer.run()

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
    """Flint platform overview and usage guide for AI models."""
    return """# Flint — Solana Trading Platform

Flint is a local-first algorithmic trading, backtesting, and MEV research platform for Solana.

## Typical Workflow
1. Download market data: `download_market_data(market="SOL-PERP", days=90)`
2. Run a backtest: `run_backtest(market="SOL-PERP", strategy="ma_crossover")`
3. Optimize parameters: `optimize_strategy(market="SOL-PERP", trials=50)`
4. Compare strategies by running multiple backtests with different strategies/params

## Available Markets
- 36 Drift perpetual markets (SOL-PERP, BTC-PERP, ETH-PERP, etc.)
- 17 Drift spot markets (SOL, JUP, DRIFT, WIF, etc.)
- Use `list_available_markets()` for the full list

## Strategy Tips
- MA Crossover works well for trending markets (SOL, BTC)
- RSI works better for range-bound markets
- Bollinger is good for mean-reversion
- Use fast_period < slow_period for MA strategies
- Higher resolution (3600=1h) gives more reliable signals than 60s

## Data
- All data is free from Drift Protocol (no API keys needed)
- Data is cached locally in DuckDB — subsequent runs are instant
- Candle resolutions: 60 (1m), 300 (5m), 3600 (1h), 86400 (1d)
"""


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
