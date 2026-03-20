"""MEV simulator — replay historical data and estimate extractable value.

Takes historical trade records, pool states, and position data to
simulate what MEV opportunities existed and what profit could have
been extracted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..models import ArbRoute, Candle, LiquidationOpportunity, PoolState
from .arb import ArbDetector
from .liquidation import LiquidationScanner


@dataclass
class MevSnapshot:
    """MEV state at a single point in time."""
    ts: int
    arb_routes: List[ArbRoute] = field(default_factory=list)
    liquidations: List[LiquidationOpportunity] = field(default_factory=list)
    total_arb_profit: float = 0.0
    total_liq_profit: float = 0.0

    @property
    def total_profit(self) -> float:
        return self.total_arb_profit + self.total_liq_profit


@dataclass
class SimulationResult:
    """Aggregated results from a historical MEV simulation."""
    snapshots: List[MevSnapshot] = field(default_factory=list)
    total_arb_profit: float = 0.0
    total_liq_profit: float = 0.0
    total_arb_opportunities: int = 0
    total_liq_opportunities: int = 0
    periods_scanned: int = 0

    @property
    def total_profit(self) -> float:
        return self.total_arb_profit + self.total_liq_profit

    @property
    def avg_arb_profit_per_period(self) -> float:
        return self.total_arb_profit / self.periods_scanned if self.periods_scanned else 0


class MevSimulator:
    """Simulates historical MEV extraction opportunities.

    Usage::

        sim = MevSimulator()
        result = sim.run(
            pool_snapshots={ts: [pools...]},
            position_snapshots={ts: [positions...]},
            oracle_prices={ts: {"SOL-PERP": 130.5, ...}},
            start_token="So11...112",
        )
    """

    def __init__(
        self,
        arb_min_profit_bps: float = 10.0,
        arb_amount: float = 1.0,
        liq_maintenance_margin: float = 0.05,
        liq_fee: float = 0.01,
    ) -> None:
        self._arb_detector = ArbDetector(min_profit_bps=arb_min_profit_bps)
        self._arb_amount = arb_amount
        self._liq_scanner = LiquidationScanner(
            maintenance_margin_ratio=liq_maintenance_margin,
            liquidation_fee=liq_fee,
        )

    def run(
        self,
        pool_snapshots: Dict[int, List[PoolState]],
        position_snapshots: Dict[int, List[Dict]],
        oracle_prices: Dict[int, Dict[str, float]],
        start_token: str,
    ) -> SimulationResult:
        """Run the simulation over time-keyed snapshots.

        Args:
            pool_snapshots: {timestamp: [PoolState, ...]}
            position_snapshots: {timestamp: [{position dict}, ...]}
            oracle_prices: {timestamp: {"MARKET": price, ...}}
            start_token: token mint to start arb routes from
        """
        all_timestamps = sorted(
            set(pool_snapshots.keys())
            | set(position_snapshots.keys())
            | set(oracle_prices.keys())
        )

        result = SimulationResult()
        for ts in all_timestamps:
            snap = MevSnapshot(ts=ts)

            # Arb scanning
            pools = pool_snapshots.get(ts, [])
            if pools:
                self._arb_detector.update_pools(pools)
                routes = self._arb_detector.find_arb_routes(
                    start_token, self._arb_amount,
                )
                snap.arb_routes = routes
                snap.total_arb_profit = sum(r.profit for r in routes)
                result.total_arb_opportunities += len(routes)
                result.total_arb_profit += snap.total_arb_profit

            # Liquidation scanning
            positions = position_snapshots.get(ts, [])
            prices = oracle_prices.get(ts, {})
            if positions and prices:
                liqs = self._liq_scanner.scan_positions(positions, prices)
                snap.liquidations = liqs
                snap.total_liq_profit = sum(l.estimated_profit for l in liqs)
                result.total_liq_opportunities += len(liqs)
                result.total_liq_profit += snap.total_liq_profit

            result.snapshots.append(snap)
            result.periods_scanned += 1

        return result
