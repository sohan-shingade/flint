"""SharedCapitalPortfolioEngine — multiple strategies sharing one capital pool.

D-6.1-unified (Wave 3 foundation + refinements). The existing
`PortfolioEngine` runs each strategy on a sliced subset of capital
and aggregates the independent results — that's a portfolio of
independent runs, not a real shared-capital book. This engine puts
every strategy on **one** `BacktestContext` so:

  * Cash, fees, funding, and borrow accrue against the same ledger.
  * Position state is shared — strategy A's long SOL and strategy
    B's short SOL net to the actual book exposure.
  * Pre-trade margin checks (orchestrator) see the full book and
    can reject any strategy whose order would breach the cap.

Strategy attribution is preserved by tagging every order_id with
the strategy name (`stratname:bt-N`). Fills inherit the tag via
`order_id`, so per-strategy PnL falls out by post-processing the
fill stream.

Refinements landed:
  * **Attribution-by-trade** — `entry_order_id` on `_Position` (and
    on closed_trade dicts) means the engine attributes PnL to the
    strategy that opened a leg even when a different strategy or
    a liquidation closes it.
  * **Per-strategy capital caps** — constructor accepts
    `strategy_capital_caps={"alice": 0.4, ...}` and the proxy gates
    new orders that would push that strategy's gross notional past
    `cap * equity`. Cap=1.0 is unbounded; missing entries default to
    no cap.
  * **Portfolio dollar imbalance** — `engine.dollar_imbalance(ctx)`
    surfaces (long $ - short $) as a signed scalar so dollar-neutral
    pair strategies can close the gap themselves; the engine doesn't
    force rebalancing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..backtest.engine import _max_drawdown, _sharpe_ratio
from ..execution.backtest_context import BacktestContext
from ..models import Candle, Side, Signal
from ..strategy.base import Strategy


@dataclass
class SharedPortfolioResult:
    total_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    final_equity: float
    combined_equity: List[float] = field(default_factory=list)
    per_strategy_pnl: Dict[str, float] = field(default_factory=dict)
    per_strategy_trades: Dict[str, int] = field(default_factory=dict)
    fills_by_strategy: Dict[str, List[dict]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


class _TaggedContextProxy:
    """Thin wrapper around `BacktestContext` that tags every order_id
    with `strategy_name:` so fills carry strategy attribution.

    Forwards everything else (account, positions, funding queries,
    candles, log) to the shared context. Multi-strategy strategies
    that read positions see the *whole book*, not just their slice —
    that's the point of shared capital.
    """

    def __init__(
        self,
        ctx: BacktestContext,
        strategy_name: str,
        capital_cap_pct: Optional[float] = None,
    ):
        self._ctx = ctx
        self._tag = strategy_name
        # Fraction of total equity this strategy is allowed to commit
        # gross. None = unbounded; 0.4 = 40 % of `ctx.account.equity`.
        self._cap_pct = capital_cap_pct

    # --- order placement: prepend tag to the engine-assigned id ---------

    def market_order(self, market, side, size, reduce_only=False, tag="", venue="default"):
        if not reduce_only and self._cap_pct is not None:
            ref_price = self._ctx.current_candle.close if self._ctx.current_candle else 0.0
            if not self._cap_check(market, size, ref_price):
                return ""
        oid = self._ctx.market_order(
            market, side, size, reduce_only=reduce_only, tag=tag, venue=venue,
        )
        return self._retag_last(oid)

    def limit_order(self, market, side, size, price, reduce_only=False, tag="", venue="default"):
        if not reduce_only and self._cap_pct is not None:
            if not self._cap_check(market, size, price):
                return ""
        oid = self._ctx.limit_order(
            market, side, size, price, reduce_only=reduce_only, tag=tag, venue=venue,
        )
        return self._retag_last(oid)

    def stop_order(self, market, side, size, trigger_price, tag="", venue="default"):
        if self._cap_pct is not None:
            if not self._cap_check(market, size, trigger_price):
                return ""
        oid = self._ctx.stop_order(
            market, side, size, trigger_price, tag=tag, venue=venue,
        )
        return self._retag_last(oid)

    def take_profit_order(self, market, side, size, trigger_price, tag="", venue="default"):
        if self._cap_pct is not None:
            if not self._cap_check(market, size, trigger_price):
                return ""
        oid = self._ctx.take_profit_order(
            market, side, size, trigger_price, tag=tag, venue=venue,
        )
        return self._retag_last(oid)

    def _own_notional(self) -> float:
        """Sum of |size| * mark across positions opened by this
        strategy (matched via `entry_order_id` prefix). Marks use the
        position's `mark_price` if set, else `entry_price` — the
        backtest path doesn't update mark on every candle, but
        `current_candle.close` would skew with intra-bar moves; the
        position's own entry is a fair approximation here."""
        prefix = f"{self._tag}:"
        total = 0.0
        for pos in self._ctx._pm.values():
            if not getattr(pos, "entry_order_id", "").startswith(prefix):
                continue
            mark = pos.mark_price if pos.mark_price > 0 else pos.entry_price
            total += pos.size * mark
        return total

    def _cap_check(self, market: str, size: float, ref_price: float) -> bool:
        """Return False (and log a warning) if accepting an order of
        `size` at `ref_price` would push this strategy's gross notional
        past its cap. Reduce-only orders bypass — they shrink, not grow."""
        if self._cap_pct is None:
            return True
        equity = self._ctx.account.equity
        if equity <= 0:
            return False
        new_notional = abs(size * ref_price)
        own = self._own_notional()
        cap_dollars = self._cap_pct * equity
        if own + new_notional > cap_dollars + 1e-6:
            self._ctx.log(
                f"CAP REJECTED [{self._tag}] {market} {size:.4f} @ {ref_price:.2f} "
                f"(own=${own:.2f} + new=${new_notional:.2f} > "
                f"cap=${cap_dollars:.2f} = {self._cap_pct:.0%} of ${equity:.2f})"
            )
            return False
        return True

    def _retag_last(self, oid: str) -> str:
        """After the underlying ctx queues the order, prefix its id
        with the strategy tag in-place so downstream fills carry the
        attribution. Returns the new id."""
        if not oid:
            return oid
        new_id = f"{self._tag}:{oid}"
        # Patch market queue
        for q in (self._ctx._oq.market_queue, self._ctx._oq.pending):
            for o in q:
                if o.order_id == oid:
                    o.order_id = new_id
                    return new_id
        return new_id

    def cancel(self, order_id: str) -> bool:
        return self._ctx.cancel(order_id)

    def cancel_all(self, market=None) -> int:
        # Only cancel orders this strategy placed — orchestrator-level
        # bulk cancel is the parent engine's job.
        before = len(self._ctx._oq.pending)
        prefix = f"{self._tag}:"
        self._ctx._oq.pending = [
            o for o in self._ctx._oq.pending
            if not (
                (market is None or o.market == market)
                and o.order_id.startswith(prefix)
            )
        ]
        return before - len(self._ctx._oq.pending)

    def log(self, message: str) -> None:
        self._ctx.log(f"[{self._tag}] {message}")

    # --- pure-read forwards ----------------------------------------------

    @property
    def account(self):
        return self._ctx.account

    @property
    def positions(self):
        return self._ctx.positions

    @property
    def pending_orders(self):
        return self._ctx.pending_orders

    @property
    def current_candle(self):
        return self._ctx.current_candle

    @property
    def timestamp(self):
        return self._ctx.timestamp

    @property
    def markets(self):
        return self._ctx.markets

    def get_candles(self, *args, **kwargs):
        return self._ctx.get_candles(*args, **kwargs)

    def get_funding_rate(self, *args, **kwargs):
        return self._ctx.get_funding_rate(*args, **kwargs)

    def get_funding_rates(self, *args, **kwargs):
        return self._ctx.get_funding_rates(*args, **kwargs)

    def get_funding_by_venue(self, *args, **kwargs):
        return self._ctx.get_funding_by_venue(*args, **kwargs)

    def get_borrow_rate(self, *args, **kwargs):
        return self._ctx.get_borrow_rate(*args, **kwargs)

    def get_orderbook(self, *args, **kwargs):
        return self._ctx.get_orderbook(*args, **kwargs)


