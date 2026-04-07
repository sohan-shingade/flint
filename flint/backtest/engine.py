"""Backtest engine — feeds candles through a strategy and tracks performance.

v0.2: Uses ExecutionContext for order management. Supports pluggable fill/fee
models, stop-loss/take-profit, limit orders, funding payments, and multi-position.
Fully backwards compatible with v0.1 Signal-based strategies.
"""
from __future__ import annotations

import inspect
import time as _time
from typing import Dict, List, Optional

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
from ..execution.fill_models import FillModel, FillPipeline, OrderbookFillModel
from ..strategy.base import Strategy


def _parse_venue_market(key: str):
    """Parse a 'venue:market' composite key. Returns (venue, market)."""
    if ":" in key:
        venue, market = key.split(":", 1)
        return (venue, market)
    return ("default", key)


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
        orderbook_snapshots: Optional[list] = None,
        open_interest: Optional[list] = None,
        borrow_snapshots: Optional[list] = None,
        risk_manager=None,
        margin_engine=None,
        capital_allocator=None,
        max_runtime_s: float = 300,
        venue_fill_models: Optional[Dict[str, FillModel]] = None,
    ) -> None:
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.position_size = position_size
        self._fill_model = fill_model or FillPipeline()
        self._fee_model = fee_model or FlatFeeModel(fee_bps=fee_rate * 10_000)
        self._funding_rates = funding_rates or []
        self._orderbook_snapshots = orderbook_snapshots or []
        self._open_interest = open_interest or []
        self._borrow_snapshots = borrow_snapshots or []
        self._margin_engine = margin_engine
        self._capital_allocator = capital_allocator
        self._risk_manager = risk_manager
        self._max_runtime_s = max_runtime_s
        self._venue_fill_models: Dict[str, FillModel] = venue_fill_models or {}

    def run(self, candles, extra_markets: dict = None) -> BacktestResult:
        """Run backtest.

        Args:
            candles: Primary market candles (List[Candle]) or Dict[str, List[Candle]] for multi-market.
                     Dict keys may be plain market names ("SOL-PERP") or venue:market composite
                     keys ("drift:SOL-PERP", "hyperliquid:SOL-PERP").
            extra_markets: Optional dict of {market: List[Candle]} for cross-market data access.
        """
        # Normalize input: accept Dict[str, List[Candle]] or List[Candle]
        if isinstance(candles, dict):
            all_markets = candles
            # Parse venue:market composite keys — tag candles with venue
            parsed = {}
            for key, vals in all_markets.items():
                venue, market = _parse_venue_market(key)
                tagged = []
                for c in vals:
                    if c.venue == "default" and venue != "default":
                        tagged.append(Candle(
                            ts=c.ts, open=c.open, high=c.high, low=c.low,
                            close=c.close, volume=c.volume, market=market,
                            resolution_s=c.resolution_s, venue=venue,
                        ))
                    else:
                        tagged.append(c)
                # Use plain market as key (for BacktestContext compatibility)
                # When two venue keys map to the same market, keep the longer stream
                if market not in parsed or len(tagged) > len(parsed[market]):
                    parsed[market] = tagged
            primary = max(parsed.keys(), key=lambda k: len(parsed[k]))
            candles = parsed[primary]
            extra_markets = {k: v for k, v in parsed.items() if k != primary}
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
            margin_engine=self._margin_engine,
            capital_allocator=self._capital_allocator,
            venue_fill_models=self._venue_fill_models,
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
        # Also sort them for range-based lookup (catch rates between candles)
        funding_by_ts = {}
        for fr in self._funding_rates:
            funding_by_ts[fr.ts] = fr
        sorted_funding = sorted(self._funding_rates, key=lambda f: f.ts)
        funding_cursor = 0

        # Pre-sort orderbook snapshots for cursor-based feeding
        sorted_orderbooks = sorted(self._orderbook_snapshots, key=lambda s: s.ts)
        ob_cursor = 0

        # Pre-sort open interest for cursor-based feeding
        sorted_oi = sorted(self._open_interest, key=lambda o: o.ts)
        oi_cursor = 0

        # Pre-sort borrow snapshots (Jupiter Perps) for cursor-based feeding
        sorted_borrow = sorted(self._borrow_snapshots, key=lambda b: b.ts)
        borrow_cursor = 0

        # Pre-sort extra market candles for efficient sync
        extra_sorted: dict = {}
        extra_cursors: dict = {}
        if extra_markets:
            for mkt, mkt_candles in extra_markets.items():
                extra_sorted[mkt] = sorted(mkt_candles, key=lambda c: c.ts)
                extra_cursors[mkt] = 0
        extra_histories: dict = {m: [] for m in (extra_markets or {})}

        # Warn if all candle volumes are zero (Pyth price-only data)
        volume_warnings = []
        sample = candles[:min(100, len(candles))]
        if sample and all(c.volume == 0 for c in sample):
            volume_warnings.append(
                "All candles have zero volume (Pyth price-only data). "
                "Volume-dependent indicators (VWAP, volume breakout) will be unreliable."
            )

        _deadline = _time.monotonic() + self._max_runtime_s if self._max_runtime_s > 0 else float('inf')

        for _ci, candle in enumerate(candles):
            # Timeout check (every 100 candles to minimize overhead)
            if _ci % 100 == 0 and _time.monotonic() > _deadline:
                raise RuntimeError(
                    f"Backtest exceeded {self._max_runtime_s:.0f}s time limit "
                    f"({_ci:,}/{len(candles):,} candles processed)"
                )

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

            # 1b. Check liquidations BEFORE anything else
            ctx.check_liquidations(candle)

            # 1c. Update margin stats (leverage, utilization) for this bar
            if self._margin_engine is not None:
                self._margin_engine.update_stats(ctx.positions, ctx.account.cash)

            # 2. Process pending stop/limit orders BEFORE strategy
            ctx.process_pending_orders(candle)

            # 3. Apply all funding rates up to current candle timestamp
            while funding_cursor < len(sorted_funding) and sorted_funding[funding_cursor].ts <= candle.ts:
                fr = sorted_funding[funding_cursor]
                ctx.add_funding_rate(fr)  # make visible to strategy via ctx.get_funding_rate()
                ctx.apply_funding(fr)     # apply PnL impact to positions
                funding_cursor += 1

            # 3b. Feed orderbook snapshots up to current timestamp
            while ob_cursor < len(sorted_orderbooks) and sorted_orderbooks[ob_cursor].ts <= candle.ts:
                ctx.add_orderbook_snapshot(sorted_orderbooks[ob_cursor])
                ob_cursor += 1

            # 3b2. Feed open interest up to current timestamp
            while oi_cursor < len(sorted_oi) and sorted_oi[oi_cursor].ts <= candle.ts:
                ctx.add_open_interest(sorted_oi[oi_cursor])
                oi_cursor += 1

            # 3b3. Feed borrow snapshots (Jupiter Perps) up to current timestamp
            while borrow_cursor < len(sorted_borrow) and sorted_borrow[borrow_cursor].ts <= candle.ts:
                ctx.add_borrow_rate(sorted_borrow[borrow_cursor])
                borrow_cursor += 1

            # 3c. Process pending capital transfers
            ctx.process_transfers(candle.ts)

            # 3d. Update fill model's orderbook if it's book-aware
            if isinstance(self._fill_model, (OrderbookFillModel, FillPipeline)):
                book = ctx.get_orderbook(candle.market)
                self._fill_model.set_orderbook(book)

            # 3e. Feed orderbooks and candle buffers to venue-specific fill models
            for _vname, _vfm in self._venue_fill_models.items():
                if hasattr(_vfm, 'set_orderbook'):
                    _vfm.set_orderbook(ctx.get_orderbook(candle.market))
                if hasattr(_vfm, 'set_candle_buffer'):
                    _vfm.set_candle_buffer(candles[_ci:])

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
        return self._build_result(ctx, equity_curve, res_s, volume_warnings=volume_warnings)

    def _build_result(self, ctx: BacktestContext, equity_curve: List[float], resolution_s: int = 3600, volume_warnings: Optional[List[str]] = None) -> BacktestResult:
        """Build BacktestResult from context state."""
        volume_warnings = volume_warnings or []
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

        # Per-venue analytics
        per_venue_pnl = {}
        per_venue_trades_count = {}
        per_venue_funding = {}
        for trade in trades:
            venue = trade.get("venue", "default")
            pnl = trade.get("pnl", 0)
            per_venue_pnl[venue] = per_venue_pnl.get(venue, 0) + pnl
            per_venue_trades_count[venue] = per_venue_trades_count.get(venue, 0) + 1
            funding = trade.get("funding_paid", 0)
            per_venue_funding[venue] = per_venue_funding.get(venue, 0) + funding

        # Fallback: if no per-trade funding data, use ctx.total_funding as a single bucket
        if not per_venue_funding and ctx.total_funding:
            per_venue_funding["default"] = ctx.total_funding

        total_tx_costs = sum(f.tx_cost for f in ctx.all_fills)

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
            total_tx_costs=total_tx_costs,
            strategy_warnings=volume_warnings + [m for m in ctx.log_messages
                               if "WARNING" in m or "LIQUIDATED" in m or "MARGIN REJECTED" in m],
            per_venue_pnl=per_venue_pnl,
            per_venue_trades=per_venue_trades_count,
            per_venue_funding_income=per_venue_funding,
            jupiter_borrow_paid=ctx.total_borrow_paid,
            borrow_payments=ctx._borrow_payments,
            margin_stats=self._margin_engine.stats if self._margin_engine else None,
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
    std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    if std < 1e-12:
        return 0.0
    return float(np.mean(returns) / std * np.sqrt(periods_per_year))
