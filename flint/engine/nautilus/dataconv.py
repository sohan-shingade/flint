"""Flint market-data models → Nautilus objects (the engine-side wrangler, §A3, cross-seam 1).

This is the one place Flint's recorded facts (:class:`~flint.core.models.Candle`,
:class:`~flint.core.models.MarkSnapshot`, :class:`~flint.engine.fills.TradePrint`,
:class:`~flint.core.models.FundingRate`) become the Nautilus data objects the
backtest kernel consumes. Every ``nautilus_trader`` name comes from
:mod:`._compat` (the churn firewall) — this module names no Nautilus import path.

Design choices recorded here (N2):

* **Funding is a custom type, not Nautilus's ``FundingRateUpdate``.** Nautilus's
  funding message is lossy — no ``price_basis``, no ``rate_type``, and it never
  settles into the account (spike-verified). Flint's predicted/final split and the
  §6.4 settlement contract are *ours*, so predicted rows flow to strategies as
  :class:`FlintFundingRate` (a ``Data`` subclass carrying every field losslessly),
  and final rows go to the FlintFundingModule in N4 — never to the stream.

* **Books are stubbed until N8.** L2 replay + native matching is the tick lane's
  job; wiring ``OrderBookDeltas``/``Depth10`` now would add matching-fidelity risk
  the N2 gate does not exercise. :func:`orderbook_to_deltas` raises until N8.

* **Instruments come from ``VenueSpec``.** HL markets map to ``CryptoPerpetual``.
  Size precision is the venue's ``size_decimals``; price precision is a skeleton
  default (HL's *5-significant-figure* rule is not a fixed decimal precision — the
  per-asset price rule is refined in N3+). Fees are carried onto the instrument for
  the N3 fill path.
"""

from __future__ import annotations

from decimal import Decimal

from flint.core.models import (
    BookDelta,
    Candle,
    FundingRate,
    MarkSnapshot,
    OrderbookSnapshot,
    QuoteTick,
)
from flint.core.time import bar_start
from flint.engine.fills import TradePrint
from flint.venues import VenueSpec

from . import timeconv
from ._compat import (
    USDC,
    AggregationSource,
    AggressorSide,
    Bar,
    BarAggregation,
    BarSpecification,
    BarType,
    BookAction,
    BookOrder,
    CryptoPerpetual,
    Currency,
    CustomData,
    Data,
    DataType,
    IndexPriceUpdate,
    InstrumentId,
    MarkPriceUpdate,
    OrderBookDelta,
    OrderBookDeltas,
    OrderSide,
    Price,
    PriceType,
    Quantity,
    Symbol,
    TradeId,
    TradeTick,
    Venue,
)
from ._compat import QuoteTick as NautilusQuoteTick

# The synthetic client id under which Flint's custom (non-market-data) streams are
# routed through the backtest data engine. Subscribers pass the same id.
FLINT_CLIENT_ID = "FLINT"


# --- custom data: funding ----------------------------------------------------


class FlintFundingRate(Data):
    """A predicted funding observation carried losslessly through Nautilus (§6.4, §A4).

    A Nautilus ``Data`` subclass so it rides the message bus to strategies (via
    ``subscribe_data``) without Nautilus's lossy ``FundingRateUpdate`` dropping
    ``price_basis``/``rate_type``. Only **predicted** rows become this type — final
    rows settle through the FlintFundingModule (N4) and are never stream data.

    ``settlement_ts`` is when the predicted rate is expected to settle. The core
    :class:`~flint.core.models.FundingRate` does not yet carry it (D1 added it to
    the storage schema, not the in-memory model), so :func:`predicted_funding_to_data`
    derives it as the next interval boundary; when the feed carries a real
    ``settlement_ts`` the conversion will pass it through unchanged.
    """

    def __init__(
        self,
        *,
        instrument_id: InstrumentId,
        market: str,
        venue: str,
        rate_hourly: float,
        interval_s: int,
        price_basis: str,
        rate_type: str,
        settlement_ts: int,
        ts_event: int,
        ts_init: int,
    ) -> None:
        self.instrument_id = instrument_id
        self.market = market
        self.venue = venue
        self.rate_hourly = rate_hourly
        self.interval_s = interval_s
        self.price_basis = price_basis
        self.rate_type = rate_type
        self.settlement_ts = settlement_ts
        self._ts_event = ts_event
        self._ts_init = ts_init

    @property
    def ts_event(self) -> int:
        return self._ts_event

    @property
    def ts_init(self) -> int:
        return self._ts_init


