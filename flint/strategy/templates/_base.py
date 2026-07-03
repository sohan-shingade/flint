"""Shared scaffolding for the built-in templates (§8.4).

Two things every template wants: a ``venue`` + ``notional_usd`` param pair, and a
declare-your-target-state helper that turns "I want to be long/short/flat" into the
*minimal* valid Signals given the current position. Templates therefore never call
``ctx.submit_order`` (that stays the imperative escape hatch, §8.1) — they express a
desired state and let :func:`rebalance_signals` emit a reduce-only close and/or a
sized open. Distinct actions on one ``(venue, market)`` are separate signal keys, so
a flip (``close`` + ``short``) passes the engine's one-per-bar validation (§8.1).
"""

from __future__ import annotations

from typing import Any

from flint.core.models import Side, Signal

from ..base import Strategy


def template_params(**extra: Any) -> dict[str, Any]:
    """The common template knobs (``venue``, ``notional_usd``) plus a template's own.

    ``Strategy.__init__`` copies ``type(self).params`` verbatim, so each template must
    declare the full set — this keeps the shared defaults in one place instead.
    """
    return dict(venue="hyperliquid", notional_usd=1000.0, **extra)


def rebalance_signals(
    ctx: Any, market: str, venue: str, desired: str, notional_usd: float
) -> list[Signal]:
    """Return the minimal signals to move ``(venue, market)`` to ``desired`` state.

    ``desired`` is ``"long"``/``"short"``/``"flat"``. If already there, ``[]`` (no
    churn). Otherwise close whatever is held (reduce-only, full size) and/or open the
    new side sized in USD at the execution bar's open (§8.1 rule 1).
    """
    pos = ctx.position(market, venue)
    current = "flat" if pos is None else ("long" if pos.side == Side.LONG else "short")
    if desired == current:
        return []
    signals: list[Signal] = []
    if current != "flat":
        signals.append(Signal.close(market, venue))
    if desired == "long":
        signals.append(Signal.long(market, venue, size_usd=notional_usd))
    elif desired == "short":
        signals.append(Signal.short(market, venue, size_usd=notional_usd))
    return signals


class TemplateStrategy(Strategy):
    """Base for the non-ML built-in templates: ``venue``/``notional`` + a rebalance seam.

    Subclasses compute a desired state in ``on_candle`` and return
    ``self._rebalance(market, ctx, desired)`` — the position-aware, always-valid
    Signal list. They never touch ``ctx.submit_order``.
    """

    params = template_params()

    def _rebalance(self, market: str, ctx: Any, desired: str) -> list[Signal]:
        return rebalance_signals(
            ctx, market, self.params["venue"], desired, self.params["notional_usd"]
        )