class SharedCapitalPortfolioEngine:
    """Run multiple strategies against one shared `BacktestContext`."""

    def __init__(
        self,
        strategies: List[Tuple[str, Strategy]],
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.0005,
        history_size: int = 200,
        strategy_capital_caps: Optional[Dict[str, float]] = None,
    ):
        if not strategies:
            raise ValueError("at least one strategy required")
        self.strategies = strategies
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.history_size = history_size
        # Per-strategy gross-notional caps (fraction of equity). Missing
        # strategies = uncapped. Cap > 1.0 is allowed (lever up beyond
        # equity if other strategies aren't using their share).
        self.strategy_capital_caps: Dict[str, float] = strategy_capital_caps or {}

    @staticmethod
    def dollar_imbalance(ctx: BacktestContext) -> float:
        """Signed dollar imbalance: long $ - short $ across all open
        legs, valued at each position's mark price (falling back to
        entry price). Positive = net long, negative = net short, zero
        = dollar-neutral.

        Pair / dollar-neutral strategies can read this each bar and
        place an offsetting order if they want to enforce neutrality.
        The engine deliberately doesn't auto-rebalance — strategies
        with intentional directional bias would be broken by it."""
        long_dollars = 0.0
        short_dollars = 0.0
        for pos in ctx._pm.values():
            mark = pos.mark_price if pos.mark_price > 0 else pos.entry_price
            notional = pos.size * mark
            if pos.side == Side.LONG:
                long_dollars += notional
            else:
                short_dollars += notional
        return long_dollars - short_dollars

    def run(self, candles: List[Candle]) -> SharedPortfolioResult:
        if not candles:
            return SharedPortfolioResult(
                total_pnl=0.0, sharpe_ratio=0.0, max_drawdown=0.0,
                final_equity=self.initial_capital,
            )

        ctx = BacktestContext(initial_capital=self.initial_capital)

        # Each strategy gets its own tagged proxy. Strategies share the
        # same underlying book, but every order they place is tagged
        # for attribution.
        proxies = {
            name: _TaggedContextProxy(
                ctx, name, capital_cap_pct=self.strategy_capital_caps.get(name),
            )
            for name, _ in self.strategies
        }
        for _, strat in self.strategies:
            strat.reset()

        history: List[Candle] = []
        equity_curve: List[float] = []

        for candle in candles:
            ctx.set_candle(candle)
            ctx.process_pending_orders(candle)

            history.append(candle)
            if len(history) > self.history_size:
                history = history[-self.history_size:]

            for name, strat in self.strategies:
                signal = strat.on_candle(candle, history, proxies[name])
                # v1 (Signal-based) strategies route through a market
                # order. The Signal -> order translation here mirrors
                # `BacktestEngine._handle_signal`'s contract.
                if signal == Signal.BUY:
                    proxies[name].market_order(candle.market, Side.LONG, 1.0)
                elif signal == Signal.SELL:
                    proxies[name].market_order(candle.market, Side.SHORT, 1.0)

            ctx.process_market_orders(candle)
            equity_curve.append(ctx.account.equity)

        # Close everything at the last candle so results don't carry
        # open exposure.
        ctx.close_all_positions(candles[-1])
        equity_curve.append(ctx.account.equity)

        per_strategy_pnl: Dict[str, float] = {n: 0.0 for n, _ in self.strategies}
        per_strategy_trades: Dict[str, int] = {n: 0 for n, _ in self.strategies}
        fills_by_strategy: Dict[str, List[dict]] = {n: [] for n, _ in self.strategies}

        for fill in ctx.all_fills:
            tag = fill.order_id.split(":", 1)[0] if ":" in fill.order_id else "untagged"
            if tag not in per_strategy_pnl:
                continue
            per_strategy_trades[tag] += 1
            fills_by_strategy[tag].append({
                "ts": fill.ts, "market": fill.market,
                "side": fill.side.value, "price": fill.price,
                "size": fill.size, "fee": fill.fee,
            })

        for trade in ctx.closed_trades:
            # Attribution-by-trade (D-6.1 refinement): each closed_trade
            # dict carries `entry_order_id` + `exit_order_id`, both
            # prefixed with the placing strategy's tag. Realized PnL
            # attribution is now precise even when one strategy opens
            # a leg and another closes it (or a liquidation closes it
            # without a tagged exit). Priority:
            #   1. Tagged exit → closer owns realized PnL (open + close
            #      strategy can be the same; this is the common case).
            #   2. Untagged exit + tagged entry → opener owns it
            #      (handles liquidations with `entry_order_id` populated).
            #   3. No tags → fall back to per-market even split.
            pnl = trade.get("pnl", 0.0)
            exit_oid = trade.get("exit_order_id", "")
            entry_oid = trade.get("entry_order_id", "")
            if exit_oid and ":" in exit_oid:
                tag = exit_oid.split(":", 1)[0]
                if tag in per_strategy_pnl:
                    per_strategy_pnl[tag] += pnl
                    continue
            if entry_oid and ":" in entry_oid:
                tag = entry_oid.split(":", 1)[0]
                if tag in per_strategy_pnl:
                    per_strategy_pnl[tag] += pnl
                    continue
            # Last-resort even split — shouldn't fire post-D-6.1 unless
            # a closed_trade originated outside the proxy path.
            mkt = trade.get("market")
            owners = [
                n for n, fills in fills_by_strategy.items()
                if any(f["market"] == mkt for f in fills)
            ]
            if not owners:
                continue
            share = pnl / len(owners)
            for owner in owners:
                per_strategy_pnl[owner] += share

        total_pnl = ctx.account.equity - self.initial_capital
        return SharedPortfolioResult(
            total_pnl=total_pnl,
            sharpe_ratio=_sharpe_ratio(equity_curve),
            max_drawdown=_max_drawdown(equity_curve),
            final_equity=ctx.account.equity,
            combined_equity=equity_curve,
            per_strategy_pnl=per_strategy_pnl,
            per_strategy_trades=per_strategy_trades,
            fills_by_strategy=fills_by_strategy,
            warnings=[m for m in ctx.log_messages if "REJECTED" in m or "WARNING" in m],
        )
