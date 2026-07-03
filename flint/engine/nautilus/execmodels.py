"""Making Nautilus record a fill at *Flint's* price and fee (§A7, §6.3).

The bar lane's contract is that **Flint's fill models price every fill** — Tier-C
parametric and Tier-A/B book-walk alike — and Nautilus is only the substrate that
holds the account and positions so the run-end reconciliation can cross-check
Flint's accounting. But Nautilus's own bar matching would fill a market order at
the bar *close*, not at Flint's slippage-adjusted number. Two small pluggable
models close that gap:

* :class:`FlintPriceFillModel` — a Nautilus ``FillModel`` whose
  ``get_orderbook_for_fill_simulation`` returns a synthetic one-level book pinned
  to the price Flint computed (carried on the order's tags). A marketable order
  then fills at exactly that price and size, so Nautilus's position/entry mirror
  Flint's to the tick.
* :class:`FlintTagFeeModel` — a Nautilus ``FeeModel`` that returns the exact fee
  Flint computed (also tag-carried), so Nautilus's account commission equals
  Flint's fee to the cent regardless of the maker/taker side Nautilus would assign
  to the aggressive order. This is the "configure the venue so Nautilus's fee
  equals Flint's" resolution (plan §A7 fee handling) — the instrument's own
  maker/taker rates are left unused.

The single channel both models (and the recorder) read from is the JSON blob under
the :data:`FLINT_FILL_TAG` order tag; :func:`encode_fill_tag` / :func:`decode_fill_tag`
are its codec. Floats round-trip exactly through JSON (Python emits ``repr``), so
the recorded FILL is byte-identical to the legacy engine's.
"""

from __future__ import annotations

import json
from typing import Any

from ._compat import (
    USDC,
    BookOrder,
    BookType,
    FeeModel,
    FillModel,
    MakerTakerFeeModel,
    Money,
    OrderBook,
    OrderSide,
    Price,
    Quantity,
)

# The order tag key under which the whole Flint fill payload rides to the fill
# model, fee model, and recorder. One tag, one JSON object — see module docstring.
FLINT_FILL_TAG = "flint_fill"

# Synthetic-book depth: large enough to fill any single Flint order in full at the
# pinned price (Flint has already sized the order to what it decided fills).
_UNLIMITED = 1_000_000


def encode_fill_tag(payload: dict[str, Any]) -> str:
    """Encode a Flint fill payload as the ``flint_fill=<json>`` order tag."""
    return f"{FLINT_FILL_TAG}={json.dumps(payload)}"


def decode_fill_tag(order: Any) -> dict[str, Any] | None:
    """Recover the Flint fill payload from an order's tags, or ``None`` if absent.

    Absent means this is not a Flint-priced bar-lane fill (e.g. a future tick-lane
    native fill), and the caller falls back to the Nautilus event's own numbers.
    """
    for tag in order.tags or []:
        if tag.startswith(FLINT_FILL_TAG + "="):
            return json.loads(tag[len(FLINT_FILL_TAG) + 1 :])
    return None


class FlintPriceFillModel(FillModel):
    """Fill every order at the exact price Flint computed (bar lane, §A7).

    Returns a one-level synthetic book with the opposite side resting at the
    tag-carried Flint price, so a marketable order lifts it and fills at that price.
    A stray order without the Flint tag returns ``None`` (Nautilus's default logic),
    which is what the tick lane's native matching will use in N8.
    """

    def fill_limit_inside_spread(self) -> bool:
        # Allow fills at the pinned price even when it sits inside the bar-derived
        # spread — the pinned book *is* the liquidity for a Flint-priced fill.
        return True

    def get_orderbook_for_fill_simulation(self, instrument, order, best_bid, best_ask):
        payload = decode_fill_tag(order)
        if payload is None:
            return None  # not a Flint-priced order — defer to native matching (N8)
        price = Price.from_str(f"{float(payload['price']):.{instrument.price_precision}f}")
        book = OrderBook(instrument_id=instrument.id, book_type=BookType.L2_MBP)
        size = Quantity(_UNLIMITED, instrument.size_precision)
        # A BUY lifts an ask pinned at the Flint price; a SELL hits a bid there.
        resting_side = OrderSide.SELL if order.side == OrderSide.BUY else OrderSide.BUY
        book.add(BookOrder(side=resting_side, price=price, size=size, order_id=1), 0, 0)
        return book


class FlintTagFeeModel(FeeModel):
    """Charge exactly the fee Flint computed for the fill (bar lane, §A7).

    Reads the fee off the order's Flint tag so Nautilus's account commission equals
    Flint's fee to the cent — the maker/taker side Nautilus assigns to the
    aggressive order is irrelevant. An order without the tag pays nothing (the
    tick lane will supply its own fee model in N8).
    """

    def get_commission(self, order, fill_qty, fill_px, instrument):
        payload = decode_fill_tag(order)
        fee = float(payload["fee"]) if payload is not None else 0.0
        return Money(fee, USDC)


class TickFeeModel(FeeModel):
    """The tick lane's fee model: native maker/taker, zero for liquidation closes (§A7/N8).

    Native tick fills (untagged) are charged Nautilus's own maker/taker commission
    from the instrument's fee rates (delegated to :class:`MakerTakerFeeModel`), which
    the recorder then reads off the ``OrderFilled`` event so both books charge the
    same fee to the cent. The only *tagged* order the tick lane submits is the §6.5
    liquidation flatten, pinned to entry with ``fee=0`` so ``adjust_account`` stays
    the sole money mover — that tag's fee (``0``) is honored verbatim, and the
    instrument's maker/taker is left unused for it.
    """

    def __init__(self) -> None:
        super().__init__()
        self._flint_maker_taker = MakerTakerFeeModel()

    def get_commission(self, order, fill_qty, fill_px, instrument):
        payload = decode_fill_tag(order)
        if payload is not None:  # a Flint-tagged order (the liquidation flatten)
            return Money(float(payload["fee"]), USDC)
        return self._flint_maker_taker.get_commission(
            order, fill_qty, fill_px, instrument
        )


__all__ = [
    "FLINT_FILL_TAG",
    "encode_fill_tag",
    "decode_fill_tag",
    "FlintPriceFillModel",
    "FlintTagFeeModel",
    "TickFeeModel",
]