def predicted_funding_to_data(
    fr: FundingRate, instrument_id: InstrumentId
) -> FlintFundingRate | None:
    """Convert one **predicted** :class:`FundingRate` to :class:`FlintFundingRate`.

    Returns ``None`` for non-predicted rows: ``final`` rows settle through the
    funding module (N4), never the strategy stream (§6.4). ``ts_event`` is the
    publish time (``fr.ts``, unix-ms → ns); ``settlement_ts`` is derived as the end
    of the interval containing ``fr.ts`` (the next funding boundary).
    """
    if fr.rate_type != "predicted":
        return None
    settlement_ms = bar_start(fr.ts, fr.interval_s) + fr.interval_s * timeconv.MS_PER_SECOND
    ns = timeconv.ms_to_ns(fr.ts)
    return FlintFundingRate(
        instrument_id=instrument_id,
        market=fr.market,
        venue=fr.venue,
        rate_hourly=fr.rate_hourly,
        interval_s=fr.interval_s,
        price_basis=fr.price_basis,
        rate_type=fr.rate_type,
        settlement_ts=settlement_ms,
        ts_event=ns,
        ts_init=ns,
    )


def predicted_funding_custom_data(
    fr: FundingRate, instrument_id: InstrumentId
) -> CustomData | None:
    """Wrap a predicted :class:`FlintFundingRate` as Nautilus ``CustomData`` (§6.4).

    A bare :class:`FlintFundingRate` cannot be added to the engine directly: because
    it carries an ``instrument_id``, ``BacktestEngine.add_data`` treats it as
    instrument-scoped market data and streams it *unwrapped*, so the data engine —
    which routes generic data only when it arrives as ``CustomData`` — never
    publishes it and no ``subscribe_data`` subscriber ever sees it. Wrapping it in
    ``CustomData(DataType(FlintFundingRate), rate)`` restores the generic-data path:
    the engine publishes it on ``data.FlintFundingRate.<venue>.<symbol>``, which the
    host's ``subscribe_data(DataType(FlintFundingRate))`` matches. Returns ``None``
    for non-predicted rows (they settle through the module, never the stream).
    """
    rate = predicted_funding_to_data(fr, instrument_id)
    if rate is None:
        return None
    return CustomData(DataType(FlintFundingRate), rate)


# --- bars --------------------------------------------------------------------


def resolution_to_bar_spec(resolution_s: int) -> BarSpecification:
    """Map a Flint ``resolution_s`` to a Nautilus ``BarSpecification`` (LAST price).

    Picks the coarsest whole aggregation unit that divides the resolution evenly:
    a day, else an hour, else a minute, else a second. ``3600`` → ``1-HOUR``.
    """
    if resolution_s <= 0:
        raise ValueError(f"resolution_s must be positive, got {resolution_s}")
    if resolution_s % 86_400 == 0:
        step, agg = resolution_s // 86_400, BarAggregation.DAY
    elif resolution_s % 3_600 == 0:
        step, agg = resolution_s // 3_600, BarAggregation.HOUR
    elif resolution_s % 60 == 0:
        step, agg = resolution_s // 60, BarAggregation.MINUTE
    else:
        step, agg = resolution_s, BarAggregation.SECOND
    return BarSpecification(step, agg, PriceType.LAST)


def bar_type_for(
    instrument_id: InstrumentId,
    resolution_s: int,
    *,
    aggregation_source: AggregationSource = AggregationSource.EXTERNAL,
) -> BarType:
    """The ``BarType`` at ``resolution_s`` for ``instrument_id``.

    ``EXTERNAL`` (the default) names pre-aggregated recorded candles the bar lane
    feeds directly. The tick lane's ``on_candle`` instead passes ``INTERNAL``, so
    Nautilus aggregates the fed trade tape into bars itself — the aggregation the
    ``test_nautilus_aggregator_parity`` gate pins equal to Flint's ``CandleAggregator``.
    """
    return BarType(
        instrument_id,
        resolution_to_bar_spec(resolution_s),
        aggregation_source,
    )


def _price(value: float, precision: int) -> Price:
    return Price.from_str(f"{value:.{precision}f}")


def _qty(value: float, precision: int) -> Quantity:
    return Quantity.from_str(f"{value:.{precision}f}")


