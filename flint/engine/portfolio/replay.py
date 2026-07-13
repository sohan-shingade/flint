"""Replay/fold over the event log → ``BookState`` (§2.10, §6.2).

Final state is **computed by replaying** the append-only event log, never stored
directly — that is what makes a run reproducible, auditable, and exportable
(§2.10). ``fold`` walks the events in sequence and rebuilds the whole book: the
per-venue accounts, the position book, and the order state machine.

The position math is the single function ``apply_fill_delta``, shared with the
live loop (``loop._apply_fill`` calls it too). That sharing is deliberate: it
makes ``fold(events)`` byte-identical to the engine's own final state *by
construction* rather than by a parallel reimplementation that could silently
drift — the §2.10 reproducibility contract with no second source of truth.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from flint.core.models import OrderType, Position, Side

from ..money import Money, money
from ..orders import OrderRecord, OrderStatus
from ..state import Account, PortfolioState
from .events import (
    FILL,
    FUNDING,
    LIQUIDATION,
    ORDER_CANCELLED,
    ORDER_PLACED,
    ORDER_REJECTED,
    Event,
)


def apply_fill_delta(
    positions: dict[tuple[str, str], Position],
    key: tuple[str, str],
    *,
    side: Side,
    size: float,
    price: float,
    margin_mode: str,
) -> float:
    """Apply one fill to the position book at ``key`` and return realized PnL.

    Mirrors the §6.1 open / increase / reduce / flip rules exactly. A same-side
    fill averages the entry; an opposite-side fill closes ``min(size)`` (realizing
    PnL), then leaves the remainder, flat, or a flipped position. Realized PnL is
    a float here (instantaneous mark-to-market arithmetic, §5); the caller lifts
    it into a ``Decimal`` accumulator.
    """
    venue, market = key
    pos = positions.get(key)
    if pos is None:
        positions[key] = Position(
            market=market,
            venue=venue,
            side=side,
            size=size,
            entry_price=price,
            margin_mode=margin_mode,
        )
        return 0.0
    if pos.side is side:  # increase: size-weighted average entry
        total = pos.size + size
        entry = (pos.entry_price * pos.size + price * size) / total
        positions[key] = replace(pos, size=total, entry_price=entry)
        return 0.0
    # opposite side: realize against the closed portion
    closed = min(pos.size, size)
    realized = (price - pos.entry_price) * pos.side.sign * closed
    remaining = pos.size - size
    if remaining > 0:  # partial close
        positions[key] = replace(pos, size=remaining)
    elif remaining == 0:  # flat
        del positions[key]
    else:  # flip: close the old side, open the remainder on the fill side
        positions[key] = replace(pos, side=side, size=-remaining, entry_price=price)
    return realized


@dataclass
class BookState:
    """The whole book reconstructed from an event log by ``fold`` (§2.10).

    Same three surfaces the live engine exposes — per-venue ``accounts``, the
    ``positions`` book, and the ``orders`` state machine — so a folded book can be
    compared field-for-field against the engine's ``PortfolioState`` in a parity test.
    """

    accounts: dict[str, Account] = field(default_factory=dict)
    positions: dict[tuple[str, str], Position] = field(default_factory=dict)
    orders: dict[str, OrderRecord] = field(default_factory=dict)


def fold(events: Iterable[Event]) -> BookState:
    """Replay ``events`` (in sequence order) into a ``BookState`` (§2.10).

    Cash moves only on the three monetary events — FILL (fee + realized PnL),
    FUNDING (settled payment), LIQUIDATION (post-trigger realized loss) — each
    read straight off the payload the loop wrote, so the reconstruction cannot
    diverge from what actually happened. Order-state events drive the §6.2 machine.
    Duplicate ORDER_PLACED ids fold once (the idempotency guard, §6.2).
    """
    state = PortfolioState()
    orders: dict[str, OrderRecord] = {}

    for ev in events:
        p = ev.payload
        kind = ev.kind
        if kind == ORDER_PLACED:
            coid = p["client_order_id"]
            if coid in orders:
                continue  # idempotent: a re-submitted id is recognized, not doubled
            orders[coid] = OrderRecord(
                client_order_id=coid,
                market=p["market"],
                venue=p["venue"],
                side=Side(p["side"]),
                type=OrderType(p["type"]),
                size=p["size"],
                price=p["price"],
                status=OrderStatus.PLACED,
            )
        elif kind == FILL:
            acct = state.account(p["venue"])
            acct.cash -= money(p["fee"])
            acct.fees_paid += money(p["fee"])
            realized = apply_fill_delta(
                state.positions,
                (p["venue"], p["market"]),
                side=Side(p["side"]),
                size=p["size"],
                price=p["price"],
                margin_mode=p.get("margin_mode", "cross"),
            )
            if realized:
                acct.credit(money(realized))
                acct.realized_pnl += money(realized)
            rec = orders.get(p["client_order_id"])
            if rec is not None and not rec.is_terminal:
                rec.apply_fill(p["size"])
        elif kind == FUNDING:
            acct = state.account(p["venue"])
            amount: Money = money(p["amount"])
            acct.credit(amount)
            acct.funding_paid += amount
        elif kind == LIQUIDATION:
            acct = state.account(p["venue"])
            realized_pnl: Money = money(p["realized_pnl"])
            acct.credit(realized_pnl)
            acct.realized_pnl += realized_pnl
            state.positions.pop((p["venue"], p["market"]), None)
        elif kind == ORDER_REJECTED:
            rec = orders.get(p["client_order_id"])
            if rec is not None:
                rec.transition(OrderStatus.REJECTED, reason=p.get("reason", ""))
        elif kind == ORDER_CANCELLED:
            rec = orders.get(p["client_order_id"])
            if rec is not None and not rec.is_terminal:
                rec.transition(OrderStatus.CANCELLED, reason=p.get("reason", ""))

    return BookState(accounts=state.accounts, positions=state.positions, orders=orders)


def warm_state(
    events: Iterable[Event], *, initial_capital: Money, venue: str
) -> PortfolioState:
    """Fold ``events`` into the §6.7 warm-start book: initial capital + deltas.

    ``fold`` rebuilds cash/positions as *deltas from zero* (fills, funding,
    liquidation); true state is ``initial_capital + deltas``. This is the one
    reconstruction both warm-start consumers share — the in-process
    ``PaperSession`` and the sandboxed paper-step child — so a resumed book can
    never disagree between the two paths. Working orders are deliberately not
    carried: the engine cancels them at run end (``run_ended``, §6.2), so a
    warm start begins with an empty order machine by contract.
    """
    book = fold(events)
    state = PortfolioState()
    state.account(venue).cash = initial_capital
    for v, acct in book.accounts.items():
        a = state.account(v)
        a.cash = a.cash + acct.cash
        a.fees_paid = acct.fees_paid
        a.funding_paid = acct.funding_paid
        a.realized_pnl = acct.realized_pnl
    state.positions.update(book.positions)
    return state
