"""Backtest CrossVenueFundingArb on historical data."""
import math
import time
from datetime import datetime

from flint.store import FlintStore
from flint.backtest.engine import BacktestEngine
from flint.strategy.funding_arb_v2 import CrossVenueFundingArb


def load_data(store, markets, resolution_s=3600, days=90):
    """Load candles and funding rates for the backtest period."""
    end_ts = int(time.time())
    start_ts = end_ts - days * 86400

    # Multi-market candles dict
    candles_dict = {}
    for market in markets:
        candles = store.query_candles(market, resolution_s, start_ts, end_ts, venue="pyth")
        if not candles:
            candles = store.query_candles(market, resolution_s, start_ts, end_ts)
        if candles:
            candles_dict[market] = candles
            print(f"  {market}: {len(candles)} candles")

    # All funding rates in the period
    funding = []
    for market in markets:
        rates = store.query_funding_rates(market, start_ts, end_ts)
        funding.extend(rates)
    print(f"  Funding rates: {len(funding)} total")

    return candles_dict, funding


def compute_metrics(result, days):
    """Compute strategy metrics from backtest result."""
    equity_curve = result.equity_curve if hasattr(result, 'equity_curve') else []
    trades = result.trades if hasattr(result, 'trades') else []

    total_return = result.total_return_pct if hasattr(result, 'total_return_pct') else 0
    max_dd = result.max_drawdown_pct if hasattr(result, 'max_drawdown_pct') else 0
    sharpe = result.sharpe_ratio if hasattr(result, 'sharpe_ratio') else 0
    n_trades = result.total_trades if hasattr(result, 'total_trades') else 0
    win_rate = result.win_rate if hasattr(result, 'win_rate') else 0
    final_equity = result.final_equity if hasattr(result, 'final_equity') else 0

    # Compute daily returns for our own Sharpe calculation
    if equity_curve and len(equity_curve) >= 2:
        # equity_curve is List[float] — one per candle (hourly)
        # Group into daily by taking every 24th value
        step = max(1, len(equity_curve) // days) if days > 0 else 24
        daily_values = equity_curve[::step]
        daily_equity = {i: v for i, v in enumerate(daily_values)}
        days_sorted = sorted(daily_equity.keys())

        days_sorted = sorted(daily_equity.keys())
        if len(days_sorted) >= 2:
            daily_returns = []
            for i in range(1, len(days_sorted)):
                prev = daily_equity[days_sorted[i - 1]]
                curr = daily_equity[days_sorted[i]]
                if prev > 0:
                    daily_returns.append((curr - prev) / prev)

            if daily_returns:
                avg_ret = sum(daily_returns) / len(daily_returns)
                var = sum((r - avg_ret) ** 2 for r in daily_returns) / len(daily_returns)
                std = math.sqrt(var) if var > 0 else 1e-10
                computed_sharpe = (avg_ret / std) * math.sqrt(365)
                print(f"  Computed Sharpe (daily): {computed_sharpe:.2f}")
                print(f"  Avg daily return: {avg_ret * 100:.4f}%")
                print(f"  Daily return std: {std * 100:.4f}%")

    return {
        "total_return_pct": total_return,
        "max_drawdown_pct": max_dd,
        "sharpe": sharpe,
        "n_trades": n_trades,
        "win_rate": win_rate,
    }


def run_backtest(
    markets=None,
    days=90,
    capital=10000,
    **strategy_kwargs,
):
    """Run the funding arb backtest and print results."""
    if markets is None:
        markets = ["SOL-PERP", "ETH-PERP", "BTC-PERP"]

    store = FlintStore("data/flint.duckdb")

    print(f"Loading data for {markets} ({days} days)...")
    candles_dict, funding = load_data(store, markets, days=days)
    store.close()

    if not candles_dict:
        print("No candle data found!")
        return

    strategy = CrossVenueFundingArb(**strategy_kwargs)
    print(f"\nStrategy: {strategy.name}")
    print(f"  min_spread_bps={strategy._min_spread_bps}")
    print(f"  z_score_entry={strategy._z_entry}")
    print(f"  max_hold_hours={strategy._max_hold_hours}")
    print(f"  fee_bps={strategy._fee_bps}")
    print(f"  max_positions={strategy._max_positions}")

    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=capital,
        funding_rates=funding,
    )

    print(f"\nRunning backtest with ${capital} capital...")
    t0 = time.time()
    result = engine.run(candles_dict)
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s")

    final_eq = capital + result.total_pnl
    total_return = (result.total_pnl / capital) * 100

    print(f"\n{'='*50}")
    print(f"RESULTS")
    print(f"{'='*50}")
    print(f"  Final equity:    ${final_eq:,.2f}")
    print(f"  Total PnL:       ${result.total_pnl:,.2f}")
    print(f"  Total return:    {total_return:.2f}%")
    print(f"  Max drawdown:    {result.max_drawdown:.2f}%")
    print(f"  Sharpe ratio:    {result.sharpe_ratio:.2f}")
    print(f"  Total trades:    {result.total_trades}")
    print(f"  Win rate:        {result.win_rate:.1f}%")
    print(f"  Total fees:      ${result.total_fees:,.2f}")
    print(f"  Funding income:  ${result.funding_paid:,.2f}")
    if result.per_venue_pnl:
        print(f"  Per-venue PnL:   {result.per_venue_pnl}")
    if result.per_venue_funding_income:
        print(f"  Per-venue fund:  {result.per_venue_funding_income}")

    metrics = compute_metrics(result, days)
    print(f"{'='*50}")

    return result, metrics


if __name__ == "__main__":
    run_backtest(
        markets=["SOL-PERP", "ETH-PERP", "BTC-PERP"],
        days=90,
        capital=10000,
    )
