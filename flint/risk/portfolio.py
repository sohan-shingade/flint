"""PortfolioRiskEngine — book-level pre-trade checks.

Phase 6 T6.2. Sits above the per-strategy `RiskGuard` chain in
`flint/risk/guards.py` and evaluates aggregate book-level constraints:
gross / net exposure, per-venue / per-market concentration, correlation
clusters, historical-simulation VaR.

Every pre-trade check returns `(approved: bool, reason: str)`; a rejected
order is never submitted. Kill-switch hook (`check_kill_switch`) is polled
separately by the engine each bar and can force-flatten the book.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np


@dataclass
class RiskLimits:
    """Book-level caps. All ratios are multiples of current equity unless
    otherwise noted."""
    max_gross_exposure: float = 3.0     # |long| + |short|, as multiple of equity
    max_net_exposure: float = 1.5       # |long - short|, same units
    max_per_venue_pct: float = 0.70     # notional share on one venue
    max_per_market_pct: float = 0.50    # notional share on one market
    max_correlation_cluster_pct: float = 0.80  # markets with pairwise corr ≥ 0.8 count together
    max_drawdown_pct: float = 0.25      # kill-switch trigger
    var_limit_95_1d: Optional[float] = None  # USD; None disables VaR gate
    var_limit_99_1d: Optional[float] = None


@dataclass
class PositionLite:
    """Minimal info needed for the engine. Mirrors `PositionInfo` shape but
    decoupled so tests don't need the full engine plumbing."""
    market: str
    venue: str
    size: float          # positive long, negative short
    entry_price: float


@dataclass
class OrderLite:
    market: str
    venue: str
    side: str            # "long" | "short"
    size: float          # absolute units
    price: float         # current mark


@dataclass
class RiskCheckResult:
    approved: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.approved


@dataclass
class VaRReport:
    var_95: float
    var_99: float
    window_bars: int


