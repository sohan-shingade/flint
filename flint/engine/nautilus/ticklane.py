"""``TickLaneStrategy`` — event-driven Flint strategies on the Nautilus core (§8.6, §A7b).

The tick-lane analogue of :class:`~flint.engine.nautilus.shim.BarLaneStrategy`. Where
the bar shim re-runs a locked per-bar sequence, this host reacts to the raw event
tape: it subscribes to **only** the Nautilus data streams the wrapped tick strategy
overrode a handler for (a trades-only strategy never receives a book callback, so
unrequested deltas never cross the GIL, §8.6), converts each Nautilus datum back to
its Flint model, hands it to the strategy with an as-of-event ``ctx``, and routes the
returned signals **immediately** — the tick lane has no T+1: a market order is
submitted the instant it is decided and matched against Nautilus's real L2 book
(native matching), a limit order rests on that book.

Fills are Nautilus-native (no Flint fill model): the host submits an **untagged**
order, :class:`~flint.engine.nautilus.execmodels.FlintPriceFillModel` defers it to the
matching engine, and the recorder reads the resulting ``OrderFilled`` off the bus
(``_on_native_fill``) — so the host stashes the per-order metadata the event does not
carry (Flint coid, margin mode, submit-time mid) via ``recorder.note_native_order``
before each submission, and is otherwise never the accounting writer. The recorder is
the single EventLog + shadow-book writer, exactly as in the bar lane.

The one order the host submits itself, rather than on the strategy's behalf, is the
§6.5 liquidation flatten: :meth:`flatten` is wired into the liquidation module's
``process`` (the module has no Strategy capability to submit an order), and pins a
reduce-only close at the position's entry price with a zero-fee tag — byte-identical
to the bar shim's ``_submit_liquidation_close`` — so ``adjust_account`` stays the sole
money mover and both books agree at run end.

Nautilus's Cython ``Strategy`` base reserves plain attribute names, so every host
attribute is ``_flint_``-prefixed.
"""

from __future__ import annotations

from random import Random

from flint.core.models import BookDelta, Candle, Order, OrderType, QuoteTick, Side
from flint.core.models import FundingRate as FlintFundingRateModel
from flint.core.models import TimeInForce as FlintTimeInForce
from flint.engine.api import EngineFeed, EngineRunSpec
from flint.engine.loop import EngineContext
from flint.engine.signals import _UsdIntent, materialize_usd_intent, route_signals
from flint.engine.state import PortfolioState

from . import dataconv, timeconv
from ._compat import (
    AggregationSource,
    BookAction,
    ClientId,
    ClientOrderId,
    DataType,
    OrderSide,
    Price,
    Quantity,
    Strategy,
    TimeInForce,
)
from .dataconv import FlintFundingRate
from .execmodels import encode_fill_tag

_NAUTILUS_SIDE = {Side.LONG: OrderSide.BUY, Side.SHORT: OrderSide.SELL}
# The Flint book-side label for a Nautilus book-order side (bid = a BUY resting order).
_BOOK_SIDE_LABEL = {OrderSide.BUY: "bid", OrderSide.SELL: "ask"}


