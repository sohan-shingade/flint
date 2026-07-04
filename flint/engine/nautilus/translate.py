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

from datetime import timedelta
from decimal import Decimal

from flint.core.models import Side
from flint.engine.money import ZERO, money
from flint.engine.orders import OrderRecord, OrderStatus
from flint.engine.portfolio import apply_fill_delta
from flint.engine.portfolio.events import (
    EQUITY,
    FILL,
    FUNDING,
    LIQUIDATION,
    ORDER_CANCELLED,
    ORDER_PLACED,
    ORDER_REJECTED,
    RUN_FINISHED,
    RUN_STARTED,
)
from flint.engine.state import PortfolioState
from flint.engine.tearsheet import InvariantError

from . import timeconv
from ._compat import (
    Actor,
    LiquiditySide,
    OrderCanceled,
    OrderFilled,
    OrderRejected,
    OrderSide,
)
from .execmodels import decode_fill_tag

# §19.4 run-end reconciliation tolerances. Cash is Decimal and matches to the cent;
# entry price and size can differ by the instrument's price/size rounding (Nautilus
# holds fixed-precision Price/Quantity; Flint holds raw floats), so they reconcile
# to a float tolerance rather than exactly (§19.4).
CASH_TOLERANCE = Decimal("0.01")
ENTRY_TOLERANCE = 1e-6
SIZE_TOLERANCE = 1e-9

# The Nautilus buy side, resolved once (a native BUY fill is a Flint LONG, §A6).
_BUY = OrderSide.BUY


def _native_slippage_bps(side: Side, price: float, submit_mid: float) -> float:
    """Adverse slippage of a native fill vs the submit-time mid, in bps (0 if unknown).

    Positive = worse than the mid a trader saw when the order was sent: a LONG paying
    above the mid, a SHORT selling below it. ``0.0`` when the mid is unknown (no book
    or quote at submit time) — never an invented number (D26).
    """
    if submit_mid <= 0:
        return 0.0
    signed = (price - submit_mid) if side is Side.LONG else (submit_mid - price)
    return signed / submit_mid * 10_000


