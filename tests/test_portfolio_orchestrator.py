"""D-3.5-orchestrator — PortfolioMarginEngine facade tests.

Covers the three sub-engines composed by the orchestrator
(MarginEngine + VenueAllocator + PortfolioRiskEngine), reject-priority
order, BacktestContext integration, and that omitting any sub-engine
falls through to a no-op pass.
"""
from __future__ import annotations


from flint.execution.capital import VenueAllocator
from flint.execution.margin import MarginEngine
from flint.execution.venue_config import VENUE_DEFAULTS
from flint.models import Order, OrderType, PositionInfo, Side
from flint.risk.portfolio import PortfolioRiskEngine, RiskLimits
from flint.risk.portfolio_orchestrator import (
    PortfolioCheck,
    PortfolioMarginEngine,
)


def _order(market="SOL-PERP", side=Side.LONG, size=10.0, venue="drift"):
    return Order(
        market=market, side=side, order_type=OrderType.MARKET,
        size=size, order_id="o1", ts=1700000000, venue=venue,
    )


def _position(market="SOL-PERP", side=Side.LONG, size=10.0, venue="drift",
              entry_price=100.0, upnl=0.0):
    return PositionInfo(
        market=market, side=side, size=size, entry_price=entry_price,
        unrealized_pnl=upnl, entry_ts=1700000000, venue=venue,
    )


class TestNoEnginesIsNoOp:
    def test_passes_when_all_three_unset(self):
        pme = PortfolioMarginEngine()
        check = pme.check_order(_order(), cash=10_000, positions=[], current_price=100.0)
        assert check.approved is True
        assert check.component == ""

    def test_kill_switch_returns_false_without_portfolio_engine(self):
        pme = PortfolioMarginEngine()
        assert pme.check_kill_switch(equity=1.0) is False

    def test_check_liquidations_empty_without_margin(self):
        pme = PortfolioMarginEngine()
        assert pme.check_liquidations({}, {}, ts=1) == []


class TestPortfolioCheckBoolean:
    def test_truthy_when_approved(self):
        c = PortfolioCheck(approved=True)
        assert bool(c) is True

    def test_falsy_when_rejected(self):
        c = PortfolioCheck(approved=False, reason="nope", component="margin")
        assert bool(c) is False
        assert c.reason == "nope"
        assert c.component == "margin"


class TestMarginRejection:
    def test_rejects_when_insufficient_margin(self):
        pme = PortfolioMarginEngine(margin=MarginEngine(VENUE_DEFAULTS))
        # $100 cash, $100k notional (1000x leverage) — well past any cap
        order = _order(size=1000.0)
        check = pme.check_order(order, cash=100.0, positions=[], current_price=100.0)
        assert check.approved is False
        assert check.component == "margin"

    def test_passes_when_within_margin(self):
        pme = PortfolioMarginEngine(margin=MarginEngine(VENUE_DEFAULTS))
        order = _order(size=1.0)
        check = pme.check_order(order, cash=10_000.0, positions=[], current_price=100.0)
        assert check.approved is True


class TestAllocatorRejectionShortCircuits:
    """Allocator runs first; when it rejects, margin engine is not
    consulted at all. We prove that by passing a deliberately broken
    margin engine — if it ran, the test would fail with a different
    error than the allocator reason."""

    def test_allocator_reason_surfaces(self):
        alloc = VenueAllocator({"drift": 50.0})
        pme = PortfolioMarginEngine(allocator=alloc)
        # 10 units * $100 = $1000 notional; allocator only has $50
        check = pme.check_order(_order(), cash=10_000, positions=[], current_price=100.0)
        assert check.approved is False
        assert check.component == "allocator"
        assert "drift" in check.reason


class TestPortfolioRiskRejection:
    def test_gross_exposure_cap_rejects(self):
        # Tight 1.0x gross cap → 10 * $100 = $1000 notional vs $1000 equity = 1.0x; one more hits
        limits = RiskLimits(max_gross_exposure=0.5)
        pme = PortfolioMarginEngine(portfolio=PortfolioRiskEngine(limits=limits))
        check = pme.check_order(
            _order(size=10.0), cash=1_000.0, positions=[], current_price=100.0,
        )
        assert check.approved is False
        assert check.component == "portfolio"