class PortfolioRiskEngine:
    """Book-level pre-trade checks + kill switch.

    Usage:
        engine = PortfolioRiskEngine(RiskLimits(max_gross_exposure=2.0))
        r = engine.check_order(order, equity, positions)
        if not r: reject(r.reason)

        if engine.check_kill_switch(equity):
            flatten_all()
    """

    def __init__(
        self,
        limits: Optional[RiskLimits] = None,
        correlation_matrix: Optional[Dict[Tuple[str, str], float]] = None,
    ):
        self.limits = limits or RiskLimits()
        self._corr = correlation_matrix or {}
        self._peak_equity: float = 0.0
        self._return_history: List[float] = []  # per-bar returns for VaR

    # -- Kill-switch ----------------------------------------------------------

    def check_kill_switch(self, equity: float) -> bool:
        """Returns True when cumulative drawdown from peak breaches the cap.
        Engine should call this each bar after marking positions; on True,
        flatten all positions and stop trading."""
        if equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity <= 0:
            return False
        dd = (self._peak_equity - equity) / self._peak_equity
        return dd >= self.limits.max_drawdown_pct

    # -- Return tracking for VaR ----------------------------------------------

    def record_return(self, r: float) -> None:
        self._return_history.append(float(r))

    def var(self, window_bars: int = 252) -> VaRReport:
        """Historical-simulation VaR on the trailing `window_bars` returns."""
        if len(self._return_history) < 10:
            return VaRReport(var_95=0.0, var_99=0.0,
                             window_bars=len(self._return_history))
        rets = np.asarray(self._return_history[-window_bars:])
        # VaR expressed as a positive loss magnitude.
        var_95 = float(-np.quantile(rets, 0.05))
        var_99 = float(-np.quantile(rets, 0.01))
        return VaRReport(var_95=var_95, var_99=var_99,
                         window_bars=len(rets))

    # -- Pre-trade gate -------------------------------------------------------

    def check_order(
        self,
        order: OrderLite,
        equity: float,
        positions: Iterable[PositionLite],
    ) -> RiskCheckResult:
        """Evaluate a candidate order against every configured limit.
        Returns first failing reason; passes only when every check OK."""
        positions = list(positions)

        # Project the post-fill book.
        signed = order.size if order.side == "long" else -order.size
        proj_positions = list(positions) + [
            PositionLite(market=order.market, venue=order.venue,
                         size=signed, entry_price=order.price)
        ]

        lim = self.limits

        # Gross exposure
        notionals = [abs(p.size) * p.entry_price for p in proj_positions]
        gross = sum(notionals)
        if equity > 0 and gross / equity > lim.max_gross_exposure:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"gross exposure {gross / equity:.2f}x exceeds cap "
                    f"{lim.max_gross_exposure}x"
                ),
            )

        # Net exposure
        longs = sum(p.size * p.entry_price for p in proj_positions if p.size > 0)
        shorts = sum(-p.size * p.entry_price for p in proj_positions if p.size < 0)
        net = abs(longs - shorts)
        if equity > 0 and net / equity > lim.max_net_exposure:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"net exposure {net / equity:.2f}x exceeds cap "
                    f"{lim.max_net_exposure}x"
                ),
            )

        # Per-venue concentration
        if gross > 0:
            per_venue: Dict[str, float] = {}
            for p, n in zip(proj_positions, notionals):
                per_venue[p.venue] = per_venue.get(p.venue, 0.0) + n
            max_venue_pct = max(per_venue.values()) / gross
            if max_venue_pct > lim.max_per_venue_pct:
                top = max(per_venue, key=per_venue.get)
                return RiskCheckResult(
                    approved=False,
                    reason=(
                        f"venue {top!r} holds {max_venue_pct:.0%} of notional "
                        f"> cap {lim.max_per_venue_pct:.0%}"
                    ),
                )

            # Per-market concentration
            per_market: Dict[str, float] = {}
            for p, n in zip(proj_positions, notionals):
                per_market[p.market] = per_market.get(p.market, 0.0) + n
            max_market_pct = max(per_market.values()) / gross
            if max_market_pct > lim.max_per_market_pct:
                top = max(per_market, key=per_market.get)
                return RiskCheckResult(
                    approved=False,
                    reason=(
                        f"market {top!r} holds {max_market_pct:.0%} of notional "
                        f"> cap {lim.max_per_market_pct:.0%}"
                    ),
                )

            # Correlation clusters — merge markets with pairwise corr ≥ 0.8.
            if self._corr:
                clusters = self._compute_clusters(list(per_market.keys()),
                                                  threshold=0.8)
                for cluster in clusters:
                    if len(cluster) < 2:
                        continue
                    cluster_notional = sum(per_market.get(m, 0.0)
                                           for m in cluster)
                    if cluster_notional / gross > lim.max_correlation_cluster_pct:
                        return RiskCheckResult(
                            approved=False,
                            reason=(
                                f"correlated cluster {sorted(cluster)} holds "
                                f"{cluster_notional / gross:.0%} > cap "
                                f"{lim.max_correlation_cluster_pct:.0%}"
                            ),
                        )

        # VaR gates (loss projection * equity)
        if lim.var_limit_95_1d is not None:
            v = self.var()
            projected = v.var_95 * equity
            if projected > lim.var_limit_95_1d:
                return RiskCheckResult(
                    approved=False,
                    reason=(
                        f"1-day 95% VaR ${projected:,.0f} > cap "
                        f"${lim.var_limit_95_1d:,.0f}"
                    ),
                )

        return RiskCheckResult(approved=True)

    # -- Correlation clustering ----------------------------------------------

    def _compute_clusters(
        self,
        markets: List[str],
        threshold: float = 0.8,
    ) -> List[set]:
        """Group markets whose pairwise correlation is ≥ threshold.
        Simple transitive closure — graph is small in practice."""
        n = len(markets)
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(n):
            for j in range(i + 1, n):
                c = self._corr.get(
                    (markets[i], markets[j]),
                    self._corr.get((markets[j], markets[i]), 0.0),
                )
                if c >= threshold:
                    union(i, j)

        clusters: Dict[int, set] = {}
        for idx, m in enumerate(markets):
            clusters.setdefault(find(idx), set()).add(m)
        return list(clusters.values())