class FlintRecorder(Actor):
    """Single EventLog writer + shadow book + order state machine (§A6)."""

    def __init__(
        self,
        *,
        event_log,
        shadow: PortfolioState,
        engine_name: str,
        equity_interval_s: int | None = None,
        mark_keys: dict | None = None,
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
        # Tick-lane EQUITY cadence: a fixed interval (default 60 s) sampled off a
        # Nautilus clock timer, since Nautilus's AccountState is not per-bar (§6.0,
        # §A6). ``None`` in the bar lane, where the shim drives per-bar equity.
        self._flint_equity_interval_s = equity_interval_s
        # Tick-lane native-fill metadata, keyed by the Nautilus client-order-id the
        # host submitted. A native L2 fill carries no Flint tag, so the host notes the
        # Flint coid + venue/market/margin_mode + submit-mid here at submit time, and
        # the recorder reads it back when the ``OrderFilled`` arrives (§A6/N8).
        self._flint_native_meta: dict[str, dict] = {}
        # Tick-lane mark subscriptions: Nautilus ``InstrumentId`` → Flint (venue,
        # market) key. In the bar lane the shim writes ``_flint_last_mark`` per bar via
        # ``set_mark``; the tick lane instead keeps it fresh off the ``MarkPriceUpdate``
        # stream (``on_mark_price``), so equity valuation and the liquidation module's
        # ``process`` read the latest recorded mark. Empty in the bar lane.
        self._flint_mark_keys: dict = mark_keys or {}

    # -- run lifecycle ---------------------------------------------------------

    def on_start(self) -> None:
        self._flint_log.emit(RUN_STARTED, {"engine": self._flint_engine_name})
        # Listen for Nautilus order events: OrderFilled is translated (bar-lane fills
        # are tagged, tick-lane fills fall through to the native path); tick-lane IOC
        # remainder cancels and native rejects also arrive here. Bar-lane rejects/
        # cancels are Flint-level and come via the shim's direct calls, not the bus.
        self.msgbus.subscribe(topic="events.order.*", handler=self._on_order_event)
        # Tick lane: sample EQUITY on a fixed-interval clock timer (§6.0).
        if self._flint_equity_interval_s is not None:
            self.clock.set_timer(
                name="flint_equity",
                interval=timedelta(seconds=self._flint_equity_interval_s),
                callback=self._on_equity_timer,
            )
        # Tick lane: keep the carried marks fresh off the mark-price stream, so the
        # liquidation module's ``process`` and equity valuation read the latest mark
        # (the bar lane writes these via the shim's per-bar ``set_mark`` instead).
        for iid in self._flint_mark_keys:
            self.subscribe_mark_prices(iid)

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
        self._emit_order_terminal(
            ORDER_REJECTED,
            client_order_id=order.client_order_id,
            market=order.market,
            venue=order.venue,
            reason=reason,
        )

    def record_cancelled(self, order, *, reason: str) -> None:
        """Carry ``order`` to the terminal ``cancelled`` state and emit (§6.2)."""
        rec = self._flint_orders.get(order.client_order_id)
        if rec is None or rec.is_terminal:
            return
        rec.transition(OrderStatus.CANCELLED, reason=reason)
        self._emit_order_terminal(
            ORDER_CANCELLED,
            client_order_id=order.client_order_id,
            market=order.market,
            venue=order.venue,
            reason=reason,
        )

    def _emit_order_terminal(
        self, kind: str, *, client_order_id: str, market: str, venue: str, reason: str
    ) -> None:
        self._flint_log.emit(
            kind,
            {
                "client_order_id": client_order_id,
                "market": market,
                "venue": venue,
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

        Tick lane (N8): an ``OrderFilled`` with no Flint tag is a native L2 fill —
        the recorder reads the fill facts off the event itself (:meth:`_on_native_fill`);
        an ``OrderCanceled`` is the IOC remainder cancel, and an ``OrderRejected`` a
        native pre-trade rejection, both routed to the state machine here.
        """
        if isinstance(event, OrderCanceled):
            self._on_native_cancel(event)
            return
        if isinstance(event, OrderRejected):
            self._on_native_reject(event)
            return
        if not isinstance(event, OrderFilled):
            return
        order = self.cache.order(event.client_order_id)
        p = decode_fill_tag(order)
        if p is None:  # tick-lane native L2 fill (no Flint tag) — N8
            self._on_native_fill(event)
            return
        if p.get("liquidation_close"):
            # A §6.5 forced-close flatten (liquidation.py): Nautilus closes its own
            # position off this entry-priced fill, but the shadow book and the
            # LIQUIDATION event were already written by ``record_liquidation``. Do not
            # fold it or emit a FILL — the money moved once, in the module.
            return
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

    # -- tick-lane native fills / cancels / rejects (N8) -----------------------

    def note_native_order(
        self,
        naut_coid: str,
        *,
        flint_coid: str,
        venue: str,
        market: str,
        margin_mode: str,
        submit_mid: float,
    ) -> None:
        """Record the metadata a native L2 fill needs but the event doesn't carry (N8).

        A tick-lane order is matched by Nautilus's own book, so its ``OrderFilled``
        carries no Flint tag. The host notes the Flint coid, the position's margin
        mode, and the submit-time mid (for slippage) here at submit time, keyed by
        the Nautilus client-order-id, so :meth:`_on_native_fill` can fold and
        attribute the fill. The recorder stays the single writer — this only stashes
        lookup data, it emits nothing.
        """
        self._flint_native_meta[naut_coid] = {
            "flint_coid": flint_coid,
            "venue": venue,
            "market": market,
            "margin_mode": margin_mode,
            "submit_mid": submit_mid,
        }

    def _on_native_fill(self, event) -> None:
        """Fold a native L2 fill and emit FILL v2 from the event's own numbers (§A6/N8).

        The tick lane's fills come from Nautilus's book, not Flint's fill models, so
        the price/size/side/fee are read off the ``OrderFilled`` event (the fee is
        exactly the ``TickFeeModel`` commission, so both books charge the same to the
        cent). Fidelity is ``native-l2``; slippage is the fill vs the submit-time mid
        when that is known; there are no Tier-C path flags. The shadow book folds
        through the same ``apply_fill_delta`` reducer the bar lane and ``replay.fold``
        use, so the single-writer guarantee and the run-end reconciliation hold.
        """
        meta = self._flint_native_meta.get(str(event.client_order_id))
        if meta is None:  # a fill with neither a Flint tag nor native metadata
            raise InvariantError(
                f"native OrderFilled {event.client_order_id} has no Flint metadata — "
                "every tick-lane order is noted at submit time (note_native_order)"
            )
        venue = meta["venue"]
        market = meta["market"]
        margin_mode = meta["margin_mode"]
        side = Side.LONG if event.order_side == _BUY else Side.SHORT
        price = float(event.last_px)
        size = float(event.last_qty)
        fee = float(event.commission.as_double())
        acct = self._flint_shadow.account(venue)
        acct.cash -= money(fee)
        acct.fees_paid += money(fee)
        realized = apply_fill_delta(
            self._flint_shadow.positions,
            (venue, market),
            side=side,
            size=size,
            price=price,
            margin_mode=margin_mode,
        )
        if realized:
            acct.credit(money(realized))
            acct.realized_pnl += money(realized)
        rec = self._flint_orders.get(meta["flint_coid"])
        is_partial = False
        if rec is not None and not rec.is_terminal:
            rec.apply_fill(size)
            is_partial = rec.status is OrderStatus.PARTIAL
        liquidity = "maker" if event.liquidity_side == LiquiditySide.MAKER else "taker"
        self._flint_log.emit(
            FILL,
            {
                "client_order_id": meta["flint_coid"],
                "market": market,
                "venue": venue,
                "side": side,
                "price": price,
                "size": size,
                "fee": fee,
                "liquidity": liquidity,
                "fidelity_tier": "native-l2",
                "slippage_bps": _native_slippage_bps(side, price, meta["submit_mid"]),
                "is_partial": is_partial,
                "margin_mode": margin_mode,
                "realized_pnl": str(money(realized)),
                "intrabar_ambiguous": False,
                "flags": [],
            },
            ts=timeconv.ns_to_ms(event.ts_event),
            event_version=2,
        )

    def _on_native_cancel(self, event) -> None:
        """A tick-lane IOC remainder cancel → ORDER_CANCELLED (§6.2/N8).

        Only a tick-lane strategy order (present in the native metadata) is handled;
        a bar-lane fill or a liquidation-close flatten — which Nautilus may also
        cancel/complete — has no metadata and is ignored, so the bar lane is unaffected.
        """
        meta = self._flint_native_meta.get(str(event.client_order_id))
        if meta is None:
            return
        rec = self._flint_orders.get(meta["flint_coid"])
        if rec is None or rec.is_terminal:
            return
        rec.transition(OrderStatus.CANCELLED, reason="ioc_remainder")
        self._emit_order_terminal(
            ORDER_CANCELLED,
            client_order_id=meta["flint_coid"],
            market=meta["market"],
            venue=meta["venue"],
            reason="ioc_remainder",
        )

    def _on_native_reject(self, event) -> None:
        """A tick-lane native pre-trade rejection → ORDER_REJECTED (§6.2/N8)."""
        meta = self._flint_native_meta.get(str(event.client_order_id))
        if meta is None:
            return
        rec = self._flint_orders.get(meta["flint_coid"])
        if rec is None or rec.is_terminal:
            return
        reason = getattr(event, "reason", "") or "rejected"
        rec.transition(OrderStatus.REJECTED, reason=reason)
        self._emit_order_terminal(
            ORDER_REJECTED,
            client_order_id=meta["flint_coid"],
            market=meta["market"],
            venue=meta["venue"],
            reason=reason,
        )

    def _on_equity_timer(self, event) -> None:
        """Fixed-interval EQUITY sample (tick lane) — valued on the latest marks."""
        self.emit_equity(timeconv.ns_to_ms(event.ts_event))

    def on_mark_price(self, update) -> None:
        """Carry the latest tick-lane mark for its (venue, market) key (§6.0/§6.5).

        The tick-lane analogue of the bar shim's ``set_mark``: a ``MarkPriceUpdate``
        refreshes ``_flint_last_mark`` so the liquidation module's ``process`` (same
        timestep) and the interval EQUITY sample value positions on the most recent
        real mark — never a synthesized one (D26). Only subscribed instruments arrive.
        """
        key = self._flint_mark_keys.get(update.instrument_id)
        if key is not None:
            self._flint_last_mark[key] = float(update.value)

    def finalize_native(self) -> None:
        """Cancel every still-working tick-lane order at run end (mirrors §6.2).

        Driven by the engine after ``engine.run()`` so no order is left working when
        ``check_invariants`` folds the log — the tick-lane analogue of the bar-lane
        shim's ``finalize_run``. A native order Nautilus already cancelled/filled is
        terminal and skipped.
        """
        for coid, rec in list(self._flint_orders.items()):
            if rec.is_terminal:
                continue
            rec.transition(OrderStatus.CANCELLED, reason="run_ended")
            self._emit_order_terminal(
                ORDER_CANCELLED,
                client_order_id=coid,
                market=rec.market,
                venue=rec.venue,
                reason="run_ended",
            )

    # -- funding settlement (FlintFundingModule-driven, §6.4) ------------------

    def record_funding(
        self,
        *,
        venue: str,
        market: str,
        rate_hourly: float,
        settled_rate_hourly: float,
        rate_capped: bool,
        interval_s: int,
        price_basis: str,
        rate_type: str,
        oracle_price: float,
        size: float,
        amount: Decimal,
        ts: int,
    ) -> None:
        """Apply one settled funding payment and emit FUNDING — byte-identical to ``loop._settle_funding``.

        The :class:`~flint.engine.nautilus.funding.FlintFundingModule` computes
        ``amount`` from the three pure primitives; the recorder is the single writer,
        so it makes the shadow-book money move (credit cash, accrue ``funding_paid``)
        and emits the FUNDING event here. ``replay.fold`` reproduces the same cash from
        the ``amount`` string on the payload, so the shadow book and the stored log
        cannot diverge. The payload fields and order mirror the legacy loop exactly
        (``oracle_price`` names the settlement price for every ``price_basis``).
        """
        acct = self._flint_shadow.account(venue)
        acct.credit(amount)
        acct.funding_paid += amount
        self._flint_log.emit(
            FUNDING,
            {
                "market": market,
                "venue": venue,
                "rate_hourly": rate_hourly,
                "settled_rate_hourly": settled_rate_hourly,
                "rate_capped": rate_capped,
                "interval_s": interval_s,
                "price_basis": price_basis,
                "rate_type": rate_type,
                "oracle_price": oracle_price,
                "size": size,
                "amount": str(amount),
            },
            ts=ts,
        )

    # -- liquidation (FlintLiquidationModule-driven, §6.5) ---------------------

    def record_liquidation(self, decision) -> None:
        """Apply one planned liquidation and emit LIQUIDATION — byte-identical to ``loop._apply_liquidation``.

        The realized loss and the full LIQUIDATION payload were computed purely by
        :func:`~flint.engine.liquidation.cascade.plan_liquidations` (the §6.5
        economics, backstop clamp and insurance shortfall included). The recorder is
        the single writer, so it credits the shadow account, accrues ``realized_pnl``,
        drops the position, and emits the event — the same three effects, in the same
        order, the legacy inline applier had (§6.5, §19.2). The Nautilus account is
        moved separately by the module's ``adjust_account`` so both books agree at
        run end.
        """
        venue = decision.key[0]
        acct = self._flint_shadow.account(venue)
        acct.credit(money(decision.realized))
        acct.realized_pnl += money(decision.realized)
        del self._flint_shadow.positions[decision.key]
        self._flint_log.emit(LIQUIDATION, decision.payload, ts=decision.ts)

    def carried_marks(self) -> dict[tuple[str, str], float]:
        """The carried bar-close marks (per venue/market) for cross-pool valuation.

        The shim owns the write (``set_mark`` at step (8), for equity); the
        liquidation module reads them here to value a venue's *other* cross positions
        at their carried close mark (§6.5). One writer, no divergence (the N3
        double-write resolution).
        """
        return self._flint_last_mark

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

    def reconcile(
        self, *, portfolio, cache, venue, settlement_currency, seed_positions=None
    ) -> None:
        """Assert the shadow book agrees with Nautilus's own account/positions (§19.4).

        Two event models describe one run; this closes the loop at run end. Shadow
        cash must equal the Nautilus account balance (within :data:`CASH_TOLERANCE`),
        and every open position must match on side/size (:data:`SIZE_TOLERANCE`) and
        entry price (:data:`ENTRY_TOLERANCE`). A mismatch is an engine bug and raises
        :class:`InvariantError`.

        **Warm start (§6.7).** When the run adopted a pre-seeded shadow book (paper
        resume), ``seed_positions`` maps ``market -> signed size`` of the positions the
        book already held at run start. Those are never materialized in Nautilus, so
        its cache only ever holds the *delta* traded since run start. The position
        comparison is therefore against the shadow book **minus the seed**: for each
        market the expected Nautilus signed size is ``shadow_signed - seed_signed``
        (a market whose delta is zero must be flat in Nautilus; a market fully closed
        or flipped nets to the correct signed delta). Entry price is **not** compared
        on a seeded market — the shadow blends the seed's entry with the run's fills
        while Nautilus's average reflects only the delta, so the two legitimately
        differ; the shadow book is the accounting authority (D26/§19.4). This also
        leaves Nautilus's *margin* view approximate under warm start (its risk engine
        never sees the pre-existing positions), which is acceptable for paper.

        Cash stays a strict equality: Nautilus is funded with the seed cash and every
        subsequent move is mirrored (Flint-priced fills via the tag fee model, funding
        / liquidation via ``adjust_account``). The one move Nautilus cannot mirror is
        realized PnL booked against an invisible seed position — a fill that reduces or
        closes a carried position realizes on the shadow book but opens/flips a fresh
        Nautilus position instead. Such a fill is out of scope for N10's warm start;
        the shadow book remains authoritative and this cross-check would flag it.
        """
        shadow_cash = sum((a.cash for a in self._flint_shadow.accounts.values()), ZERO)
        account = portfolio.account(venue)
        nautilus_cash = account.balance_total(settlement_currency).as_decimal()
        if abs(shadow_cash - nautilus_cash) > CASH_TOLERANCE:
            raise InvariantError(
                f"shadow cash {shadow_cash} != Nautilus balance {nautilus_cash} "
                f"(tolerance {CASH_TOLERANCE}) at run end"
            )
        seed = seed_positions or {}
        shadow_open = {
            market: pos
            for (_, market), pos in self._flint_shadow.positions.items()
            if pos.size != 0
        }
        # Expected Nautilus holdings = the shadow book minus the invisible seed. A
        # seeded market that was never traded (delta 0) drops out; one that was closed
        # or flipped nets to the signed delta Nautilus actually opened.
        expected: dict[str, float] = {}
        for market in set(shadow_open) | set(seed):
            pos = shadow_open.get(market)
            shadow_signed = (pos.side.sign * pos.size) if pos is not None else 0.0
            delta = shadow_signed - seed.get(market, 0.0)
            if abs(delta) > SIZE_TOLERANCE:
                expected[market] = delta
        nautilus_open = {p.instrument_id.symbol.value: p for p in cache.positions_open()}
        if set(expected) != set(nautilus_open):
            raise InvariantError(
                f"expected Nautilus open positions {sorted(expected)} (shadow minus "
                f"seed) != Nautilus open positions {sorted(nautilus_open)} at run end"
            )
        for market, delta in expected.items():
            np = nautilus_open[market]
            naut_signed = float(np.signed_qty)
            if abs(delta - naut_signed) > SIZE_TOLERANCE:
                raise InvariantError(
                    f"{market}: expected Nautilus signed size {delta} (shadow minus "
                    f"seed) != Nautilus {naut_signed} at run end"
                )
            # Entry price is comparable only where the shadow position is entirely the
            # run's own fills (no seed to blend it with, §19.4).
            if market not in seed:
                pos = shadow_open[market]
                if abs(pos.entry_price - float(np.avg_px_open)) > ENTRY_TOLERANCE:
                    raise InvariantError(
                        f"{market}: shadow entry {pos.entry_price} != Nautilus "
                        f"{float(np.avg_px_open)} (tolerance {ENTRY_TOLERANCE}) at run end"
                    )


__all__ = ["FlintRecorder", "CASH_TOLERANCE", "ENTRY_TOLERANCE", "SIZE_TOLERANCE"]
