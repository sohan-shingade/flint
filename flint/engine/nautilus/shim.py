"""``BarLaneStrategy`` — unmodified Flint bar strategies on the Nautilus core (§A7, §6.1).

Nautilus supplies the clock, the account, and the position book; Flint supplies the
**bar economics**. This Nautilus ``Strategy`` hosts an unmodified Flint strategy
(``on_candle(candle, ctx) -> list[Signal]``) and re-runs, per bar, the exact locked
sequence the legacy loop runs (§6.1) — T+1 fills at the next open, resting-order
triggers, the strategy callback, and §8.1 signal routing — reusing the *shared*
pure modules verbatim:

* :class:`~flint.engine.loop.EngineContext` (reused as-is — the §8.2 visibility
  contract is not re-implemented),
* :mod:`flint.engine.signals` (``route_signals`` / ``materialize_usd_intent`` —
  one implementation of the §8.1 conversion rules, coid ordering preserved),
* :func:`flint.engine.fills.assemble_fill_context` + the venue's ``FillModel``
  (Flint prices every tier; the shim submits the fill to Nautilus at that price).

The shim never touches the shadow book or the EventLog directly — the
:class:`~flint.engine.nautilus.translate.FlintRecorder` is the single writer. The
shim calls the recorder to place/reject/cancel orders and to emit equity, and it
submits each Flint-priced fill to Nautilus so the recorder's ``OrderFilled``
handler folds it into the shadow book (the one source of accounting) and the
run-end reconciliation can cross-check Flint against Nautilus.

Determinism: the shim seeds one :class:`random.Random` (``config.rng_seed``) shared
between the per-fill latency draws and ``ctx.rng``, consumed in the legacy order
(one latency draw per fill, then the strategy) — so a Nautilus run is byte-identical
to a legacy run on the same feed.

Nautilus's Cython ``Component`` base reserves plain attribute names (``_log`` etc.),
so **every** shim attribute is ``_flint_``-prefixed.
"""

from __future__ import annotations

from random import Random

from flint.core.models import Candle, Order, OrderType, Side
from flint.core.time import bar_end
from flint.engine.api import EngineFeed, EngineRunSpec
from flint.engine.fills import assemble_fill_context, fill_model_for
from flint.engine.fills.latency import LatencyModel
from flint.engine.loop import EngineContext
from flint.engine.orders import OrderStatus
from flint.engine.signals import _UsdIntent, materialize_usd_intent, route_signals
from flint.engine.state import PortfolioState

from . import dataconv, timeconv
from ._compat import (
    ClientOrderId,
    OrderSide,
    Quantity,
    Strategy,
    TimeInForce,
)
from .execmodels import encode_fill_tag

# Same partial-fill epsilon the legacy loop uses when shrinking a resting order.
_FILL_EPS = 1e-12

_NAUTILUS_SIDE = {Side.LONG: OrderSide.BUY, Side.SHORT: OrderSide.SELL}


