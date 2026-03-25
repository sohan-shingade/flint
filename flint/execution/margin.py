"""Margin and liquidation tracking for backtesting.

Enforces per-venue margin requirements, computes liquidation prices,
and force-liquidates positions when mark price crosses the threshold.
Opt-in: disabled by default to preserve backward compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..models import Order, PositionInfo, Side
from .venue_config import VenueConfig, VENUE_DEFAULTS


@dataclass
class LiquidationEvent:
    """Record of a forced liquidation during backtest."""
    market: str
    venue: str
    ts: int
    side: str           # "long" or "short"
    size: float
    entry_price: float
    liq_price: float
    mark_price: float   # price that triggered liquidation
    loss: float          # realized loss including penalty
    penalty: float       # liquidation penalty fee


@dataclass
class MarginState:
    """Snapshot of margin utilization."""
    total_notional: float     # sum of all position notionals
    total_margin_used: float  # sum of margin allocated
    free_margin: float        # cash - margin_used
    margin_ratio: float       # equity / total_notional (higher = safer)
    leverage: float            # total_notional / equity
    per_venue: Dict[str, float]  # venue -> margin used


def compute_liquidation_price(
    entry_price: float,
    size: float,
    collateral: float,
    maintenance_margin: float = 0.05,
    is_long: bool = True,
) -> float:
    """Compute oracle price at which a position gets liquidated.

    For longs:  liq = entry - (collateral - mmr * notional) / size
    For shorts: liq = entry + (collateral - mmr * notional) / size
    """
    if size <= 0:
        return 0.0
    notional = entry_price * size
    margin_req = maintenance_margin * notional
    buffer = collateral - margin_req
    if is_long:
        liq = entry_price - buffer / size
        return max(liq, 0.0)  # can't go negative
    else:
        return entry_price + buffer / size


class MarginEngine:
    """Enforces margin requirements and tracks liquidation risk.

    Usage:
        engine = BacktestEngine(strategy, margin_engine=MarginEngine(configs))

    When enabled:
    - Orders are rejected if insufficient margin
    - Liquidation prices are computed on position open
    - Positions are force-closed when mark price crosses liquidation
    - Liquidation penalty fee is applied
    """

    def __init__(
        self,
        venue_configs: Optional[Dict[str, VenueConfig]] = None,
    ):
        self.configs = venue_configs or VENUE_DEFAULTS
        self._liquidation_events: List[LiquidationEvent] = []

    def get_config(self, venue: str) -> VenueConfig:
        return self.configs.get(venue, self.configs.get("default", VENUE_DEFAULTS["default"]))

    def check_can_open(
        self,
        order: Order,
        cash: float,
        positions: List[PositionInfo],
        current_price: float,
    ) -> Tuple[bool, str]:
        """Check if this order can be opened given current margin.

        Returns (allowed, reason).
        """
        config = self.get_config(order.venue)
        notional = order.size * current_price
        required_margin = notional * config.initial_margin

        # Compute current margin usage for this venue
        venue_margin_used = 0.0
        for pos in positions:
            if pos.venue == order.venue:
                venue_margin_used += pos.size * pos.entry_price * config.initial_margin

        available = cash - venue_margin_used
        if required_margin > available:
            return False, (
                f"Insufficient margin on {order.venue}: "
                f"need ${required_margin:,.0f}, have ${available:,.0f} free"
            )

        # Check leverage limit
        total_notional = sum(
            p.size * p.entry_price for p in positions if p.venue == order.venue
        ) + notional
        equity = cash + sum(p.unrealized_pnl for p in positions)
        if equity > 0 and total_notional / equity > config.max_leverage:
            return False, (
                f"Would exceed max leverage ({config.max_leverage}x) on {order.venue}"
            )

        return True, ""

    def compute_position_liq_price(
        self,
        entry_price: float,
        size: float,
        venue: str,
        side: Side,
        cash: float,
    ) -> float:
        """Compute liquidation price for a new position."""
        config = self.get_config(venue)
        # Collateral = margin allocated (initial margin * notional)
        notional = entry_price * size
        collateral = notional * config.initial_margin
        return compute_liquidation_price(
            entry_price, size, collateral,
            config.maintenance_margin,
            is_long=(side == Side.LONG),
        )

    def check_liquidations(
        self,
        positions: Dict,  # {(venue, market): _Position}
        current_prices: Dict[str, float],  # market -> current price
        current_ts: int,
    ) -> List[LiquidationEvent]:
        """Check all positions for liquidation.

        Called BEFORE strategy runs each bar. Returns events for any
        positions that should be force-liquidated.
        """
        events = []
        for (venue, market), pos in list(positions.items()):
            price = current_prices.get(market)
            if price is None:
                continue

            config = self.get_config(venue)
            notional = pos.entry_price * pos.size
            # Collateral = initial margin - cumulative funding paid
            # Funding payments drain (or increase) effective collateral
            collateral = notional * config.initial_margin - pos.funding_paid

            liq_price = compute_liquidation_price(
                pos.entry_price, pos.size, max(collateral, 0),
                config.maintenance_margin,
                is_long=(pos.side == Side.LONG),
            )

            liquidated = False
            if pos.side == Side.LONG and price <= liq_price:
                liquidated = True
            elif pos.side == Side.SHORT and price >= liq_price:
                liquidated = True

            if liquidated:
                # Compute loss at liquidation price
                if pos.side == Side.LONG:
                    pnl = (liq_price - pos.entry_price) * pos.size
                else:
                    pnl = (pos.entry_price - liq_price) * pos.size
                penalty = notional * config.liquidation_penalty
                total_loss = pnl - penalty

                event = LiquidationEvent(
                    market=market,
                    venue=venue,
                    ts=current_ts,
                    side=pos.side.value,
                    size=pos.size,
                    entry_price=pos.entry_price,
                    liq_price=liq_price,
                    mark_price=price,
                    loss=total_loss,
                    penalty=penalty,
                )
                events.append(event)
                self._liquidation_events.append(event)

        return events

    def compute_margin_state(
        self,
        cash: float,
        positions: List[PositionInfo],
    ) -> MarginState:
        """Compute current margin utilization snapshot."""
        per_venue: Dict[str, float] = {}
        total_notional = 0.0
        total_margin = 0.0

        for pos in positions:
            config = self.get_config(pos.venue)
            notional = pos.size * pos.entry_price
            margin = notional * config.initial_margin
            total_notional += notional
            total_margin += margin
            per_venue[pos.venue] = per_venue.get(pos.venue, 0) + margin

        equity = cash + sum(p.unrealized_pnl for p in positions)
        return MarginState(
            total_notional=total_notional,
            total_margin_used=total_margin,
            free_margin=cash - total_margin,
            margin_ratio=equity / total_notional if total_notional > 0 else float('inf'),
            leverage=total_notional / equity if equity > 0 else 0,
            per_venue=per_venue,
        )

    @property
    def liquidation_events(self) -> List[LiquidationEvent]:
        return list(self._liquidation_events)

    def reset(self) -> None:
        self._liquidation_events = []