def candle_to_bar(
    candle: Candle,
    bar_type: BarType,
    *,
    price_precision: int,
    size_precision: int,
) -> Bar:
    """Convert a Flint ``Candle`` (START ms) to a Nautilus ``Bar`` (CLOSE ns).

    The bar is stamped at its close per Nautilus convention; ``timeconv`` derives
    that from the candle's START and its width, so the round-trip is bar-exact.
    """
    close_ns = timeconv.candle_start_ms_to_bar_close_ns(candle.ts, candle.resolution_s)
    return Bar(
        bar_type=bar_type,
        open=_price(candle.open, price_precision),
        high=_price(candle.high, price_precision),
        low=_price(candle.low, price_precision),
        close=_price(candle.close, price_precision),
        volume=_qty(candle.volume, size_precision),
        ts_event=close_ns,
        ts_init=close_ns,
    )


# --- marks -------------------------------------------------------------------


def mark_to_updates(
    mark: MarkSnapshot, instrument_id: InstrumentId, *, price_precision: int
) -> list[MarkPriceUpdate | IndexPriceUpdate]:
    """Split a Flint ``MarkSnapshot`` into a ``MarkPriceUpdate`` (+ ``IndexPriceUpdate``).

    Flint bundles mark and index in one snapshot (the §6.1 correctness encoding);
    Nautilus keeps them as separate streams. The index update is emitted only when a
    positive index is present (never invented, D26).
    """
    ns = timeconv.ms_to_ns(mark.ts)
    out: list[MarkPriceUpdate | IndexPriceUpdate] = [
        MarkPriceUpdate(
            instrument_id=instrument_id,
            value=_price(mark.mark_price, price_precision),
            ts_event=ns,
            ts_init=ns,
        )
    ]
    if mark.index_price > 0:
        out.append(
            IndexPriceUpdate(
                instrument_id=instrument_id,
                value=_price(mark.index_price, price_precision),
                ts_event=ns,
                ts_init=ns,
            )
        )
    return out


# --- trades ------------------------------------------------------------------


def trade_to_tick(
    tp: TradePrint,
    instrument_id: InstrumentId,
    *,
    price_precision: int,
    size_precision: int,
    seq: int,
) -> TradeTick:
    """Convert a Flint ``TradePrint`` to a Nautilus ``TradeTick``.

    A ``TradePrint`` records no aggressor side or venue trade id, so the aggressor
    is ``NO_AGGRESSOR`` and the trade id is synthesized from ``(ts, seq)`` to stay
    unique and deterministic within a run.
    """
    ns = timeconv.ms_to_ns(tp.ts)
    return TradeTick(
        instrument_id=instrument_id,
        price=_price(tp.price, price_precision),
        size=_qty(tp.size, size_precision),
        aggressor_side=AggressorSide.NO_AGGRESSOR,
        trade_id=TradeId(f"{tp.ts}-{seq}"),
        ts_event=ns,
        ts_init=ns,
    )


# --- quotes (tick lane, N8) ---------------------------------------------------


def quote_to_tick(
    q: QuoteTick,
    instrument_id: InstrumentId,
    *,
    price_precision: int,
    size_precision: int,
) -> NautilusQuoteTick:
    """Convert a Flint :class:`~flint.core.models.QuoteTick` to a Nautilus quote."""
    ns = timeconv.ms_to_ns(q.ts)
    return NautilusQuoteTick(
        instrument_id=instrument_id,
        bid_price=_price(q.bid_price, price_precision),
        ask_price=_price(q.ask_price, price_precision),
        bid_size=_qty(q.bid_size, size_precision),
        ask_size=_qty(q.ask_size, size_precision),
        ts_event=ns,
        ts_init=ns,
    )


def tick_to_trade_print(tick: TradeTick) -> TradePrint:
    """Recover a Flint :class:`TradePrint` from a Nautilus ``TradeTick`` (tick lane).

    The reverse of :func:`trade_to_tick` for the ``on_trade`` handler: a strategy
    reacts to Flint's own model (price/size/ms-ts), never a Nautilus object.
    """
    return TradePrint(
        price=float(tick.price),
        size=float(tick.size),
        ts=timeconv.ns_to_ms(tick.ts_event),
    )


# --- books (L2 replay, tick lane, N8) ----------------------------------------

_BOOK_SIDE = {"bid": OrderSide.BUY, "ask": OrderSide.SELL}


