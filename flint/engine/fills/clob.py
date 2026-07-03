"""``ClobFillModel`` — the honest central-limit-order-book fill (§6.3).

This is the wedge: the place freqtrade/backtrader lie and lose perp traders. The
model never fills at mid or at the bar close; it does what the venue does, bounded
by the data available, and records the **fidelity tier** it achieved per fill so a
coarse fill is never mistaken for a faithful one.

Tier is chosen by the data in the ``FillContext`` (§6.3):

* **Tier A** — book *and* trade prints: taker orders walk real depth (VWAP,
  partial/reject per TIF); maker orders fill only after recorded trade volume
  clears the queue ahead of them.
* **Tier B** — book only: same book-walk for takers; maker fills are
  probabilistic from a volume-at-price proxy, flagged ``estimated``.
* **Tier C** — no book (or a stale one): next-bar-open ± a parametric half-spread
  + square-root impact, flagged ``uncalibrated``; a zero-volume bar is rejected
  (no liquidity evidence to invent from — D26).

Taker specifics (§6.3): the order crosses the spread (a buy lifts the ask, a sell
hits the bid), walks depth for a VWAP with ``slippage_bps`` recorded against the
arrival touch, honours the Hyperliquid **oracle band** (depth beyond the band is
unavailable → clip + partial), and pays the taker fee. A limit that crosses on
arrival is a **taker**; one that rests is a **maker** — recorded per fill, because
HL's maker/taker spread is ~3× and misclassifying it flatters limit strategies.
"""

from __future__ import annotations

import math

from flint.core.models import Fill, Order, OrderType, Side, TimeInForce

from ..money import money
from .base import FillContext, FillModel, FillResult

_EPS = 1e-12


def _round_sig(x: float, figs: int) -> float:
    """Round ``x`` to ``figs`` significant figures (HL price rule); 0 = no-op."""
    if figs <= 0 or x == 0.0:
        return x
    decimals = figs - 1 - math.floor(math.log10(abs(x)))
    return round(x, decimals)


