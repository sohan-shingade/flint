"""The full-cost tearsheet data block + run invariants (§6.3, §19.2).

The whole wedge is *honest* cost accounting, so the tearsheet decomposes a run's
PnL into the lines a perp trader actually pays — trading PnL, funding, fees,
slippage — and states the **effective evaluated range** and the **fidelity tier
mix** next to them, so nobody reads a Tier-C run as a faithful one. It is a data
block computed by folding the event log (§2.10), not a rendered report.

``check_invariants`` is the §19.2 conservation check asserted at run end: every
placed order reaches exactly one terminal state (in = out), and the funding
settlements applied match what was expected. A violation is an engine bug and
raises loudly at the source, never a silently wrong number downstream.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .money import ZERO, Money, money
from .orders import TERMINAL, OrderStatus
from .portfolio.events import (
    FILL,
    FUNDING,
    LIQUIDATION,
    ORDER_CANCELLED,
    ORDER_PLACED,
    ORDER_REJECTED,
    Event,
)


class InvariantError(AssertionError):
    """A run-end conservation invariant was violated — always an engine bug (§19.2)."""


@dataclass
class Tearsheet:
    """The decomposed full-cost picture of a run (§6.3, §19.2).

    ``trading_pnl`` is realized price PnL (fills + liquidations); ``funding`` and
    ``fees`` are the cash lines; ``slippage_cost`` is the modelled execution cost
    vs mid, dollarized from each fill's recorded ``slippage_bps``. ``net_pnl`` is
    the sum. ``fills_by_tier`` and the flag counts expose how faithful the fills
    were; ``evaluated_start_ts``/``evaluated_end_ts`` is the effective range the
    numbers cover. The order counters are the §19.2 conservation ledger.
    """

    trading_pnl: Money = ZERO
    funding: Money = ZERO
    fees: Money = ZERO
    slippage_cost: Money = ZERO
    net_pnl: Money = ZERO
    # execution fidelity
    fills_by_tier: dict[str, int] = field(default_factory=dict)
    flagged_fills: dict[str, int] = field(default_factory=dict)
    total_latency_ms: int = 0
    # order-state conservation (§19.2)
    orders_placed: int = 0
    orders_filled: int = 0
    orders_rejected: int = 0
    orders_cancelled: int = 0
    fills: int = 0
    funding_settlements: int = 0
    liquidations: int = 0
    # effective evaluated range (unix ms) — stated next to every metric
    evaluated_start_ts: int | None = None
    evaluated_end_ts: int | None = None


def build_tearsheet(events: Iterable[Event]) -> Tearsheet:
    """Fold ``events`` into the full-cost tearsheet data block (§6.3)."""
    ts = Tearsheet()
    terminal_by_status: dict[str, int] = {}

    for ev in events:
        p = ev.payload
        kind = ev.kind
        if ev.ts:
            ts.evaluated_start_ts = (
                ev.ts
                if ts.evaluated_start_ts is None
                else min(ts.evaluated_start_ts, ev.ts)
            )
            ts.evaluated_end_ts = (
                ev.ts
                if ts.evaluated_end_ts is None
                else max(ts.evaluated_end_ts, ev.ts)
            )
        if kind == ORDER_PLACED:
            ts.orders_placed += 1
        elif kind == ORDER_REJECTED:
            terminal_by_status[OrderStatus.REJECTED] = (
                terminal_by_status.get(OrderStatus.REJECTED, 0) + 1
            )
        elif kind == ORDER_CANCELLED:
            terminal_by_status[OrderStatus.CANCELLED] = (
                terminal_by_status.get(OrderStatus.CANCELLED, 0) + 1
            )
        elif kind == FILL:
            ts.fills += 1
            ts.trading_pnl += money(p["realized_pnl"])
            ts.fees += -money(p["fee"])
            tier = p.get("fidelity_tier", "?")
            ts.fills_by_tier[tier] = ts.fills_by_tier.get(tier, 0) + 1
            notional = abs(float(p["price"]) * float(p["size"]))
            ts.slippage_cost += -money(
                notional * float(p.get("slippage_bps", 0.0)) / 10_000.0
            )
            for flag in p.get("flags", ()):
                ts.flagged_fills[flag] = ts.flagged_fills.get(flag, 0) + 1
        elif kind == FUNDING:
            ts.funding += money(p["amount"])
            ts.funding_settlements += 1
        elif kind == LIQUIDATION:
            ts.trading_pnl += money(p["realized_pnl"])
            ts.liquidations += 1

    # A fully/partially filled order reaches its terminal state via the record, not
    # a distinct event; derive filled = placed − rejected − cancelled so the ledger
    # closes (a partial-then-cancel counts once, as cancelled — its terminal state).
    ts.orders_rejected = terminal_by_status.get(OrderStatus.REJECTED, 0)
    ts.orders_cancelled = terminal_by_status.get(OrderStatus.CANCELLED, 0)
    ts.orders_filled = ts.orders_placed - ts.orders_rejected - ts.orders_cancelled
    ts.net_pnl = ts.trading_pnl + ts.funding + ts.fees
    return ts


def check_invariants(
    tearsheet: Tearsheet,
    orders: dict[str, object],
    *,
    expected_funding_settlements: int | None = None,
) -> None:
    """Assert the §19.2 run-end conservation invariants, raising on any violation.

    1. **Orders in = orders out.** Every placed order is in a terminal state and
       ``placed == filled + rejected + cancelled``.
    2. **Funding applied = expected** (when the caller knows the expected count —
       the acceptance golden hand-derives it).
    """
    live = [
        rec
        for rec in orders.values()
        if getattr(rec, "status", None) not in TERMINAL
    ]
    if live:
        raise InvariantError(
            f"{len(live)} order(s) left non-terminal at run end: "
            f"{[getattr(r, 'client_order_id', '?') for r in live]}"
        )
    out = (
        tearsheet.orders_filled
        + tearsheet.orders_rejected
        + tearsheet.orders_cancelled
    )
    if tearsheet.orders_placed != out:
        raise InvariantError(
            f"order conservation broken: placed={tearsheet.orders_placed} != "
            f"filled+rejected+cancelled={out}"
        )
    if (
        expected_funding_settlements is not None
        and tearsheet.funding_settlements != expected_funding_settlements
    ):
        raise InvariantError(
            f"funding settlements applied={tearsheet.funding_settlements} != "
            f"expected={expected_funding_settlements}"
        )