def orderbook_to_deltas(
    book: OrderbookSnapshot,
    instrument_id: InstrumentId,
    *,
    price_precision: int,
    size_precision: int,
    seq_start: int = 0,
) -> OrderBookDeltas:
    """An ``OrderbookSnapshot`` → an initial-book ``OrderBookDeltas`` (CLEAR + levels).

    A recorded snapshot rebuilds the whole book: a ``CLEAR`` wipes any prior state,
    then one ``ADD`` per bid/ask level (best-first) reconstructs it. This seeds the
    Nautilus matching book so a marketable order fills against real depth (§A7/N8).
    """
    ns = timeconv.ms_to_ns(book.ts)
    deltas = [
        OrderBookDelta(instrument_id, BookAction.CLEAR, None, 0, seq_start, ns, ns)
    ]
    seq = seq_start
    for side, levels in (("bid", book.bids), ("ask", book.asks)):
        for price, size in levels:
            seq += 1
            deltas.append(
                OrderBookDelta(
                    instrument_id,
                    BookAction.ADD,
                    BookOrder(
                        _BOOK_SIDE[side],
                        _price(price, price_precision),
                        _qty(size, size_precision),
                        seq,
                    ),
                    0,
                    seq,
                    ns,
                    ns,
                )
            )
    return OrderBookDeltas(instrument_id, deltas)


def book_delta_to_delta(
    bd: BookDelta,
    instrument_id: InstrumentId,
    *,
    price_precision: int,
    size_precision: int,
) -> OrderBookDelta:
    """Convert one Flint :class:`BookDelta` row to a Nautilus ``OrderBookDelta``.

    A positive size ``UPDATE`` upserts the price level (works for a fresh level on
    an L2_MBP book); ``size == 0`` is a Tardis delete → ``DELETE`` at that price
    with a zero-size order. Snapshot rows are also ``UPDATE`` (the caller emits a
    ``CLEAR`` at a resync boundary via :func:`book_delta_clear`).
    """
    ns = timeconv.ms_to_ns(bd.ts)
    action = BookAction.DELETE if bd.is_delete else BookAction.UPDATE
    order = BookOrder(
        _BOOK_SIDE[bd.side],
        _price(bd.price, price_precision),
        _qty(bd.size, size_precision),
        bd.seq,
    )
    return OrderBookDelta(instrument_id, action, order, 0, bd.seq, ns, ns)


def book_delta_clear(
    instrument_id: InstrumentId, ts_ms: int, seq: int
) -> OrderBookDelta:
    """A standalone ``CLEAR`` delta — emitted before a snapshot-resync group."""
    ns = timeconv.ms_to_ns(ts_ms)
    return OrderBookDelta(instrument_id, BookAction.CLEAR, None, 0, seq, ns, ns)


# --- instruments -------------------------------------------------------------


def _increment_str(precision: int) -> str:
    if precision <= 0:
        return "1"
    return "0." + "0" * (precision - 1) + "1"


def build_instrument(
    venue_spec: VenueSpec,
    market: str,
    venue: str,
    *,
    price_precision: int = 2,
) -> CryptoPerpetual:
    """Build a Nautilus ``CryptoPerpetual`` for one HL market from a ``VenueSpec``.

    ``market`` is a Flint symbol like ``"SOL-PERP"``; its base asset is the part
    before the first ``-``. Quote and settlement are USDC (HL is USDC-margined).
    ``size_precision`` is the venue's ``size_decimals``; ``price_precision`` is a
    skeleton default (see module docstring — HL's 5-sig-fig rule is not a fixed
    decimal precision; per-asset price rules are refined in N3+). Fees are carried
    for the N3 fill path.
    """
    base_code = market.split("-", 1)[0]
    instrument_id = InstrumentId(Symbol(market), Venue(venue.upper()))
    size_precision = venue_spec.size_decimals
    return CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol(market),
        base_currency=Currency.from_str(base_code),
        quote_currency=USDC,
        settlement_currency=USDC,
        is_inverse=False,
        price_precision=price_precision,
        size_precision=size_precision,
        price_increment=Price.from_str(_increment_str(price_precision)),
        size_increment=Quantity.from_str(_increment_str(size_precision)),
        ts_event=0,
        ts_init=0,
        maker_fee=Decimal(str(venue_spec.maker_fee_rate)),
        taker_fee=Decimal(str(venue_spec.taker_fee_rate)),
    )
