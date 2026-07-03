"""Funding settlement — the payment one settlement moves, priced honestly (§6.4).

The funding engine is deliberately split across three pure primitives so the loop
(``_settle_funding``) can compose them without knowing venue law:

* ``clamp_funding_rate`` applies the venue's ``rate_cap_hourly`` (HL: 4%/hr — see
  ``VenueSpec``), because an uncapped fixture rate would settle an impossible
  payment. Returns whether it bit, so the event log can show it.
* ``settlement_price_for`` picks the price a settlement is charged on from the
  ``price_basis`` on the row: the oracle/index for HL, the mark for Binance
  (§2.2 trap). It reads the last mark snapshot at-or-before the settlement second
  — and **hard-fails when there is none** rather than silently pricing on the bar
  close, the exact error §6.4 warns against ("not the bar close, which can differ
  by percent in a fast market").
* ``settlement_payment`` computes the signed ``Decimal`` payment for one
  settlement, scaling the hourly-normalized rate by the settlement's own
  ``interval_s`` (HL hourly → ×1; Binance 8h → ×8).

Sign convention (§6.4): a **positive** rate means longs pay shorts, so the
payment *to* a position is::

    payment = -side.sign * size * settlement_price * rate * (interval_s / 3600)

A long (sign +1) with a positive rate pays (negative payment); a long with a
negative rate receives. Only ``final`` rates ever settle — the ``predicted``
series is strategy-visible via ``ctx.funding_rate`` and never becomes a payment
(§6.4 predicted/final contract); the loop enforces that filter and this module
refuses a non-final rate as a backstop.
"""

from __future__ import annotations

from flint.core.models import FundingRate, MarkSnapshot, Position
from flint.core.time import last_before

from ..money import Money, money


class FundingCoverageError(Exception):
    """A funding settlement cannot be settled/priced honestly (§6.4, §16).

    Raised rather than quietly under-charging: settling a non-``final`` rate, or
    pricing an oracle-basis settlement with no oracle snapshot to read (which
    would otherwise fall back to the bar close — the §6.4 error), is a hard stop.
    A missing-coverage backtest must fail loud, not report an optimistic curve.
    """


def clamp_funding_rate(rate_hourly: float, cap_hourly: float) -> tuple[float, bool]:
    """Clamp ``rate_hourly`` to ``±cap_hourly``; return (rate, whether it bit).

    ``cap_hourly`` is venue law from the ``VenueSpec`` (HL: 0.04 = 4%/hr, cited).
    The bool lets the settlement event record that a cap was applied — a clamped
    settlement is a modelling fact the tearsheet should be able to surface.
    """
    if rate_hourly > cap_hourly:
        return cap_hourly, True
    if rate_hourly < -cap_hourly:
        return -cap_hourly, True
    return rate_hourly, False


def settlement_price_for(rate: FundingRate, marks: list[MarkSnapshot]) -> float:
    """The price a settlement is charged on, per its ``price_basis`` (§6.4, §2.2).

    HL oracle-basis funding prices on the **index/oracle**; Binance mark-basis on
    the **mark** — pricing the wrong one is the §2.2 trap. The snapshot is the last
    one at-or-before the settlement second. No snapshot → ``FundingCoverageError``:
    the honest engine will not silently fall back to the bar close (§6.4).
    """
    snap = last_before(marks, rate.ts + 1, key=lambda m: m.ts)
    if snap is None:
        raise FundingCoverageError(
            f"no mark snapshot at/before funding settlement ts={rate.ts} for "
            f"{rate.market} on {rate.venue}: cannot price {rate.price_basis}-basis "
            "funding honestly (refusing to fall back to the bar close, §6.4)"
        )
    return snap.index_price if rate.price_basis == "oracle" else snap.mark_price


def settlement_payment(
    position: Position,
    rate_hourly: float,
    settlement_price: float,
    interval_s: int = 3600,
) -> Money:
    """The signed ``Decimal`` funding payment to ``position`` for one settlement.

    ``rate_hourly`` is normalized to 1h (§2.2); a settlement covers ``interval_s``
    seconds, so the settled fraction scales by ``interval_s / 3600`` — HL (hourly)
    by 1, Binance (8h) by 8. ``settlement_price`` is the oracle or mark price per
    ``price_basis`` (see ``settlement_price_for``). Positive = the position
    receives; negative = it pays. Computed in ``Decimal`` because it is a monetary
    accumulator (§5) — the golden funding example (§6.4) depends on the value
    being exact, not float-noisy.
    """
    interval_h = money(interval_s) / money(3600)
    return (
        money(-position.side.sign)
        * money(position.size)
        * money(settlement_price)
        * money(rate_hourly)
        * interval_h
    )
