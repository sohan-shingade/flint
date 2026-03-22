"""Backtest engine — feeds candles through a strategy and tracks performance.

v0.2: Uses ExecutionContext for order management. Supports pluggable fill/fee
models, stop-loss/take-profit, limit orders, funding payments, and multi-position.
Fully backwards compatible with v0.1 Signal-based strategies.
"""
from __future__ import annotations

import inspect
from typing import List, Optional

import numpy as np

from ..models import (
    BacktestResult,
    Candle,
    FundingRate,
    Position,
    Signal,
    Side,
)
from ..execution.backtest_context import BacktestContext
from ..execution.fee_models import FeeModel, FlatFeeModel
from ..execution.fill_models import FillModel, ClosePriceFill
from ..strategy.base import Strategy


def _strategy_uses_context(strategy: Strategy) -> bool:
    """Detect if a strategy's on_candle explicitly uses the ctx parameter.

    A strategy "uses context" if on_candle has a `ctx` parameter that
    is NOT defaulting to None in the method body (i.e., the strategy
    was written for v2). We check by looking at whether the concrete
    class overrides on_candle with a ctx parameter.
    """
    sig = inspect.signature(strategy.on_candle)
    params = list(sig.parameters.keys())
    # v1 strategies: on_candle(self, candle, history) — 2 params after self
    # v2 strategies: on_candle(self, candle, history, ctx=None) — 3 params
    # Both have ctx now, but we always pass it. The adapter handles v1 signal
    # conversion. So we just always pass ctx and handle Signal returns.
    return "ctx" in params


