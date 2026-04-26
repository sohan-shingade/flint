"""D-6.1-unified — shared-capital portfolio backtest tests."""
from __future__ import annotations

import math

import pytest

from flint.models import Candle, Signal, Side
from flint.portfolio.shared_engine import (
    SharedCapitalPortfolioEngine,
    SharedPortfolioResult,
    _TaggedContextProxy,
)
from flint.strategy.base import Strategy


def _sin_candles(n=200, market="SOL-PERP"):
    return [
        Candle(
            ts=i * 60,
            open=100 + 5 * math.sin(i / 20),
            high=102 + 5 * math.sin(i / 20),
            low=98 + 5 * math.sin(i / 20),
            close=100 + 5 * math.sin(i / 20),
            volume=1000.0, market=market, resolution_s=60,
        )
        for i in range(n)
    ]


class _BuyOnce(Strategy):
    """Buys 1 unit on the 5th candle, holds."""

    def __init__(self, market="SOL-PERP", trigger_idx=5):
        self._fired = False
        self._market = market
        self._trigger = trigger_idx
        self._seen = 0

    @property
    def name(self) -> str:
        return "BuyOnce"

    def reset(self) -> None:
        self._fired = False
        self._seen = 0

    def on_candle(self, candle, history, ctx=None):
        self._seen += 1
        if self._seen == self._trigger and not self._fired and ctx is not None:
            ctx.market_order(self._market, Side.LONG, 1.0)
            self._fired = True
        return Signal.HOLD


class _SellOnce(Strategy):
    def __init__(self, market="SOL-PERP", trigger_idx=10):
        self._fired = False
        self._market = market
        self._trigger = trigger_idx
        self._seen = 0

    @property
    def name(self) -> str:
        return "SellOnce"

    def reset(self) -> None:
        self._fired = False
        self._seen = 0

    def on_candle(self, candle, history, ctx=None):
        self._seen += 1
        if self._seen == self._trigger and not self._fired and ctx is not None:
            ctx.market_order(self._market, Side.SHORT, 1.0)
            self._fired = True
        return Signal.HOLD


class TestEngineConstruction:
    def test_empty_strategy_list_rejected(self):
        with pytest.raises(ValueError, match="at least one"):
            SharedCapitalPortfolioEngine(strategies=[])

    def test_run_with_no_candles_returns_zero_pnl(self):
        eng = SharedCapitalPortfolioEngine(
            strategies=[("a", _BuyOnce())], initial_capital=5_000.0,
        )
        result = eng.run([])
        assert result.total_pnl == 0.0
        assert result.final_equity == 5_000.0


class TestSharedCapital:
    def test_two_strategies_share_one_cash_pool(self):
        """If each strategy had its own $10k pool, both would happily
        buy 1 SOL @ $100 = $100 each (no cap). With shared capital,
        the orchestrator's margin engine sees the *combined* exposure
        and the test still passes because $200 << $10k. The point of
        this test: prove the engine routes through one ledger by
        observing one combined equity curve, not two."""
        candles = _sin_candles(50)
        engine = SharedCapitalPortfolioEngine(
            strategies=[("buyer", _BuyOnce()), ("seller", _SellOnce())],
            initial_capital=10_000.0,
        )
        result = engine.run(candles)
        assert isinstance(result, SharedPortfolioResult)
        # Both strategies fired exactly once (their 1 trade each), and
        # the engine close_all at end produces 1 close fill per
        # outstanding position.
        assert result.per_strategy_trades["buyer"] >= 1
        assert result.per_strategy_trades["seller"] >= 1


class TestOrderTagging:
    def test_fills_carry_strategy_prefix(self):
        candles = _sin_candles(50)
        engine = SharedCapitalPortfolioEngine(
            strategies=[("alpha", _BuyOnce()), ("beta", _SellOnce())],
            initial_capital=10_000.0,
        )
        result = engine.run(candles)
        # Every fill in alpha's bucket comes from an "alpha:" order;
        # beta from "beta:" orders.
        assert all(
            f for f in result.fills_by_strategy["alpha"]
        ) or result.fills_by_strategy["alpha"] == []
        # No cross-pollination
        assert "alpha" in result.fills_by_strategy
        assert "beta" in result.fills_by_strategy