class ClobFillModel(FillModel):
    """CLOB fills across fidelity tiers A/B/C, chosen by available data (§6.3)."""

    def fill(self, order: Order, ctx: FillContext) -> FillResult | None:
        tier = self._tier(ctx)
        if tier == "C":
            return self._tier_c(order, ctx)
        if self._is_taker(order, ctx):
            return self._taker(order, ctx, tier)
        return self._maker(order, ctx, tier)

    # -- tier + maker/taker classification -------------------------------------

    @staticmethod
    def _tier(ctx: FillContext) -> str:
        if ctx.book is None or ctx.stale_book:
            return "C"
        return "A" if ctx.trades else "B"

    @staticmethod
    def _is_taker(order: Order, ctx: FillContext) -> bool:
        """A market/stop/TP is always taker; a limit is taker only if it crosses."""
        if order.type is not OrderType.LIMIT:
            return True
        book = ctx.book
        assert book is not None  # tier != C guarantees a book
        if order.side is Side.LONG:
            return bool(book.asks) and order.price >= book.asks[0][0]
        return bool(book.bids) and order.price <= book.bids[0][0]

    # -- taker: cross the spread, walk depth, oracle-band clip -----------------

    def _taker(self, order: Order, ctx: FillContext, tier: str) -> FillResult | None:
        book = ctx.book
        assert book is not None
        buy = order.side is Side.LONG
        levels = list(book.asks if buy else book.bids)  # best-first (price, size)
        if not levels:
            return None
        touch = levels[0][0]
        band_hi, band_lo = self._band(ctx)

        remaining = order.size
        notional = 0.0
        filled = 0.0
        clipped = False
        for price, avail in levels:
            if buy and band_hi is not None and price > band_hi:
                clipped = True
                break
            if not buy and band_lo is not None and price < band_lo:
                clipped = True
                break
            take = min(remaining, avail)
            if take <= 0:
                break
            notional += price * take
            filled += take
            remaining -= take
            if remaining <= _EPS:
                break

        if filled <= 0:
            return None  # touch already beyond the oracle band, or no depth
        vwap = notional / filled
        is_partial = filled < order.size - _EPS
        if is_partial and order.tif is TimeInForce.FOK:
            return None  # fill-or-kill: all or nothing

        slippage_bps = abs(vwap - touch) / touch * 1e4
        price = _round_sig(vwap, ctx.price_sig_figs)
        fee = float(money(price) * money(filled) * money(ctx.taker_fee_rate))
        fill = Fill(
            market=order.market,
            side=order.side,
            price=price,
            size=filled,
            fee=fee,
            ts=ctx.effective_ts or ctx.ts,
            client_order_id=order.client_order_id,
            is_partial=is_partial,
            slippage_bps=slippage_bps,
            venue=order.venue,
            liquidity="taker",
            fidelity_tier=tier,
        )
        return FillResult(fill=fill, flags=("oracle_band_clipped",) if clipped else ())

    # -- maker: queue-position fill (Tier A trades / Tier B proxy) -------------

    def _maker(self, order: Order, ctx: FillContext, tier: str) -> FillResult | None:
        buy = order.side is Side.LONG
        limit = order.price
        if tier == "A":
            cleared = sum(
                t.size
                for t in ctx.trades
                if (t.price <= limit if buy else t.price >= limit)
            )
            flags: tuple[str, ...] = ()
        else:  # Tier B — probabilistic from a volume-at-price proxy, flagged
            cleared = ctx.volume_at_price
            flags = ("estimated",)
        filled = min(max(0.0, cleared - ctx.queue_ahead), order.size)
        if filled <= _EPS:
            return None  # queue not cleared → the order rests (loop keeps it)

        price = _round_sig(limit, ctx.price_sig_figs)
        fee = float(money(price) * money(filled) * money(ctx.maker_fee_rate))
        fill = Fill(
            market=order.market,
            side=order.side,
            price=price,
            size=filled,
            fee=fee,
            ts=ctx.effective_ts or ctx.ts,
            client_order_id=order.client_order_id,
            is_partial=filled < order.size - _EPS,
            slippage_bps=0.0,
            venue=order.venue,
            liquidity="maker",
            fidelity_tier=tier,
        )
        return FillResult(fill=fill, flags=flags)

    # -- Tier C: parametric spread + square-root impact ------------------------

    def _tier_c(self, order: Order, ctx: FillContext) -> FillResult | None:
        if ctx.bar_dollar_volume <= 0:
            return None  # zero-volume bar: no liquidity evidence to invent (D26)
        anchor = ctx.reference_price
        if anchor <= 0:
            return None
        buy = order.side is Side.LONG
        notional = anchor * order.size
        impact_bps = ctx.impact_k * math.sqrt(notional / ctx.bar_dollar_volume) * 1e4
        total_bps = ctx.half_spread_bps + impact_bps
        sign = 1.0 if buy else -1.0
        price = _round_sig(anchor * (1 + sign * total_bps / 1e4), ctx.price_sig_figs)
        fee = float(money(price) * money(order.size) * money(ctx.taker_fee_rate))
        flags = ("uncalibrated",)
        if ctx.stale_book:
            flags = flags + ("stale_book",)
        fill = Fill(
            market=order.market,
            side=order.side,
            price=price,
            size=order.size,
            fee=fee,
            ts=ctx.effective_ts or ctx.ts,
            client_order_id=order.client_order_id,
            is_partial=False,
            slippage_bps=total_bps,
            venue=order.venue,
            liquidity="taker",  # Tier-C placement-bar execution is taker (§6.3)
            fidelity_tier="C",
        )
        return FillResult(fill=fill, flags=flags)

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _band(ctx: FillContext) -> tuple[float | None, float | None]:
        """The oracle price band (hi, lo), or (None, None) if not enforced."""
        if ctx.oracle_band_bps <= 0 or ctx.oracle_price <= 0:
            return None, None
        band = ctx.oracle_band_bps / 1e4
        return ctx.oracle_price * (1 + band), ctx.oracle_price * (1 - band)
