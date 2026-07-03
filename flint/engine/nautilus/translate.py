"""``FlintRecorder`` — the single writer to the Flint EventLog (§A6).

Nautilus produces the run's *facts* (bars close, orders fill); Flint owns the
*accounting*. The recorder is a Nautilus ``Actor`` that sits inside the backtest,
maintains a **shadow book** (today's :class:`PortfolioState`) and the §6.2 order
state machine, and is the sole emitter into the injected :class:`EventLog`. One
writer means the stored event log and the shadow book cannot silently diverge, and
the existing ``fold``/``build_tearsheet``/``check_invariants`` machinery folds a
Nautilus run exactly as it folds a legacy run — ``tearsheet.py`` and ``portfolio/``
stay untouched (§A6).

Two channels feed the recorder, both landing in the same log:

* **The bar-lane shim calls it directly** to place / reject / cancel orders and to
  emit the per-bar equity snapshot — the order lifecycle events the legacy loop
  emits inline (ORDER_PLACED at route time, Flint-level rejects/cancels), in the
  legacy order, with the legacy payloads.
* **Nautilus order events reach it on the message bus** — an ``OrderFilled`` folds
  into the shadow book through the *same* ``apply_fill_delta`` reducer the legacy
  engine uses and emits a FILL v2 event byte-identical to the legacy engine's. The
  Flint fill facts ride on the order tag (:mod:`.execmodels`), so the recorded
  price/size/fee/ts are Flint's exact numbers, not Nautilus's rounded ones.

At run end :meth:`reconcile` asserts the shadow book agrees with Nautilus's own
account and positions (§19.4) — the two event models describe one run and must not
have drifted.
"""

from __future__ import annotations

from decimal import Decimal

from flint.core.models import Side
from flint.engine.money import ZERO, money
from flint.engine.orders import OrderRecord, OrderStatus
from flint.engine.portfolio import apply_fill_delta
from flint.engine.portfolio.events import (
    EQUITY,
    FILL,
    ORDER_CANCELLED,
    ORDER_PLACED,
    ORDER_REJECTED,
    RUN_FINISHED,
    RUN_STARTED,
)
from flint.engine.state import PortfolioState
from flint.engine.tearsheet import InvariantError

from ._compat import Actor, OrderFilled
from .execmodels import decode_fill_tag

# §19.4 run-end reconciliation tolerances. Cash is Decimal and matches to the cent;
# entry price and size can differ by the instrument's price/size rounding (Nautilus
# holds fixed-precision Price/Quantity; Flint holds raw floats), so they reconcile
# to a float tolerance rather than exactly (§19.4).
CASH_TOLERANCE = Decimal("0.01")
ENTRY_TOLERANCE = 1e-6
SIZE_TOLERANCE = 1e-9