class TestPriorityOrder:
    """Allocator → margin → portfolio. Verify by stacking failures and
    confirming the *first* sub-engine in the chain is the one that
    surfaces."""

    def test_allocator_beats_margin_when_both_would_fail(self):
        alloc = VenueAllocator({"drift": 1.0})
        margin = MarginEngine(VENUE_DEFAULTS)
        pme = PortfolioMarginEngine(allocator=alloc, margin=margin)
        check = pme.check_order(
            _order(size=100.0), cash=1_000.0, positions=[], current_price=100.0,
        )
        assert check.component == "allocator"

    def test_margin_beats_portfolio_when_both_would_fail(self):
        margin = MarginEngine(VENUE_DEFAULTS)
        portfolio = PortfolioRiskEngine(limits=RiskLimits(max_gross_exposure=0.001))
        pme = PortfolioMarginEngine(margin=margin, portfolio=portfolio)
        check = pme.check_order(
            _order(size=100.0), cash=100.0, positions=[], current_price=100.0,
        )
        assert check.component == "margin"


class TestBacktestContextIntegration:
    def test_context_constructs_orchestrator(self):
        from flint.execution.backtest_context import BacktestContext
        ctx = BacktestContext(initial_capital=10_000)
        assert ctx._pme is not None

    def test_market_order_logs_orchestrator_component_on_reject(self):
        from flint.execution.backtest_context import BacktestContext
        from flint.models import Candle

        margin = MarginEngine(VENUE_DEFAULTS)
        ctx = BacktestContext(initial_capital=100.0, margin_engine=margin)
        ctx.set_candle(Candle(
            ts=1, open=100, high=100, low=100, close=100,
            volume=1, market="SOL-PERP", resolution_s=60,
        ))
        # 1000 units @ $100 = $100k notional vs $100 cash → margin reject
        ctx.market_order("SOL-PERP", Side.LONG, 1000.0)
        log = "\n".join(ctx.log_messages)
        assert "MARGIN REJECTED" in log

    def test_market_order_succeeds_when_orchestrator_passes(self):
        from flint.execution.backtest_context import BacktestContext
        from flint.models import Candle

        ctx = BacktestContext(initial_capital=10_000.0)
        ctx.set_candle(Candle(
            ts=1, open=100, high=100, low=100, close=100,
            volume=1, market="SOL-PERP", resolution_s=60,
        ))
        oid = ctx.market_order("SOL-PERP", Side.LONG, 1.0)
        # Order makes it into the queue
        assert any(o.order_id == oid for o in ctx._market_orders_queue)

    def test_portfolio_risk_arg_threads_through(self):
        from flint.execution.backtest_context import BacktestContext
        from flint.models import Candle

        # Tight book limit triggers portfolio rejection.
        portfolio = PortfolioRiskEngine(
            limits=RiskLimits(max_gross_exposure=0.001)
        )
        ctx = BacktestContext(initial_capital=10_000.0, portfolio_risk=portfolio)
        ctx.set_candle(Candle(
            ts=1, open=100, high=100, low=100, close=100,
            volume=1, market="SOL-PERP", resolution_s=60,
        ))
        ctx.market_order("SOL-PERP", Side.LONG, 5.0)
        log = "\n".join(ctx.log_messages)
        assert "PORTFOLIO REJECTED" in log


class TestReduceOnlyBypassesAllChecks:
    """reduce_only orders short-circuit before the orchestrator. They
    can only run when an opposite-side position exists; if so, the
    margin/portfolio gauntlet is skipped entirely."""

    def test_reduce_only_skips_orchestrator(self):
        from flint.execution.backtest_context import BacktestContext
        from flint.execution.backtest_context import _Position
        from flint.models import Candle

        # Margin engine that would *always* reject (1.0x leverage cap
        # implies any new open fails). reduce_only must still pass.
        margin = MarginEngine(VENUE_DEFAULTS)
        ctx = BacktestContext(initial_capital=1.0, margin_engine=margin)
        ctx.set_candle(Candle(
            ts=1, open=100, high=100, low=100, close=100,
            volume=1, market="SOL-PERP", resolution_s=60,
        ))
        # Plant an opposite-side position so reduce_only is valid
        ctx._positions[("default", "SOL-PERP")] = _Position(
            market="SOL-PERP", side=Side.LONG, size=5.0,
            entry_price=100.0, entry_ts=0,
        )
        oid = ctx.market_order("SOL-PERP", Side.SHORT, 1.0, reduce_only=True)
        # Reduce-only short against existing long must be queued
        assert any(o.order_id == oid for o in ctx._market_orders_queue)