class BacktestEngine:
    """Event-driven backtest engine with pluggable execution models.

    Backwards compatible: v0.1 strategies returning Signal still work.
    v0.2 strategies can use ctx.market_order(), ctx.limit_order(), etc.
    """

    def __init__(
        self,
        strategy: Strategy,
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.0005,
        position_size: float = 1.0,
        fill_model: Optional[FillModel] = None,
        fee_model: Optional[FeeModel] = None,
        funding_rates: Optional[List[FundingRate]] = None,
        risk_manager=None,
    ) -> None:
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.position_size = position_size
        self._fill_model = fill_model or ClosePriceFill()
        self._fee_model = fee_model or FlatFeeModel(fee_bps=fee_rate * 10_000)
        self._funding_rates = funding_rates or []
        self._risk_manager = risk_manager

    def run(self, candles, extra_markets: dict = None) -> BacktestResult:
        """Run backtest.

        Args:
            candles: Primary market candles (List[Candle]) or Dict[str, List[Candle]] for multi-market.
            extra_markets: Optional dict of {market: List[Candle]} for cross-market data access.
        """
        # Normalize input: accept Dict[str, List[Candle]] or List[Candle]
        if isinstance(candles, dict):
            all_markets = candles
            # Use the first market as primary, or pick the one with most candles
            primary = max(all_markets.keys(), key=lambda k: len(all_markets[k]))
            candles = all_markets[primary]
            extra_markets = {k: v for k, v in all_markets.items() if k != primary}
        return self._run_internal(candles, extra_markets)

    def _run_internal(self, candles: List[Candle], extra_markets: dict = None) -> BacktestResult:
        if not candles:
            return BacktestResult(
                total_pnl=0, win_rate=0, max_drawdown=0, sharpe_ratio=0,
                total_trades=0, winning_trades=0, losing_trades=0,
            )

        self.strategy.reset()
        ctx = BacktestContext(
            initial_capital=self.initial_capital,
            fill_model=self._fill_model,
            fee_model=self._fee_model,
            position_size_pct=self.position_size,
            risk_manager=self._risk_manager,
        )

        has_ctx = _strategy_uses_context(self.strategy)
        equity_curve: List[float] = []
        history: List[Candle] = []

        # Set up multi-market data if available
        if extra_markets:
            all_histories = dict(extra_markets)
            if candles:
                all_histories[candles[0].market] = candles
            ctx.set_market_histories(all_histories)

        # Pre-index funding rates by timestamp for fast lookup
        funding_by_ts = {}
        for fr in self._funding_rates:
            funding_by_ts[fr.ts] = fr

        # Pre-sort extra market candles for efficient sync
        extra_sorted: dict = {}
        extra_cursors: dict = {}
        if extra_markets:
            for mkt, mkt_candles in extra_markets.items():
                extra_sorted[mkt] = sorted(mkt_candles, key=lambda c: c.ts)
                extra_cursors[mkt] = 0
        extra_histories: dict = {m: [] for m in (extra_markets or {})}

        for candle in candles:
            # Advance extra market histories up to current timestamp
            if extra_markets:
                for mkt in extra_markets:
                    sorted_candles = extra_sorted[mkt]
                    cursor = extra_cursors[mkt]
                    while cursor < len(sorted_candles) and sorted_candles[cursor].ts <= candle.ts:
                        extra_histories[mkt].append(sorted_candles[cursor])
                        cursor += 1
                    extra_cursors[mkt] = cursor
                # Update context with current histories
                combined = dict(extra_histories)
                combined[candle.market] = history + [candle]  # include current
                ctx.set_market_histories(combined)

            # 1. Set current candle on context
            ctx.set_candle(candle)

            # 2. Process pending stop/limit orders BEFORE strategy
            ctx.process_pending_orders(candle)

            # 3. Apply funding if there's a rate at this timestamp
            fr = funding_by_ts.get(candle.ts)
            if fr is not None:
                ctx.apply_funding(fr)

            # 4. Call strategy
            history.append(candle)
            if has_ctx:
                signal = self.strategy.on_candle(candle, history, ctx=ctx)
            else:
                signal = self.strategy.on_candle(candle, history)

            # 5. v1 Signal adapter — translate BUY/SELL to context orders
            if signal == Signal.BUY and not ctx.positions:
                size = (ctx.account.cash * self.position_size) / candle.close
                if size > 0:
                    ctx.market_order(candle.market, Side.LONG, size)
            elif signal == Signal.SELL and ctx.positions:
                for pos in ctx.positions:
                    if pos.market == candle.market:
                        ctx.close_position(candle.market)
                        break

            # 6. Process market orders placed this bar
            ctx.process_market_orders(candle)

            # 7. Record equity
            equity_curve.append(ctx.account.equity)

        # Force-close open positions at last candle
        if candles and ctx.positions:
            ctx.close_all_positions(candles[-1])
            equity_curve[-1] = ctx.account.equity

        res_s = candles[0].resolution_s if candles else 3600
        return self._build_result(ctx, equity_curve, res_s)

    def _build_result(self, ctx: BacktestContext, equity_curve: List[float], resolution_s: int = 3600) -> BacktestResult:
        """Build BacktestResult from context state."""
        trades = ctx.closed_trades
        positions = []
        for t in trades:
            pos = Position(
                entry_price=t["entry_price"],
                size=t["size"] if t["side"] == "long" else -t["size"],
                entry_ts=t["entry_ts"],
                exit_price=t["exit_price"],
                exit_ts=t["exit_ts"],
                pnl=t["pnl"],
                closed=True,
            )
            positions.append(pos)

        winners = [p for p in positions if p.pnl > 0]
        losers = [p for p in positions if p.pnl <= 0]
        total_trades = len(positions)
        win_rate = len(winners) / total_trades if total_trades else 0.0

        max_dd = _max_drawdown(equity_curve) if equity_curve else 0.0
        periods_per_year = 365.25 * 24 * 3600 / resolution_s if resolution_s > 0 else 8760
        sharpe = _sharpe_ratio(equity_curve, periods_per_year)

        return BacktestResult(
            total_pnl=ctx.account.equity - self.initial_capital,
            win_rate=win_rate,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            total_trades=total_trades,
            winning_trades=len(winners),
            losing_trades=len(losers),
            positions=positions,
            equity_curve=equity_curve,
            fills=ctx.all_fills,
            total_fees=ctx.total_fees,
            funding_paid=ctx.total_funding,
        )


def _max_drawdown(equity: List[float]) -> float:
    if not equity:
        return 0.0
    arr = np.array(equity)
    peak = np.maximum.accumulate(arr)
    dd = (peak - arr) / np.where(peak == 0, 1, peak)
    return float(np.max(dd))


def _sharpe_ratio(equity: List[float], periods_per_year: float = 8760) -> float:
    if len(equity) < 2:
        return 0.0
    arr = np.array(equity)
    returns = np.diff(arr) / arr[:-1]
    if np.std(returns) == 0:
        return 0.0
    return float(np.mean(returns) / np.std(returns) * np.sqrt(periods_per_year))