class FlintRecorder(Actor):
    """Single EventLog writer + shadow book + order state machine (§A6)."""

    def __init__(
        self,
        *,
        event_log,
        shadow: PortfolioState,
        engine_name: str,
    ) -> None:
        super().__init__()
        # Attributes are ``_flint_``-prefixed: the Nautilus ``Component`` base
        # (Cython) reserves plain names like ``_log`` and forbids overwriting them.
        self._flint_log = event_log
        self._flint_shadow = shadow
        self._flint_engine_name = engine_name
        # The §6.2 order state machine, keyed by Flint client_order_id — folded from
        # the same records the legacy loop keeps, so check_invariants runs unchanged.
        self._flint_orders: dict[str, OrderRecord] = {}
        # Per-(venue, market) last mark for valuing open positions at a bar close;
        # the shim carries it forward each bar (the legacy loop does so in the
        # liquidation step, which is a SimulationModule here — N5).
        self._flint_last_mark: dict[tuple[str, str], float] = {}

    # -- run lifecycle ---------------------------------------------------------

    def on_start(self) -> None:
        self._flint_log.emit(RUN_STARTED, {"engine": self._flint_engine_name})
        # Listen for Nautilus order events; only OrderFilled is translated (bar-lane
        # rejects/cancels are Flint-level and arrive via the shim's direct calls).
        self.msgbus.subscribe(topic="events.order.*", handler=self._on_order_event)

    def emit_run_finished(self) -> None:
        """Emit RUN_FINISHED — called by the engine after run-end order cancels."""
        self._flint_log.emit(RUN_FINISHED, {"engine": self._flint_engine_name})

    # -- order lifecycle the shim drives (mirrors loop._place/_reject/_cancel) --

    def order_record(self, client_order_id: str) -> OrderRecord | None:
        """The live state-machine record for ``client_order_id`` (or ``None``)."""
        return self._flint_orders.get(client_order_id)

    def record_placed(self, order, **placed_extra) -> OrderRecord:
        """Accept an order onto the machine: pending → placed, ORDER_PLACED emitted.

        ``placed_extra`` carries USD-order provenance (``size_usd`` + sub-lot
        ``size_residual``) exactly as the legacy loop's ``_emit_placed`` does.
        """
        rec = OrderRecord(
            client_order_id=order.client_order_id,
            market=order.market,
            venue=order.venue,
            side=order.side,
            type=order.type,
            size=order.size,
            price=order.price,
        )
        self._flint_orders[order.client_order_id] = rec
        rec.transition(OrderStatus.PLACED)
        payload = {
            "client_order_id": order.client_order_id,
            "market": order.market,
            "venue": order.venue,
            "side": order.side,
            "type": order.type,
            "size": order.size,
            "price": order.price,
            "tif": order.tif,
            "reduce_only": order.reduce_only,
        }
        payload.update(placed_extra)
        self._flint_log.emit(ORDER_PLACED, payload)
        return rec

    def record_rejected(self, order, *, reason: str) -> None:
        """Carry ``order`` to the terminal ``rejected`` state and emit (§6.2)."""
        rec = self._flint_orders.get(order.client_order_id)
        if rec is None or rec.is_terminal:
            return
        rec.transition(OrderStatus.REJECTED, reason=reason)
        self._emit_order_terminal(ORDER_REJECTED, order, reason)

    def record_cancelled(self, order, *, reason: str) -> None:
        """Carry ``order`` to the terminal ``cancelled`` state and emit (§6.2)."""
        rec = self._flint_orders.get(order.client_order_id)
        if rec is None or rec.is_terminal:
            return
        rec.transition(OrderStatus.CANCELLED, reason=reason)
        self._emit_order_terminal(ORDER_CANCELLED, order, reason)

    def _emit_order_terminal(self, kind: str, order, reason: str) -> None:
        self._flint_log.emit(
            kind,
            {
                "client_order_id": order.client_order_id,
                "market": order.market,
                "venue": order.venue,
                "reason": reason,
            },
        )

    # -- fill translation (Nautilus OrderFilled → FILL v2, §A6) ----------------

    def _on_order_event(self, event) -> None:
        """Translate a Nautilus ``OrderFilled`` into a FILL v2 event + fold the book.

        Byte-identical to the legacy ``loop._apply_fill``: the fee and realized PnL
        move the shadow account, the position book folds through ``apply_fill_delta``
        (the same reducer ``replay.fold`` uses), the order's state machine advances,
        and the FILL payload carries Flint's exact numbers (from the order tag) plus
        the computed realized PnL. Non-fill order events (submitted/accepted) are
        ignored — the shim already emitted ORDER_PLACED at route time.
        """
        if not isinstance(event, OrderFilled):
            return
        order = self.cache.order(event.client_order_id)
        p = decode_fill_tag(order)
        if p is None:  # tick-lane native fill (no Flint tag) — N8
            raise InvariantError(
                "OrderFilled without a Flint fill tag — native tick-lane fills "
                "arrive in N8; the bar lane always tags its fills"
            )
        venue = p["venue"]
        market = p["market"]
        side = Side(p["side"])
        acct = self._flint_shadow.account(venue)
        acct.cash -= money(p["fee"])
        acct.fees_paid += money(p["fee"])
        realized = apply_fill_delta(
            self._flint_shadow.positions,
            (venue, market),
            side=side,
            size=p["size"],
            price=p["price"],
            margin_mode=p["margin_mode"],
        )
        if realized:
            acct.credit(money(realized))
            acct.realized_pnl += money(realized)
        rec = self._flint_orders.get(p["client_order_id"])
        if rec is not None and not rec.is_terminal:
            rec.apply_fill(p["size"])
        self._flint_log.emit(
            FILL,
            {
                "client_order_id": p["client_order_id"],
                "market": market,
                "venue": venue,
                "side": side,
                "price": p["price"],
                "size": p["size"],
                "fee": p["fee"],
                "liquidity": p["liquidity"],
                "fidelity_tier": p["fidelity_tier"],
                "slippage_bps": p["slippage_bps"],
                "is_partial": p["is_partial"],
                "margin_mode": p["margin_mode"],
                "realized_pnl": str(money(realized)),
                "intrabar_ambiguous": p["intrabar_ambiguous"],
                "flags": list(p["flags"]),
            },
            ts=p["ts"],
            event_version=2,
        )

    # -- per-bar equity (shim-driven) ------------------------------------------

    def set_mark(self, key: tuple[str, str], mark: float) -> None:
        """Carry the bar-closing mark for ``(venue, market)`` (equity valuation)."""
        self._flint_last_mark[key] = mark

    def emit_equity(self, ts: int) -> None:
        """Portfolio-wide EQUITY from the shadow book — byte-identical to ``loop._emit_equity``."""
        cash = ZERO
        accrued_funding = ZERO
        for acct in self._flint_shadow.accounts.values():
            cash += acct.cash
            accrued_funding += acct.funding_paid
        unrealized = 0.0
        for key, pos in self._flint_shadow.positions.items():
            mark = self._flint_last_mark.get(key)
            if mark is not None:
                unrealized += pos.unrealized_pnl(mark)
        unrealized_money = money(unrealized)
        self._flint_log.emit(
            EQUITY,
            {
                "equity": str(cash + unrealized_money),
                "unrealized": str(unrealized_money),
                "accrued_funding": str(accrued_funding),
                "cash": str(cash),
            },
            ts=ts,
        )

    # -- run-end reconciliation (§19.4) ---------------------------------------

    def reconcile(self, *, portfolio, cache, venue, settlement_currency) -> None:
        """Assert the shadow book agrees with Nautilus's own account/positions (§19.4).

        Two event models describe one run; this closes the loop at run end. Shadow
        cash must equal the Nautilus account balance (within :data:`CASH_TOLERANCE`),
        and every open position must match on side/size (:data:`SIZE_TOLERANCE`) and
        entry price (:data:`ENTRY_TOLERANCE`). A mismatch is an engine bug and raises
        :class:`InvariantError`.
        """
        shadow_cash = sum((a.cash for a in self._flint_shadow.accounts.values()), ZERO)
        account = portfolio.account(venue)
        nautilus_cash = account.balance_total(settlement_currency).as_decimal()
        if abs(shadow_cash - nautilus_cash) > CASH_TOLERANCE:
            raise InvariantError(
                f"shadow cash {shadow_cash} != Nautilus balance {nautilus_cash} "
                f"(tolerance {CASH_TOLERANCE}) at run end"
            )
        shadow_open = {
            market: pos
            for (_, market), pos in self._flint_shadow.positions.items()
            if pos.size != 0
        }
        nautilus_open = {p.instrument_id.symbol.value: p for p in cache.positions_open()}
        if set(shadow_open) != set(nautilus_open):
            raise InvariantError(
                f"shadow open positions {sorted(shadow_open)} != Nautilus open "
                f"positions {sorted(nautilus_open)} at run end"
            )
        for market, pos in shadow_open.items():
            np = nautilus_open[market]
            shadow_signed = pos.side.sign * pos.size
            naut_signed = float(np.signed_qty)
            if abs(shadow_signed - naut_signed) > SIZE_TOLERANCE:
                raise InvariantError(
                    f"{market}: shadow signed size {shadow_signed} != Nautilus "
                    f"{naut_signed} at run end"
                )
            if abs(pos.entry_price - float(np.avg_px_open)) > ENTRY_TOLERANCE:
                raise InvariantError(
                    f"{market}: shadow entry {pos.entry_price} != Nautilus "
                    f"{float(np.avg_px_open)} (tolerance {ENTRY_TOLERANCE}) at run end"
                )


__all__ = ["FlintRecorder", "CASH_TOLERANCE", "ENTRY_TOLERANCE", "SIZE_TOLERANCE"]