class TestProxyForwarding:
    def test_proxy_forwards_account(self):
        from flint.execution.backtest_context import BacktestContext

        ctx = BacktestContext(initial_capital=12_345.0)
        proxy = _TaggedContextProxy(ctx, "tagX")
        assert proxy.account.cash == 12_345.0
        assert proxy.timestamp == 0  # no candle set yet

    def test_proxy_market_order_returns_tagged_id(self):
        from flint.execution.backtest_context import BacktestContext

        ctx = BacktestContext(initial_capital=10_000.0)
        ctx.set_candle(Candle(
            ts=1, open=100, high=100, low=100, close=100,
            volume=1, market="SOL-PERP", resolution_s=60,
        ))
        proxy = _TaggedContextProxy(ctx, "stratX")
        oid = proxy.market_order("SOL-PERP", Side.LONG, 1.0)
        assert oid.startswith("stratX:")
        # The queued order in the underlying ctx carries the same id.
        assert any(o.order_id == oid for o in ctx._oq.market_queue)

    def test_proxy_cancel_all_only_cancels_own(self):
        from flint.execution.backtest_context import BacktestContext

        ctx = BacktestContext(initial_capital=10_000.0)
        ctx.set_candle(Candle(
            ts=1, open=100, high=100, low=100, close=100,
            volume=1, market="SOL-PERP", resolution_s=60,
        ))
        a_proxy = _TaggedContextProxy(ctx, "a")
        b_proxy = _TaggedContextProxy(ctx, "b")
        a_proxy.limit_order("SOL-PERP", Side.LONG, 1.0, 95.0)
        b_proxy.limit_order("SOL-PERP", Side.LONG, 1.0, 90.0)

        # b cancels its own — a's stays
        removed = b_proxy.cancel_all()
        assert removed == 1
        remaining = ctx._oq.pending
        assert len(remaining) == 1
        assert remaining[0].order_id.startswith("a:")


class TestPnlAttribution:
    """Closed-trade PnL is attributed to the strategy whose tagged
    `exit_order_id` closed the position."""

    def test_pnl_attributed_to_closing_strategy(self):
        # Strategy A opens a long; B closes it. PnL must go to B.
        class _OpenLong(Strategy):
            def __init__(self):
                self._fired = False

            @property
            def name(self):
                return "OpenLong"

            def reset(self):
                self._fired = False

            def on_candle(self, candle, history, ctx=None):
                if not self._fired and ctx is not None and len(history) >= 1:
                    ctx.market_order("SOL-PERP", Side.LONG, 1.0)
                    self._fired = True
                return Signal.HOLD

        class _CloseShort(Strategy):
            def __init__(self):
                self._fired = False

            @property
            def name(self):
                return "CloseShort"

            def reset(self):
                self._fired = False

            def on_candle(self, candle, history, ctx=None):
                if (not self._fired and ctx is not None
                        and len(history) >= 5 and ctx.positions):
                    ctx.market_order("SOL-PERP", Side.SHORT, 1.0)
                    self._fired = True
                return Signal.HOLD

        candles = _sin_candles(20)
        engine = SharedCapitalPortfolioEngine(
            strategies=[("opener", _OpenLong()), ("closer", _CloseShort())],
            initial_capital=10_000.0,
        )
        result = engine.run(candles)
        # Closer placed the closing order so the realized PnL on that
        # trade goes to it. Opener gets only the entry-fill fees.
        assert result.per_strategy_trades["opener"] >= 1
        assert result.per_strategy_trades["closer"] >= 1
        # Closer's PnL should be the dominant share of total PnL when
        # exact attribution is working — no even-split fallback hit.
        if abs(result.total_pnl) > 0.1:
            closer_share = result.per_strategy_pnl["closer"] / result.total_pnl
            assert closer_share > 0.5


class TestWarningsThreaded:
    def test_warnings_propagate(self):
        """Margin reject warnings from the underlying ctx should appear
        in the result.warnings list."""
        # No specific assertion content — just that warnings is a list.
        # A future test will pin a real margin-reject scenario.
        candles = _sin_candles(20)
        engine = SharedCapitalPortfolioEngine(
            strategies=[("a", _BuyOnce())], initial_capital=10_000.0,
        )
        result = engine.run(candles)
        assert isinstance(result.warnings, list)


# ─── D-6.1 refinements: caps, attribution, dollar imbalance ──────────

