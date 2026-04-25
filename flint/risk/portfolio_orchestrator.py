"""PortfolioMarginEngine — single pre-trade check facade.

D-3.5-orchestrator (Wave 2). Composes the three pre-existing
risk components — venue-level `MarginEngine`, capital-side
`VenueAllocator`, and book-level `PortfolioRiskEngine` — into one
object the BacktestContext can consult once per `market_order`. Today
those checks are scattered across BacktestContext (margin), per-trade
debit logic (allocator), and ad-hoc strategy code (book limits); this
class is the seam that lets the engine reject violators uniformly.

Decision returned by `check_order(...)`:

    PortfolioCheck(
        approved: bool,
        reason: str,
        component: str,   # "margin" | "allocator" | "portfolio" | ""
    )

`component` lets the caller log which sub-engine vetoed (the
BacktestContext warn-line includes it). `__bool__` returns `approved`
so `if not check: reject(check.reason)` reads naturally.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..execution.capital import VenueAllocator
from ..execution.margin import LiquidationEvent, MarginEngine
from ..models import Order, PositionInfo, Side
from .portfolio import OrderLite, PortfolioRiskEngine, PositionLite


@dataclass
class PortfolioCheck:
    """Outcome of `PortfolioMarginEngine.check_order`."""
    approved: bool
    reason: str = ""
    component: str = ""

    def __bool__(self) -> bool:
        return self.approved


class PortfolioMarginEngine:
    """Composes MarginEngine + VenueAllocator + PortfolioRiskEngine.

    Any of the three sub-engines may be `None` — when omitted, the
    corresponding check is a no-op pass. That keeps the existing
    backtest paths (margin-only, no allocator, no portfolio risk)
    behaviorally identical.
    """

    def __init__(
        self,
        margin: Optional[MarginEngine] = None,
        allocator: Optional[VenueAllocator] = None,
        portfolio: Optional[PortfolioRiskEngine] = None,
    ):
        self.margin = margin
        self.allocator = allocator
        self.portfolio = portfolio

    # -- pre-trade ----------------------------------------------------------

    def check_order(
        self,
        order: Order,
        cash: float,
        positions: List[PositionInfo],
        current_price: float,
        equity: Optional[float] = None,
    ) -> PortfolioCheck:
        """Evaluate `order` against all three engines in priority order.

        Order matters:
          1. Allocator — fastest reject; if the venue can't even cover
             the trade's funding, no point checking margin.
          2. MarginEngine — venue-level margin/leverage caps.
          3. PortfolioRiskEngine — book-level gross/net/concentration/
             VaR/correlation cluster checks.

        First failure short-circuits and `component` carries the source.
        """
        # Allocator: per-venue available cash
        if self.allocator is not None:
            venue = getattr(order, "venue", "default") or "default"
            notional = order.size * current_price
            available = self.allocator.available(venue)
            if notional > available:
                return PortfolioCheck(
                    approved=False,
                    reason=(
                        f"Allocator: venue {venue} only has "
                        f"${available:,.0f} available, need ${notional:,.0f}"
                    ),
                    component="allocator",
                )

        # MarginEngine: venue-level margin/leverage
        if self.margin is not None:
            allowed, why = self.margin.check_can_open(
                order, cash, positions, current_price
            )
            if not allowed:
                return PortfolioCheck(
                    approved=False, reason=why, component="margin",
                )

        # PortfolioRiskEngine: book-level checks
        if self.portfolio is not None:
            equity_for_book = equity if equity is not None else cash + sum(
                p.unrealized_pnl for p in positions
            )
            order_lite = OrderLite(
                market=order.market,
                venue=getattr(order, "venue", "default") or "default",
                side="long" if order.side == Side.LONG else "short",
                size=order.size,
                price=current_price,
            )
            position_lites = [
                PositionLite(
                    market=p.market,
                    venue=p.venue,
                    size=p.size if p.side == Side.LONG else -p.size,
                    entry_price=p.entry_price,
                )
                for p in positions
            ]
            r = self.portfolio.check_order(order_lite, equity_for_book, position_lites)
            if not r.approved:
                return PortfolioCheck(
                    approved=False, reason=r.reason, component="portfolio",
                )

        return PortfolioCheck(approved=True)

    # -- post-trade -------------------------------------------------------

    def check_liquidations(
        self,
        positions_dict,
        prices: dict,
        ts: int,
    ) -> List[LiquidationEvent]:
        """Delegate to MarginEngine. Returns [] when no margin engine."""
        if self.margin is None:
            return []
        return self.margin.check_liquidations(positions_dict, prices, ts)

    def check_kill_switch(self, equity: float) -> bool:
        """Delegate to PortfolioRiskEngine drawdown trip. False when no
        portfolio engine — kill-switch is opt-in."""
        if self.portfolio is None:
            return False
        return self.portfolio.check_kill_switch(equity)
