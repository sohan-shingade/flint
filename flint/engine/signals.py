"""Signal→order conversion + size_usd materialization — the bar lane's pure routing (§8.1).

Extracted from the legacy loop in N1 (§6.0, D29) as the **one** implementation of
the §8.1 conversion rules; since N10 the Nautilus bar-lane shim is its only caller
(the legacy loop was deleted). This module is pure: no engine state, no I/O, no
order state machine. Signals in, an ordered list of order requests / deferred USD
intents out — the caller owns coid assignment, submission, and the state machine, so
the routing's exact ordering (and therefore its determinism) is preserved.

The three §8.1 rules this owns:

* **rule 3 — close.** A ``close`` maps to a reduce-only, full-size market order on
  the opposite side (or a no-op when there is no position to close).
* **rule 4/5 — loud validation.** A duplicate ``(venue, market, action)`` in one
  bar, or an open with neither / both of ``size``/``size_usd``, raises
  :class:`SignalValidationError` — a validation error, never a silent merge.
* **rule 1 — size_usd deferral.** A ``size_usd`` order cannot be sized without the
  execution bar's open (sizing on the decision bar's close is a hidden look-ahead),
  so it defers as a :class:`_UsdIntent`; :func:`materialize_usd_intent` sizes it to
  base units at that open, flooring to the venue lot and recording the sub-lot
  residual (never grown past the USD budget).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from flint.core.models import OrderType, Position, Side, Signal, TimeInForce


class SignalValidationError(ValueError):
    """A strategy emitted an invalid Signal batch (§8.1 conversion rules)."""


@dataclass(frozen=True)
class _UsdIntent:
    """A deferred size_usd order awaiting its execution bar's open (§8.1 rule 1).

    Held between the decision bar (where the strategy emitted it) and the next
    matching bar, where :func:`materialize_usd_intent` sizes it to base units at that
    bar's open — closing the look-ahead that sizing on the decision close would open.
    """

    market: str
    venue: str
    side: Side
    size_usd: float
    limit_price: float
    tif: TimeInForce | None
    margin_mode: str


@dataclass(frozen=True)
class OrderRequest:
    """A resolved order the caller will build (assigning a coid) and submit.

    Carries exactly the fields the legacy loop passed to its ``_new_order`` factory
    — no ``client_order_id`` (the caller assigns it), so routing stays pure and the
    coid sequence is unchanged. :meth:`as_order_kwargs` reproduces the legacy
    keyword call verbatim; omitted fields default to the :class:`Order` defaults,
    which the explicit values here match, so order construction is byte-identical.
    """

    market: str
    venue: str
    side: Side
    type: OrderType
    size: float
    price: float
    tif: TimeInForce | None
    margin_mode: str
    reduce_only: bool = False

    def as_order_kwargs(self) -> dict:
        return {
            "market": self.market,
            "venue": self.venue,
            "side": self.side,
            "type": self.type,
            "size": self.size,
            "price": self.price,
            "tif": self.tif,
            "margin_mode": self.margin_mode,
            "reduce_only": self.reduce_only,
        }


def route_signals(
    signals: list[Signal],
    default_venue: str,
    position_of: Callable[[str, str], Position | None],
) -> list[OrderRequest | _UsdIntent]:
    """Convert a bar's signals to order requests / deferred USD intents (§8.1).

    ``position_of(venue, market)`` returns the open position (or ``None``) — the
    only state this pure router needs, injected so it never touches the engine.
    Validation is loud (§8.1 rule 4/5): a duplicate ``(venue, market, action)`` in
    one bar, or an open with no sizing / both sizings, raises
    :class:`SignalValidationError`. A ``close`` maps to a reduce-only full-size
    market order (rule 3); a ``size`` (base) order is emitted immediately; a
    ``size_usd`` order defers to its execution bar as a :class:`_UsdIntent` (rule 1).
    The returned list preserves signal order, so the caller assigns coids exactly as
    the legacy loop did.
    """
    decisions: list[OrderRequest | _UsdIntent] = []
    seen: set[tuple[str, str, str]] = set()
    for sig in signals:
        venue = sig.venue or default_venue
        key = (venue, sig.market, sig.action)
        if key in seen:
            raise SignalValidationError(
                f"duplicate signal {key} in one bar — a validation error, "
                "not a merge (§8.1)"
            )
        seen.add(key)

        if sig.is_close:
            pos = position_of(venue, sig.market)
            if pos is None:
                continue  # nothing to close — a no-op, not an order
            decisions.append(
                OrderRequest(
                    market=sig.market,
                    venue=venue,
                    side=pos.side.opposite,
                    type=OrderType.MARKET,
                    size=pos.size,
                    price=0.0,
                    tif=TimeInForce.IOC,
                    margin_mode=pos.margin_mode,
                    reduce_only=True,
                )
            )
            continue

        if (sig.size > 0) == (sig.size_usd > 0):
            raise SignalValidationError(
                f"{sig.action} {sig.market} needs exactly one of size / "
                f"size_usd (got size={sig.size}, size_usd={sig.size_usd}) (§8.1)"
            )
        side = Side.LONG if sig.action == "long" else Side.SHORT
        if sig.size_usd > 0:
            # Defer: the base size is unknowable without the execution bar's open,
            # and sizing on this bar's close would be a look-ahead (§8.1).
            decisions.append(
                _UsdIntent(
                    market=sig.market,
                    venue=venue,
                    side=side,
                    size_usd=sig.size_usd,
                    limit_price=sig.limit_price,
                    tif=sig.tif,
                    margin_mode=sig.margin_mode,
                )
            )
            continue
        decisions.append(
            OrderRequest(
                market=sig.market,
                venue=venue,
                side=side,
                type=OrderType.LIMIT if sig.limit_price > 0 else OrderType.MARKET,
                size=sig.size,
                price=sig.limit_price,
                tif=sig.tif
                or (TimeInForce.GTC if sig.limit_price > 0 else TimeInForce.IOC),
                margin_mode=sig.margin_mode,
                reduce_only=False,
            )
        )
    return decisions


def round_lot(size_base: float, size_decimals: int) -> tuple[float, float]:
    """Floor ``size_base`` to the venue lot; return (lot_size, sub-lot residual)."""
    factor = 10**size_decimals
    lot_size = math.floor(size_base * factor) / factor
    return lot_size, size_base - lot_size


def materialize_usd_intent(
    intent: _UsdIntent, open_price: float, size_decimals: int
) -> tuple[OrderRequest, float]:
    """Size a deferred USD intent to base units at the execution bar's OPEN (§8.1).

    Base size = ``size_usd / open``, floored to the venue lot (never grown past the
    USD budget); returns the order request plus the sub-lot residual the caller
    records on ORDER_PLACED (never silently absorbed). A request whose ``size``
    rounds to zero is the caller's cue to place-then-reject it (no size). Market
    intents join the fill queue; limit intents rest.
    """
    base, residual = round_lot(intent.size_usd / open_price, size_decimals)
    order = OrderRequest(
        market=intent.market,
        venue=intent.venue,
        side=intent.side,
        type=OrderType.LIMIT if intent.limit_price > 0 else OrderType.MARKET,
        size=base,
        price=intent.limit_price,
        tif=intent.tif
        or (TimeInForce.GTC if intent.limit_price > 0 else TimeInForce.IOC),
        margin_mode=intent.margin_mode,
        reduce_only=False,
    )
    return order, residual
