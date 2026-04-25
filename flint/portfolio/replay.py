"""Time-travel replay over the portfolio event log.

D-6.4-replay second slice (snapshot store comes in slice 3). Folds the
event stream produced by `EventLogWriter` back into a `BookState` —
the position book + cash ledger + counters at any target_ts.

State is reconstructed from `seq=0` for now; snapshot compaction
(slice 3) will let large sessions skip the early events and start
from the latest snapshot before `target_ts`.

Event payload contract (matches what the engine writer hooks will emit
in slice 4):

    fill:        {market, venue, side, size, price, fee, tx_cost,
                  is_open}     -- is_open flag tells the folder whether
                                  this fill opens, adds to, or closes
    funding:     {market, venue, payment}
    liquidation: {market, venue, side, size, entry_price, liq_price,
                  loss, penalty}
    borrow:      {market, cost}
    order.submit / order.cancel: opaque metadata; not folded into book
                                 state (counters only).

Folding is intentionally permissive — unknown payload keys are ignored
so old logs still replay against a newer schema (forward compat).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Tuple

from .event_log import EventKind, EventLogReader, PortfolioEvent


@dataclass
class _ReplayPosition:
    """Lite per-(venue, market) position used during fold."""
    market: str
    venue: str
    side: str           # "long" | "short"
    size: float         # always positive; flip recorded as side change
    entry_price: float


@dataclass
class BookState:
    """Snapshot of the book at a point in time. Returned by `replay`."""
    cash: float
    initial_capital: float
    total_fees: float = 0.0
    total_tx_costs: float = 0.0
    total_funding: float = 0.0
    total_borrow_paid: float = 0.0
    positions: Dict[Tuple[str, str], _ReplayPosition] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fill_count: int = 0
    liquidation_count: int = 0
    order_submit_count: int = 0
    order_cancel_count: int = 0

    def equity_at(self, prices: Dict[str, float]) -> float:
        """Compute equity given a (market → mark price) map. Positions
        without a price contribute 0 unrealized PnL."""
        unrealized = 0.0
        for pos in self.positions.values():
            mark = prices.get(pos.market)
            if mark is None:
                continue
            if pos.side == "long":
                unrealized += (mark - pos.entry_price) * pos.size
            else:
                unrealized += (pos.entry_price - mark) * pos.size
        return self.cash + unrealized


def replay(
    store,
    session_id: str,
    target_ts: int,
    initial_capital: float,
) -> BookState:
    """Reconstruct `BookState` for `session_id` at `target_ts`.

    Reads every event with `ts <= target_ts` from the log and folds
    them into a fresh `BookState` seeded with `initial_capital`. Slice 3
    will accept an optional `from_snapshot=BookState` to short-circuit
    the early-event scan.
    """
    reader = EventLogReader(store)
    events = reader.read_until(session_id, target_ts)
    return fold(events, initial_capital)


def fold(events: Iterable[PortfolioEvent], initial_capital: float) -> BookState:
    """Pure-Python fold over an event stream. Exposed separately so
    tests + the future snapshot compactor can call it without a store."""
    state = BookState(cash=initial_capital, initial_capital=initial_capital)
    for ev in events:
        _apply_event(state, ev)
    return state


def _apply_event(state: BookState, ev: PortfolioEvent) -> None:
    kind = ev.kind
    p = ev.payload
    if kind == EventKind.FILL:
        _apply_fill(state, p)
    elif kind == EventKind.FUNDING:
        _apply_funding(state, p)
    elif kind == EventKind.LIQUIDATION:
        _apply_liquidation(state, p)
    elif kind == EventKind.BORROW:
        _apply_borrow(state, p)
    elif kind == EventKind.ORDER_SUBMIT:
        state.order_submit_count += 1
    elif kind == EventKind.ORDER_CANCEL:
        state.order_cancel_count += 1
    # Unknown kinds are intentionally ignored — forward compat.


def _apply_fill(state: BookState, p: Dict[str, Any]) -> None:
    state.fill_count += 1
    market = p["market"]
    venue = p.get("venue", "default")
    side = p["side"]              # "long" | "short"
    size = float(p["size"])
    price = float(p["price"])
    fee = float(p.get("fee", 0.0))
    tx_cost = float(p.get("tx_cost", 0.0))

    state.cash -= fee + tx_cost
    state.total_fees += fee
    state.total_tx_costs += tx_cost

    key = (venue, market)
    pos = state.positions.get(key)

    if pos is None:
        # Open
        state.positions[key] = _ReplayPosition(
            market=market, venue=venue, side=side, size=size, entry_price=price,
        )
        return

    if pos.side == side:
        # DCA
        total_cost = pos.entry_price * pos.size + price * size
        pos.size += size
        pos.entry_price = total_cost / pos.size if pos.size > 0.0001 else price
        return

    # Opposite — reduce / close / flip
    if size >= pos.size:
        # Full close (and possibly flip remainder)
        if pos.side == "long":
            pnl = (price - pos.entry_price) * pos.size
        else:
            pnl = (pos.entry_price - price) * pos.size
        state.cash += pnl
        state.realized_pnl += pnl
        remainder = size - pos.size
        del state.positions[key]
        if remainder > 0.0001:
            state.positions[key] = _ReplayPosition(
                market=market, venue=venue, side=side, size=remainder, entry_price=price,
            )
        return

    # Partial close
    if pos.side == "long":
        pnl = (price - pos.entry_price) * size
    else:
        pnl = (pos.entry_price - price) * size
    state.cash += pnl
    state.realized_pnl += pnl
    pos.size -= size


def _apply_funding(state: BookState, p: Dict[str, Any]) -> None:
    payment = float(p.get("payment", 0.0))
    state.cash -= payment
    state.total_funding += payment


def _apply_liquidation(state: BookState, p: Dict[str, Any]) -> None:
    state.liquidation_count += 1
    market = p["market"]
    venue = p.get("venue", "default")
    loss = float(p.get("loss", 0.0))
    penalty = float(p.get("penalty", 0.0))
    state.cash += loss      # event.loss is signed (negative for losses)
    state.total_fees += penalty
    state.positions.pop((venue, market), None)


def _apply_borrow(state: BookState, p: Dict[str, Any]) -> None:
    cost = float(p.get("cost", 0.0))
    state.cash -= cost
    state.total_borrow_paid += cost


__all__ = ["BookState", "fold", "replay"]