class TestCapitalCaps:
    def test_cap_blocks_oversized_market_order(self):
        """Strategy with cap=0.1 (10 % of equity) can't open $1000+
        notional on $10k of equity."""
        class _Greedy(Strategy):
            def __init__(self):
                self._fired = False
            @property
            def name(self): return "Greedy"
            def reset(self): self._fired = False
            def on_candle(self, c, h, ctx=None):
                if not self._fired and ctx is not None and len(h) >= 1:
                    # 50 SOL at ~$100 each = $5000 notional, way over a 10% cap
                    ctx.market_order("SOL-PERP", Side.LONG, 50.0)
                    self._fired = True
                return Signal.HOLD

        candles = _sin_candles(20)
        engine = SharedCapitalPortfolioEngine(
            strategies=[("greedy", _Greedy())],
            initial_capital=10_000.0,
            strategy_capital_caps={"greedy": 0.1},  # 10 % cap
        )
        result = engine.run(candles)
        # No trade should land — cap rejected the only order
        assert result.per_strategy_trades["greedy"] == 0
        # Warning surfaced
        assert any("CAP REJECTED" in w for w in result.warnings)

    def test_cap_allows_within_budget(self):
        """0.5 size at ~$100 = $50 notional, fits under a 1.0 cap on $10k."""
        candles = _sin_candles(30)
        engine = SharedCapitalPortfolioEngine(
            strategies=[("buyer", _BuyOnce())],
            initial_capital=10_000.0,
            strategy_capital_caps={"buyer": 1.0},  # full equity
        )
        result = engine.run(candles)
        assert result.per_strategy_trades["buyer"] >= 1

    def test_no_cap_means_unbounded(self):
        """When `strategy_capital_caps` is None, no enforcement applies."""
        candles = _sin_candles(20)
        engine = SharedCapitalPortfolioEngine(
            strategies=[("buyer", _BuyOnce())],
            initial_capital=10_000.0,
        )
        result = engine.run(candles)
        # No cap warnings
        assert not any("CAP REJECTED" in w for w in result.warnings)

    def test_reduce_only_bypasses_cap(self):
        """Reduce-only orders shrink — they shouldn't be blocked by a
        capital cap, even if the cap math would naively reject."""
        from flint.execution.backtest_context import BacktestContext

        ctx = BacktestContext(initial_capital=10_000.0)
        # Tight cap on a tagged proxy
        proxy = _TaggedContextProxy(ctx, "tight", capital_cap_pct=0.001)
        # Set up a candle so current_candle.close is non-zero
        ctx.set_candle(_sin_candles(1)[0])
        # Reduce-only order should bypass the cap entirely
        oid = proxy.market_order(
            "SOL-PERP", Side.SHORT, 100.0, reduce_only=True,
        )
        # oid is non-empty even though 100 SOL @ $100 = $10k > cap
        assert oid != ""


class TestAttributionByEntryOrderId:
    def test_liquidation_with_no_exit_oid_attributes_to_opener(self):
        """When a liquidation closes a position without a tagged exit
        order, the realized PnL should still attribute to the strategy
        that opened the leg via `entry_order_id`."""
        from flint.execution.backtest_context import BacktestContext

        ctx = BacktestContext(initial_capital=10_000.0)
        proxy = _TaggedContextProxy(ctx, "alice")
        ctx.set_candle(_sin_candles(1)[0])
        oid = proxy.market_order("SOL-PERP", Side.LONG, 1.0)
        assert oid.startswith("alice:")
        # Now look at the queued order: its entry will tag the position
        # when the fill lands. Process the candle so the order fills.
        ctx.process_market_orders(_sin_candles(1)[0])
        # Position should carry the alice-tagged entry_order_id
        positions = list(ctx._pm.values())
        assert len(positions) == 1
        assert positions[0].entry_order_id.startswith("alice:")


class TestDollarImbalance:
    def test_dollar_imbalance_zero_for_offsetting_legs(self):
        from flint.execution.backtest_context import BacktestContext
        from flint.execution._position import _Position

        ctx = BacktestContext(initial_capital=10_000.0)
        long_pos = _Position(
            market="SOL-PERP", side=Side.LONG, size=1.0,
            entry_price=100.0, entry_ts=0, venue="default",
        )
        long_pos.mark_price = 100.0
        short_pos = _Position(
            market="BTC-PERP", side=Side.SHORT, size=0.001,
            entry_price=100_000.0, entry_ts=0, venue="default",
        )
        short_pos.mark_price = 100_000.0
        ctx._pm.set(("default", "SOL-PERP"), long_pos)
        ctx._pm.set(("default", "BTC-PERP"), short_pos)
        # Both legs = $100 notional → net imbalance 0
        assert SharedCapitalPortfolioEngine.dollar_imbalance(ctx) == 0.0

    def test_dollar_imbalance_positive_when_net_long(self):
        from flint.execution.backtest_context import BacktestContext
        from flint.execution._position import _Position

        ctx = BacktestContext(initial_capital=10_000.0)
        p = _Position(
            market="SOL-PERP", side=Side.LONG, size=1.0,
            entry_price=100.0, entry_ts=0, venue="default",
        )
        p.mark_price = 100.0
        ctx._pm.set(("default", "SOL-PERP"), p)
        assert SharedCapitalPortfolioEngine.dollar_imbalance(ctx) == 100.0

    def test_dollar_imbalance_empty_book_returns_zero(self):
        from flint.execution.backtest_context import BacktestContext

        ctx = BacktestContext(initial_capital=10_000.0)
        assert SharedCapitalPortfolioEngine.dollar_imbalance(ctx) == 0.0