class TickLaneStrategy(Strategy):
    """Host an event-driven Flint tick strategy on the Nautilus core (§8.6, §A7b)."""

    def __init__(
        self,
        *,
        tick_strategy: object,
        feed: EngineFeed,
        recorder: object,
        shadow: PortfolioState,
        spec: EngineRunSpec,
        instruments: dict[str, object],
        resolution_s: int,
        venue: str,
    ) -> None:
        super().__init__()
        self._flint_strategy = tick_strategy
        # The streams the strategy asked for — only these are subscribed (§8.6). A raw
        # TickStrategy and a TickEngineStrategy adapter both expose ``handlers``.
        self._flint_handlers = frozenset(getattr(tick_strategy, "handlers", ()))
        self._flint_feed = feed
        self._flint_recorder = recorder
        self._flint_shadow = shadow
        self._flint_venue_spec = spec.venue_spec
        self._flint_venue = venue
        self._flint_instruments = instruments
        self._flint_resolution_s = resolution_s
        # A seeded RNG exposed as ctx.rng, seeded exactly as the bar lane (rng_seed) so
        # any stochastic strategy logic is reproducible run-to-run (determinism, §19.4).
        self._flint_rng = Random(spec.config.rng_seed)
        # Per-market last quote-mid and last trade price, updated as events flow, used
        # to size USD intents and to attribute native-fill slippage at submit time.
        self._flint_last_mid: dict[str, float] = {}
        self._flint_last_trade: dict[str, float] = {}
        # Closed-bar history per market (ctx.candles reads it), grown in on_bar.
        self._flint_candle_history: dict[str, list[Candle]] = {}
        # Engine-originated coid counter + unique Nautilus client-order-id sequence.
        self._flint_coid = 0
        self._flint_submission_seq = 0

    # -- subscription: only the overridden handlers' streams (§8.6) ------------

    def on_start(self) -> None:
        for market, instrument in self._flint_instruments.items():
            iid = instrument.id
            if "on_trade" in self._flint_handlers:
                self.subscribe_trade_ticks(iid)
            if "on_quote" in self._flint_handlers:
                self.subscribe_quote_ticks(iid)
            if "on_book" in self._flint_handlers:
                self.subscribe_order_book_deltas(iid)
            if "on_candle" in self._flint_handlers:
                # INTERNAL: Nautilus aggregates the fed trade tape into bars itself
                # (the aggregation proven equal to Flint's CandleAggregator, §8.6).
                self.subscribe_bars(
                    dataconv.bar_type_for(
                        iid,
                        self._flint_resolution_s,
                        aggregation_source=AggregationSource.INTERNAL,
                    )
                )
        if "on_funding" in self._flint_handlers:
            # Predicted funding rides as Flint custom data under the FLINT client id.
            self.subscribe_data(
                DataType(FlintFundingRate),
                client_id=ClientId(dataconv.FLINT_CLIENT_ID),
            )

    # -- Nautilus event callbacks (one per subscribed stream) ------------------

    def on_trade_tick(self, tick) -> None:
        market = tick.instrument_id.symbol.value
        trade = dataconv.tick_to_trade_print(tick)
        self._flint_last_trade[market] = trade.price
        self._dispatch("on_trade", trade, timeconv.ns_to_ms(tick.ts_event), market)

    def on_quote_tick(self, quote) -> None:
        market = quote.instrument_id.symbol.value
        q = QuoteTick(
            market=market,
            ts=timeconv.ns_to_ms(quote.ts_event),
            bid_price=float(quote.bid_price),
            ask_price=float(quote.ask_price),
            bid_size=float(quote.bid_size),
            ask_size=float(quote.ask_size),
            venue=self._flint_venue_of(market),
        )
        if q.mid > 0:
            self._flint_last_mid[market] = q.mid
        self._dispatch("on_quote", q, q.ts, market)

    def on_order_book_deltas(self, deltas) -> None:
        market = deltas.instrument_id.symbol.value
        venue = self._flint_venue_of(market)
        for delta in deltas.deltas:
            if delta.action == BookAction.CLEAR:
                continue  # a structural reset — not a level update the strategy acts on
            order = delta.order
            bd = BookDelta(
                market=market,
                ts=timeconv.ns_to_ms(delta.ts_event),
                seq=delta.sequence,
                side=_BOOK_SIDE_LABEL[order.side],
                price=float(order.price),
                size=float(order.size),
                is_snapshot=False,
                venue=venue,
            )
            self._dispatch("on_book", bd, bd.ts, market)

    def on_bar(self, bar) -> None:
        market = bar.bar_type.instrument_id.symbol.value
        start = timeconv.bar_close_ns_to_candle_start_ms(
            bar.ts_event, self._flint_resolution_s
        )
        candle = Candle(
            ts=start,
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
            market=market,
            resolution_s=self._flint_resolution_s,
            venue=self._flint_venue_of(market),
        )
        self._flint_last_trade[market] = candle.close
        # The candle is now closed — visible to this and later handlers via ctx.candles.
        self._flint_candle_history.setdefault(market, []).append(candle)
        self._dispatch("on_candle", candle, start, market)

    def on_data(self, data) -> None:
        if not isinstance(data, FlintFundingRate):
            return
        if data.rate_type != "predicted":  # only predicted rows reach on_funding (§6.4)
            return
        rate = FlintFundingRateModel(
            market=data.market,
            ts=timeconv.ns_to_ms(data.ts_event),
            rate_hourly=data.rate_hourly,
            interval_s=data.interval_s,
            price_basis=data.price_basis,
            rate_type=data.rate_type,
            venue=data.venue,
        )
        self._dispatch("on_funding", rate, rate.ts, data.market)

    # -- dispatch + immediate routing ------------------------------------------

    def _dispatch(self, handler: str, datum: object, now_ms: int, market: str) -> None:
        """Run one overridden handler as-of ``now_ms`` and route its signals at once.

        The ctx is the §8.2 read-only window evaluated at the event ts; a signal it
        returns is routed immediately (no T+1) — a market order fills against the live
        L2 book, a limit order rests on it. The adapter (or raw strategy) gates
        non-executable venues (D28) before returning, so only routable signals arrive.
        """
        if handler not in self._flint_handlers:
            return
        venue = self._flint_venue_of(market)
        ctx = self._build_ctx(now_ms, venue)
        dispatch = getattr(self._flint_strategy, "dispatch", None)
        if dispatch is not None:
            signals = dispatch(handler, datum, ctx)
        else:  # a raw TickStrategy (no adapter) — call the handler directly
            method = getattr(self._flint_strategy, handler)
            signals = method(datum, ctx) or []
        self._route(signals, venue)

    def _build_ctx(self, now_ms: int, venue: str) -> EngineContext:
        return EngineContext(
            state=self._flint_shadow,
            default_venue=venue,
            now=now_ms,
            rng=self._flint_rng,
            venue_spec=self._flint_venue_spec,
            submit_order_fn=self._submit,
            funding=self._flint_feed.funding,
            marks=self._flint_feed.marks,
            books=self._flint_feed.books,
            oi=self._flint_feed.oi,
            candle_history=self._flint_candle_history,
        )

    def _route(self, signals, default_venue: str) -> None:
        for decision in route_signals(
            signals,
            default_venue,
            lambda venue, market: self._flint_shadow.positions.get((venue, market)),
        ):
            if isinstance(decision, _UsdIntent):
                self._route_usd_intent(decision)
            else:
                self._submit(self._new_order(**decision.as_order_kwargs()))

    def _route_usd_intent(self, intent: _UsdIntent) -> None:
        """Size a USD intent at the current price and submit it now (no T+1, §8.6).

        The bar lane defers a ``size_usd`` order to the next bar's open to avoid a
        look-ahead; the tick lane already *is* at the decision instant, so it sizes on
        the current price (the last quote mid, else the last trade) and submits
        immediately. With no price yet knowable, the order is placed then rejected
        ``no_price`` — a structured record, never a silent drop or an invented price.
        """
        price = self._current_price(intent.market)
        if price <= 0:
            order = self._new_order(
                market=intent.market,
                venue=intent.venue,
                side=intent.side,
                type=OrderType.MARKET,
                size=0.0,
                price=0.0,
                tif=intent.tif or TimeInForce.IOC,
                margin_mode=intent.margin_mode,
                reduce_only=False,
            )
            self._flint_recorder.record_placed(order, size_usd=intent.size_usd)
            self._flint_recorder.record_rejected(order, reason="no_price")
            return
        request, residual = materialize_usd_intent(
            intent, price, self._flint_venue_spec.size_decimals
        )
        order = self._new_order(**request.as_order_kwargs())
        self._flint_recorder.record_placed(
            order, size_usd=intent.size_usd, size_residual=residual
        )
        if request.size <= 0:
            self._flint_recorder.record_rejected(order, reason="size_rounds_to_zero")
        else:
            self._submit_native(order)

    # -- order submission (immediate, native) ----------------------------------

    def _submit(self, order: Order) -> object:
        """Place ``order`` on the machine and submit it to Nautilus at once (idempotent)."""
        existing = self._flint_recorder.order_record(order.client_order_id)
        if existing is not None:
            return existing  # idempotent duplicate — already in the machine
        self._flint_recorder.record_placed(order)
        self._submit_native(order)
        return self._flint_recorder.order_record(order.client_order_id)

    def _submit_native(self, order: Order) -> None:
        """Submit an **untagged** order to Nautilus for native L2 matching (§8.6).

        The order carries no Flint fill tag, so :class:`FlintPriceFillModel` defers it
        to the matching engine (real book depth) and :class:`TickFeeModel` charges the
        native maker/taker commission. The per-order metadata the resulting
        ``OrderFilled`` will not carry — the Flint coid, margin mode, and submit-time
        mid (for slippage) — is noted on the recorder first, keyed by the Nautilus
        client-order-id, so ``_on_native_fill`` can fold and attribute the fill.
        """
        instrument = self._flint_instruments[order.market]
        self._flint_submission_seq += 1
        naut_coid = ClientOrderId(f"n{self._flint_submission_seq}")
        self._flint_recorder.note_native_order(
            str(naut_coid),
            flint_coid=order.client_order_id,
            venue=order.venue,
            market=order.market,
            margin_mode=order.margin_mode,
            submit_mid=self._current_mid(order.market),
        )
        qty = Quantity.from_str(f"{order.size:.{instrument.size_precision}f}")
        side = _NAUTILUS_SIDE[order.side]
        if order.type is OrderType.MARKET:
            nautilus_order = self.order_factory.market(
                instrument_id=instrument.id,
                order_side=side,
                quantity=qty,
                time_in_force=TimeInForce.IOC,
                client_order_id=naut_coid,
                reduce_only=order.reduce_only,
            )
        else:
            nautilus_order = self.order_factory.limit(
                instrument_id=instrument.id,
                order_side=side,
                quantity=qty,
                price=Price.from_str(
                    f"{order.price:.{instrument.price_precision}f}"
                ),
                time_in_force=TimeInForce.GTC,
                client_order_id=naut_coid,
                reduce_only=order.reduce_only,
                # Post-only rides Nautilus's native flag: the L2 matching engine
                # rejects an ALO that would cross on arrival (HL semantics).
                post_only=order.tif is FlintTimeInForce.ALO,
            )
        self.submit_order(nautilus_order)

    def _new_order(self, **kwargs) -> Order:
        coid = kwargs.pop("client_order_id", "") or self._next_coid()
        return Order(client_order_id=coid, **kwargs)

    def _next_coid(self) -> str:
        self._flint_coid += 1
        return f"coid-{self._flint_coid}"

    # -- §6.5 forced-close callback (liquidation module → host) ----------------

    def flatten(self, close) -> None:
        """Flatten one liquidated position on Nautilus at its entry price (§6.5).

        Wired into the tick-lane liquidation module's ``process``. The money already
        moved (the module credited the shadow book via the recorder and mirrored it
        onto the Nautilus account with ``adjust_account``); this only closes the
        Nautilus position so its book agrees with the shadow at run end. Byte-identical
        to the bar shim's ``_submit_liquidation_close``: a reduce-only market order
        pinned to the position's **entry** price with a zero-fee ``liquidation_close``
        tag, so Nautilus realizes exactly zero on the close and the recorder ignores
        it (bookkeeping, not a FILL).
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

    # -- helpers ---------------------------------------------------------------

    def _current_price(self, market: str) -> float:
        """The best price to size a market/USD order now: last mid, else last trade."""
        mid = self._flint_last_mid.get(market, 0.0)
        return mid if mid > 0 else self._flint_last_trade.get(market, 0.0)

    def _current_mid(self, market: str) -> float:
        """The submit-time mid for slippage attribution (last quote mid, else 0)."""
        return self._flint_last_mid.get(market, 0.0)

    def _flint_venue_of(self, market: str) -> str:
        """The Flint (lowercase) venue string for ``market`` (single-venue v1)."""
        return self._flint_venue


__all__ = ["TickLaneStrategy"]
