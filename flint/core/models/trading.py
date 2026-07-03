"""Trading models — orders, fills, positions (§5).

``Order`` is mutable: it moves through a persisted state machine (§6.2). ``Fill``
and ``Position`` are frozen — a fill is an immutable record and a position is a
value snapshot. Sizes/prices are floats; ``ts`` is unix ms.
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import OrderType, Side, TimeInForce


@dataclass
class Order:
    """An instruction to trade. Mutable — the engine advances its state (§6.2).

    ``client_order_id`` is the client-generated idempotency key: the same id
    submitted twice (e.g. a paper reconnect) is recognized and not double-filled.
    """

    market: str
    side: Side
    type: OrderType
    size: float  # base units; tick/lot-rounded by the engine (§8.1)
    price: float = 0.0  # 0 = market order
    client_order_id: str = ""
    venue: str = ""
    tif: TimeInForce = TimeInForce.IOC
    margin_mode: str = "cross"  # "cross" | "isolated" (§6.5), per-order like HL
    reduce_only: bool = False  # Signal.close() -> reduce-only full-size order
    builder_code: str = ""  # reserved (monetization, live-only, v2)


@dataclass(frozen=True)
class Fill:
    """An immutable record of an executed trade (§5).

    ``liquidity`` and ``fidelity_tier`` are recorded PER FILL so the tearsheet
    can aggregate an audit trail of how faithful each execution was (§6.3).
    """

    market: str
    side: Side
    price: float
    size: float
    fee: float
    ts: int
    client_order_id: str
    is_partial: bool = False
    slippage_bps: float = 0.0  # how much worse than mid (attribution)
    venue: str = ""
    liquidity: str = "taker"  # "maker" | "taker"
    fidelity_tier: str = "C"  # "A" | "B" | "C"


@dataclass(frozen=True)
class Position:
    """An open position (§5).

    ``liq_price`` and unrealized PnL are computed off the CURRENT MarkSnapshot,
    so they are methods here rather than stored fields. ``liq_price`` needs the
    venue's maintenance-margin tiers (§6.5) and lands with the liquidation
    engine; ``unrealized_pnl``/``notional`` are pure arithmetic and live here.
    """

    market: str
    venue: str
    side: Side
    size: float
    entry_price: float
    margin_mode: str  # "cross" | "isolated"
    isolated_margin: float = 0.0  # if isolated: the dedicated collateral

    @property
    def signed_size(self) -> float:
        """Size with direction: +size for LONG, -size for SHORT."""
        return self.side.sign * self.size

    def notional(self, mark_price: float) -> float:
        """Absolute position value at ``mark_price``."""
        return mark_price * self.size

    def unrealized_pnl(self, mark_price: float) -> float:
        """Mark-to-market PnL against ``mark_price`` (§5, §6.5)."""
        return (mark_price - self.entry_price) * self.side.sign * self.size
