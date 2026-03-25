"""Capital fragmentation — per-venue balance tracking with transfer mechanics.

Models capital split across venues. Each venue has its own cash balance.
Transfers between venues take time and cost fees. Opt-in: when not used,
BacktestContext falls back to a single cash float.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Transfer:
    """A capital transfer between venues."""
    from_venue: str
    to_venue: str
    amount: float
    cost: float
    initiated_ts: int
    arrival_ts: int  # initiated_ts + transfer_time


@dataclass
class TransferConfig:
    """Transfer parameters between venues."""
    default_time_s: int = 1800      # 30 minutes
    default_cost_usd: float = 1.0
    routes: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # routes format: {"drift_to_hyperliquid": {"time_s": 600, "cost_usd": 5.0}}

    def get_time(self, from_venue: str, to_venue: str) -> int:
        key = f"{from_venue}_to_{to_venue}"
        route = self.routes.get(key, {})
        return int(route.get("time_s", self.default_time_s))

    def get_cost(self, from_venue: str, to_venue: str) -> float:
        key = f"{from_venue}_to_{to_venue}"
        route = self.routes.get(key, {})
        return route.get("cost_usd", self.default_cost_usd)


@dataclass
class FragmentationMetrics:
    """Summary of capital fragmentation during a backtest."""
    total_deployed: float               # sum of initial venue allocations
    peak_utilization: Dict[str, float]  # venue -> max % of capital used
    idle_capital_pct: float             # % of total capital never touched
    transfer_count: int
    transfer_costs: float
    time_in_transit_hours: float        # capital-hours locked in transit
    effective_return_pct: float         # PnL / total_deployed (not just used)
    venue_pnl: Dict[str, float]        # per-venue realized PnL


class VenueAllocator:
    """Manages per-venue capital balances with transfer delays.

    Usage:
        alloc = VenueAllocator({"drift": 5000, "hyperliquid": 3000})
        alloc.debit("drift", 100)       # pay fee on drift
        alloc.credit("drift", 250)      # receive PnL on drift
        alloc.transfer("drift", "hyperliquid", 1000, current_ts=1000)
        alloc.process_arrivals(current_ts=2800)  # 30min later, funds arrive
    """

    def __init__(
        self,
        initial_balances: Dict[str, float],
        transfer_config: Optional[TransferConfig] = None,
    ):
        self._balances: Dict[str, float] = dict(initial_balances)
        self._initial_balances = dict(initial_balances)
        self._config = transfer_config or TransferConfig()
        self._in_transit: List[Transfer] = []
        self._completed_transfers: List[Transfer] = []
        self._peak_balances: Dict[str, float] = dict(initial_balances)
        self._min_balances: Dict[str, float] = dict(initial_balances)
        self._venue_pnl: Dict[str, float] = {v: 0.0 for v in initial_balances}

    @property
    def total_cash(self) -> float:
        """Total cash across all venues + in-transit."""
        in_transit = sum(t.amount for t in self._in_transit)
        return sum(self._balances.values()) + in_transit

    @property
    def balances(self) -> Dict[str, float]:
        """Current per-venue balances (read-only copy)."""
        return dict(self._balances)

    def available(self, venue: str) -> float:
        """Cash available to trade on this venue right now."""
        return self._balances.get(venue, 0.0)

    def debit(self, venue: str, amount: float) -> bool:
        """Deduct cash from a venue (for fees, margin, etc). Returns False if insufficient."""
        bal = self._balances.get(venue, 0.0)
        if bal < amount:
            return False
        self._balances[venue] = bal - amount
        self._min_balances[venue] = min(
            self._min_balances.get(venue, bal), self._balances[venue]
        )
        return True

    def credit(self, venue: str, amount: float) -> None:
        """Add cash to a venue (from PnL, position close, etc)."""
        self._balances[venue] = self._balances.get(venue, 0.0) + amount
        self._peak_balances[venue] = max(
            self._peak_balances.get(venue, 0), self._balances[venue]
        )

    def track_pnl(self, venue: str, pnl: float) -> None:
        """Record PnL attribution for a venue."""
        self._venue_pnl[venue] = self._venue_pnl.get(venue, 0.0) + pnl

    def transfer(
        self, from_venue: str, to_venue: str, amount: float, current_ts: int
    ) -> Optional[Transfer]:
        """Initiate a transfer. Deducts immediately, arrives later.

        Returns the Transfer object, or None if insufficient balance.
        """
        cost = self._config.get_cost(from_venue, to_venue)
        total_debit = amount + cost

        if self._balances.get(from_venue, 0.0) < total_debit:
            return None

        self._balances[from_venue] -= total_debit
        time_s = self._config.get_time(from_venue, to_venue)

        t = Transfer(
            from_venue=from_venue,
            to_venue=to_venue,
            amount=amount,
            cost=cost,
            initiated_ts=current_ts,
            arrival_ts=current_ts + time_s,
        )
        self._in_transit.append(t)
        return t

    def process_arrivals(self, current_ts: int) -> List[Transfer]:
        """Credit arrived transfers. Called each bar by engine."""
        arrived = []
        remaining = []
        for t in self._in_transit:
            if current_ts >= t.arrival_ts:
                self._balances[t.to_venue] = self._balances.get(t.to_venue, 0.0) + t.amount
                self._peak_balances[t.to_venue] = max(
                    self._peak_balances.get(t.to_venue, 0), self._balances[t.to_venue]
                )
                self._completed_transfers.append(t)
                arrived.append(t)
            else:
                remaining.append(t)
        self._in_transit = remaining
        return arrived

    @property
    def pending_transfers(self) -> List[Transfer]:
        return list(self._in_transit)

    def compute_metrics(self, final_equity: float) -> FragmentationMetrics:
        """Compute fragmentation metrics at end of backtest."""
        total_deployed = sum(self._initial_balances.values())

        # Peak utilization per venue
        peak_util = {}
        for venue, initial in self._initial_balances.items():
            if initial > 0:
                max_used = initial - self._min_balances.get(venue, initial)
                peak_util[venue] = min(max_used / initial, 1.0)
            else:
                peak_util[venue] = 0.0

        # Idle capital: venues that were never drawn down
        total_used = sum(
            self._initial_balances[v] - self._min_balances.get(v, self._initial_balances[v])
            for v in self._initial_balances
        )
        idle_pct = (1.0 - total_used / total_deployed) * 100 if total_deployed > 0 else 100

        # Transfer stats
        transfer_costs = sum(t.cost for t in self._completed_transfers)
        transit_hours = sum(
            (t.arrival_ts - t.initiated_ts) * t.amount / 3600
            for t in self._completed_transfers
        )

        # Effective return on total deployed
        pnl = final_equity - total_deployed
        eff_return = (pnl / total_deployed * 100) if total_deployed > 0 else 0

        return FragmentationMetrics(
            total_deployed=total_deployed,
            peak_utilization=peak_util,
            idle_capital_pct=round(idle_pct, 1),
            transfer_count=len(self._completed_transfers),
            transfer_costs=round(transfer_costs, 2),
            time_in_transit_hours=round(transit_hours, 2),
            effective_return_pct=round(eff_return, 2),
            venue_pnl=dict(self._venue_pnl),
        )