class BarLaneStrategy(Strategy):
    """Host an unmodified Flint bar strategy on the Nautilus core (§A7)."""

    def __init__(
        self,
        *,
        flint_strategy: object,
        feed: EngineFeed,
        recorder: object,
        shadow: PortfolioState,
        spec: EngineRunSpec,
        instruments: dict[str, object],
        resolution_s: int,
        funding: object = None,
        liquidation: object = None,
    ) -> None:
        super().__init__()
        self._flint_strategy = flint_strategy
        self._flint_feed = feed
        self._flint_recorder = recorder
        self._flint_shadow = shadow
        # The §6.4 funding module (a SimulationModule) or ``None`` for a candle-only
        # feed. Bar-lane funding is shim-driven, not process-driven (see funding.py):
        # settlement must land before this bar's EQUITY snapshot for byte-exact parity.
        self._flint_funding = funding
        # The §6.5 liquidation module (a SimulationModule) — always present. Bar-lane
        # liquidation is shim-driven at step (4), after funding: the module plans +
        # applies the money and hands back the Nautilus flattens the shim submits.
        self._flint_liquidation = liquidation
        self._flint_venue_spec = spec.venue_spec
        self._flint_instruments = instruments
        self._flint_resolution_s = resolution_s
        # Per-fill economics: the venue's fill model + a seeded latency draw, seeded
        # exactly as the legacy engine (config.rng_seed), shared with ctx.rng.
        self._flint_fill = fill_model_for(spec.venue_spec.structure)
        self._flint_latency = LatencyModel(spec.venue_spec.latency)
        self._flint_rng = Random(spec.config.rng_seed)
        # The three legacy queues (§8.1): T+1 market orders, deferred USD intents,
        # and resting stop/limit/TP orders.
        self._flint_pending_market: list[Order] = []
        self._flint_pending_usd: list[_UsdIntent] = []
        self._flint_resting: list[Order] = []
        # Closed-bar history per market (ctx.candles reads it), grown as bars finish.
        self._flint_candle_history: dict[str, list[Candle]] = {}
        # Engine-originated coid counter — identical assignment order to the loop.
        self._flint_coid = 0
        # Unique Nautilus client-order-ids for the fill submissions (the real Flint
        # coid rides on the order tag; a resting order can fill across several bars,
        # so each Nautilus submission needs its own id).
        self._flint_submission_seq = 0
        # Map each fed bar back to its exact Flint candle (never reconstruct one from
        # the Nautilus bar — that would round prices to the instrument precision).
        self._flint_candle_by_key: dict[tuple[str, int], Candle] = {
            (c.market, c.ts): c for c in feed.candles
        }
        # Warm start (§6.7): a carried shadow book is mirrored into Nautilus's own
        # book on the FIRST per-bar sequence (see ``_materialize_seed``), once.
        self._flint_seeded = False

    # -- lifecycle -------------------------------------------------------------

    def on_start(self) -> None:
        for instrument in self._flint_instruments.values():
            self.subscribe_bars(
                dataconv.bar_type_for(instrument.id, self._flint_resolution_s)
            )

    def on_bar(self, bar) -> None:
        """Drive one locked per-bar sequence for the fed candle at this bar close."""
        market = bar.bar_type.instrument_id.symbol.value
        start = timeconv.bar_close_ns_to_candle_start_ms(
            bar.ts_event, self._flint_resolution_s
        )
        candle = self._flint_candle_by_key.get((market, start))
        if candle is None:  # a bar with no Flint candle should never occur (D26)
            raise AssertionError(
                f"no Flint candle for bar ({market}, start_ms={start})"
            )
        self._process_bar(candle)

    def finalize_run(self) -> None:
        """Cancel every still-working order at run end (mirrors the legacy loop).

        Driven explicitly by the engine after ``engine.run()`` so the ORDER_CANCELLED
        events precede RUN_FINISHED and are emitted in the legacy order (resting then
        pending market). These orders never reached Nautilus (they never filled), so
        cancelling them is purely a Flint-level state-machine + event action.
        """
        for order in list(self._flint_resting) + list(self._flint_pending_market):
            self._flint_recorder.record_cancelled(order, reason="run_ended")
        self._flint_resting = []
        self._flint_pending_market = []
        self._flint_pending_usd = []

    # -- the locked per-bar sequence (§6.1, funding/liquidation are N4/N5) ------

    def _process_bar(self, candle: Candle) -> None:
        # (0) Warm start (§6.7): mirror the carried shadow book into Nautilus before
        # anything else on the first bar, so its book agrees from the first fill.
        # Fills are synchronous (``use_message_queue=False``), so the seed positions
        # exist in Nautilus before step (2)'s T+1 fills and the strategy's signals.
        if not self._flint_seeded:
            self._flint_seeded = True
            self._materialize_seed()
        venue = candle.venue
        market = candle.market
        start = candle.ts
        end = bar_end(start, candle.resolution_s)
        market_marks = self._flint_feed.marks.get(market, [])
        bar_marks = [m for m in market_marks if start <= m.ts < end]

        # (2) T+1: market orders decided last bar fill at this bar's open.
        self._fill_pending_market(candle, market_marks)
        # (3) Funding: every final settlement in [start, end), each in ts order.
        # Shim-driven (not module.process, which runs after this bar's EQUITY in the
        # Nautilus step) so accrued_funding is byte-exact with the legacy loop (§6.4).
        if self._flint_funding is not None:
            self._flint_funding.settle_bar(candle, end)
        # (4) Liquidation on the mark, AFTER funding (so a funding receipt can rescue a
        # position, §6.1). Shim-driven, same reason as funding: the module plans + moves
        # the money, and returns the Nautilus flattens the shim submits (see
        # liquidation.py — the SimulatedExchange has no force-close API).
        if self._flint_liquidation is not None:
            for close in self._flint_liquidation.check_bar(candle, bar_marks):
                self._submit_liquidation_close(close)
        # (5) Resting stop/limit/TP — adverse-extreme-first on Tier-C.
        self._process_resting(candle, bar_marks, market_marks)
        # (6) Strategy sees a read-only ctx as-of bar start; route its signals.
        ctx = EngineContext(
            state=self._flint_shadow,
            default_venue=venue,
            now=start,
            rng=self._flint_rng,
            venue_spec=self._flint_venue_spec,
            submit_order_fn=self._submit,
            funding=self._flint_feed.funding,
            marks=self._flint_feed.marks,
            books=self._flint_feed.books,
            oi=self._flint_feed.oi,
            candle_history=self._flint_candle_history,
        )
        signals = self._flint_strategy.on_candle(candle, ctx)
        self._route(signals, venue)
        # (7) This bar is now closed — add it to history for a later bar's ctx.
        self._flint_candle_history.setdefault(market, []).append(candle)
        # (8) Carry the bar-closing mark (the legacy loop does this in the
        # liquidation step; here it feeds the recorder's per-bar equity valuation)
        # and emit the per-bar EQUITY snapshot last, once all fills have settled.
        self._flint_recorder.set_mark(
            (venue, market),
            bar_marks[-1].mark_price if bar_marks else candle.close,
        )
        self._flint_recorder.emit_equity(start)

    # -- (2) T+1 market fills --------------------------------------------------

    def _fill_pending_market(
        self, candle: Candle, market_marks: list
    ) -> None:
        self._materialize_usd_intents(candle)
        if not self._flint_pending_market:
            return
        still_pending: list[Order] = []
        for order in self._flint_pending_market:
            if order.market != candle.market or order.venue != candle.venue:
                still_pending.append(order)
                continue
            result = self._compute_fill(order, candle.open, candle, market_marks)
            if result is None:
                self._flint_recorder.record_rejected(order, reason="no_fill")
                continue
            self._execute_fill(order, result, intrabar_ambiguous=False)
            rec = self._flint_recorder.order_record(order.client_order_id)
            if rec is not None and rec.status is OrderStatus.PARTIAL:
                self._flint_recorder.record_cancelled(order, reason="ioc_remainder")
        self._flint_pending_market = still_pending

    def _materialize_usd_intents(self, candle: Candle) -> None:
        if not self._flint_pending_usd:
            return
        remaining: list[_UsdIntent] = []
        for intent in self._flint_pending_usd:
            if intent.market != candle.market or intent.venue != candle.venue:
                remaining.append(intent)
                continue
            request, residual = materialize_usd_intent(
                intent, candle.open, self._flint_venue_spec.size_decimals
            )
            order = self._new_order(**request.as_order_kwargs())
            self._flint_recorder.record_placed(
                order, size_usd=intent.size_usd, size_residual=residual
            )
            if request.size <= 0:
                self._flint_recorder.record_rejected(order, reason="size_rounds_to_zero")
            elif intent.limit_price > 0:
                self._flint_resting.append(order)
            else:
                self._flint_pending_market.append(order)
        self._flint_pending_usd = remaining

    # -- (5) resting orders ----------------------------------------------------

    def _process_resting(
        self, candle: Candle, bar_marks: list, market_marks: list
    ) -> None:
        if not self._flint_resting:
            return
        ambiguous = not bar_marks
        triggered = [
            o
            for o in self._flint_resting
            if o.market == candle.market
            and o.venue == candle.venue
            and self._is_triggered(o, candle)
        ]
        triggered.sort(key=lambda o: 0 if o.type is OrderType.STOP else 1)
        for order in triggered:
            result = self._compute_fill(order, order.price, candle, market_marks)
            if result is None:
                continue
            filled = result.fill.size
            if filled < order.size - _FILL_EPS:
                order.size -= filled
            else:
                self._flint_resting.remove(order)
            self._execute_fill(order, result, intrabar_ambiguous=ambiguous)

    @staticmethod
    def _is_triggered(order: Order, candle: Candle) -> bool:
        """Tier-C price-cross trigger test for a resting order (verbatim §6.1)."""
        long_side = order.side is Side.LONG
        if order.type is OrderType.LIMIT:
            return candle.low <= order.price if long_side else candle.high >= order.price
        if order.type is OrderType.STOP:
            return candle.high >= order.price if long_side else candle.low <= order.price
        if order.type is OrderType.TAKE_PROFIT:
            return candle.low <= order.price if long_side else candle.high >= order.price
        return False

    # -- (6) routing -----------------------------------------------------------

    def _route(self, signals: list, default_venue: str) -> None:
        for decision in route_signals(
            signals,
            default_venue,
            lambda venue, market: self._flint_shadow.positions.get((venue, market)),
        ):
            if isinstance(decision, _UsdIntent):
                self._flint_pending_usd.append(decision)
            else:
                self._submit(self._new_order(**decision.as_order_kwargs()))

    # -- order registration (mirrors loop._submit, idempotent) -----------------

    def _submit(self, order: Order) -> object:
        existing = self._flint_recorder.order_record(order.client_order_id)
        if existing is not None:
            return existing  # idempotent duplicate — already in the machine
        self._flint_recorder.record_placed(order)
        if order.type is OrderType.MARKET:
            self._flint_pending_market.append(order)
        else:
            self._flint_resting.append(order)
        return self._flint_recorder.order_record(order.client_order_id)

    def _new_order(self, **kwargs) -> Order:
        coid = kwargs.pop("client_order_id", "") or self._next_coid()
        return Order(client_order_id=coid, **kwargs)

    def _next_coid(self) -> str:
        self._flint_coid += 1
        return f"coid-{self._flint_coid}"

    # -- fill computation + submission to Nautilus -----------------------------

    def _compute_fill(self, order: Order, reference_price: float, candle: Candle, market_marks: list):
        ctx = assemble_fill_context(
            order,
            reference_price=reference_price,
            candle=candle,
            market_marks=market_marks,
            spec=self._flint_venue_spec,
            latency=self._flint_latency,
            rng=self._flint_rng,
            books=self._flint_feed.books,
            trades=self._flint_feed.trades,
        )
        return self._flint_fill.fill(order, ctx)

    def _execute_fill(self, order: Order, result, *, intrabar_ambiguous: bool) -> None:
        """Submit the Flint-priced fill to Nautilus; the recorder emits the FILL.

        The whole Flint fill (price/size/fee/liquidity/tier/flags/ts) rides on the
        order tag; :class:`FlintPriceFillModel` fills the order at that price and
        :class:`FlintTagFeeModel` charges that fee, so Nautilus's account and
        positions mirror Flint's shadow book. The submission is synchronous
        (``use_message_queue=False``): the recorder's ``OrderFilled`` handler folds
        the fill into the shadow book and emits FILL before this returns.
        """
        fill = result.fill
        payload = {
            "client_order_id": fill.client_order_id,
            "market": fill.market,
            "venue": fill.venue,
            "side": str(fill.side),
            "price": fill.price,
            "size": fill.size,
            "fee": fill.fee,
            "liquidity": fill.liquidity,
            "fidelity_tier": fill.fidelity_tier,
            "slippage_bps": fill.slippage_bps,
            "is_partial": fill.is_partial,
            "margin_mode": order.margin_mode,
            "intrabar_ambiguous": intrabar_ambiguous,
            "flags": list(result.flags),
            "ts": fill.ts,
        }
        instrument = self._flint_instruments[order.market]
        self._flint_submission_seq += 1
        nautilus_order = self.order_factory.market(
            instrument_id=instrument.id,
            order_side=_NAUTILUS_SIDE[fill.side],
            quantity=Quantity.from_str(
                f"{fill.size:.{instrument.size_precision}f}"
            ),
            time_in_force=TimeInForce.IOC,
            client_order_id=ClientOrderId(f"n{self._flint_submission_seq}"),
            reduce_only=order.reduce_only,
            tags=[encode_fill_tag(payload)],
        )
        self.submit_order(nautilus_order)

    # -- (4) liquidation forced-close (module-planned, shim-submitted) ---------

    # -- (0) warm-start seed materialization (§6.7, liquidation close in reverse) --

    def _materialize_seed(self) -> None:
        """Open each carried shadow position inside Nautilus at run start (§6.7).

        A paper warm start adopts a pre-seeded shadow book, but Nautilus's own book
        starts flat. Mirror every carried position into Nautilus with a tagged
        zero-fee fill pinned to the position's **entry** price — the liquidation
        forced-close machinery run in reverse (open instead of flatten) — so
        Nautilus's cache and account track the shadow book from bar one. A later fill
        that reduces or closes a carried position then fills against a real Nautilus
        position instead of being denied reduce-only against a flat book, and the
        run-end reconciliation stays a strict mirror (§19.4).

        The ``seed`` tag tells the recorder to ignore the fill: the shadow already
        carries the position (it was adopted at run start) and the zero fee leaves the
        account untouched, so this is pure Nautilus-side bookkeeping, not a FILL event.
        The entry-priced open realizes nothing, so cash equality holds; a later close
        realizes PnL against the same entry basis on both books.
        """
        for (venue, market), pos in self._flint_shadow.positions.items():
            if pos.size == 0:
                continue
            instrument = self._flint_instruments.get(market)
            if instrument is None:
                raise ValueError(
                    f"warm-start seed position on {market} has no instrument in this "
                    "run — every seeded market must be present for the mirror to hold"
                )
            self._flint_submission_seq += 1
            payload = {
                "price": pos.entry_price,  # entry → Nautilus opens at the shadow basis
                "fee": 0.0,  # zero fee → the seed leaves the account balance untouched
                "market": market,
                "venue": venue,
                "seed": True,
            }
            nautilus_order = self.order_factory.market(
                instrument_id=instrument.id,
                order_side=_NAUTILUS_SIDE[pos.side],
                quantity=Quantity.from_str(f"{pos.size:.{instrument.size_precision}f}"),
                time_in_force=TimeInForce.IOC,
                client_order_id=ClientOrderId(f"nseed{self._flint_submission_seq}"),
                reduce_only=False,
                tags=[encode_fill_tag(payload)],
            )
            self.submit_order(nautilus_order)

    def _submit_liquidation_close(self, close) -> None:
        """Flatten one liquidated position on Nautilus at its entry price (§6.5).

        The money already moved (the module credited the shadow book via the recorder
        and mirrored it onto the Nautilus account with ``adjust_account``); this only
        closes the Nautilus position so its book agrees with the shadow at run end.
        A reduce-only market order pinned to the position's **entry** price
        (:class:`FlintPriceFillModel`) with zero fee realizes exactly zero on the
        Nautilus account, so ``adjust_account`` stays the sole money mover. The
        ``liquidation_close`` tag tells the recorder to ignore the fill — it is
        bookkeeping, not a FILL event.
        """
        instrument = self._flint_instruments[close.market]
        self._flint_submission_seq += 1
        payload = {
            "price": close.price,  # entry → Nautilus realizes zero on the close
            "fee": 0.0,
            "market": close.market,
            "venue": close.venue,
            "liquidation_close": True,
        }
        # Reduce a long by SELLing, a short by BUYing — the opposite of the position.
        close_side = OrderSide.SELL if close.side is Side.LONG else OrderSide.BUY
        nautilus_order = self.order_factory.market(
            instrument_id=instrument.id,
            order_side=close_side,
            quantity=Quantity.from_str(f"{close.size:.{instrument.size_precision}f}"),
            time_in_force=TimeInForce.IOC,
            client_order_id=ClientOrderId(f"nliq{self._flint_submission_seq}"),
            reduce_only=True,
            tags=[encode_fill_tag(payload)],
        )
        self.submit_order(nautilus_order)


__all__ = ["BarLaneStrategy"]
