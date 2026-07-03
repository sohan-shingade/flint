"""The per-bar backtest loop — the honest engine's spine (§6.1).

A backtest walks candles in time order. For each bar the loop runs a **locked**
sequence (§6.1) — the order is correctness, not incidental:

1. **Set the three prices** for the bar (§2.4): the oracle/index drives funding,
   the mark drives liquidation and PnL, the last/close anchors fills.
2. **Fill T+1 market orders at this bar's open.** A market order decided on bar
   *t* becomes eligible at bar *t+1* and executes at its open — never at the
   close of the bar the strategy could see (the classic look-ahead).
3. **Settle ALL funding whose ts falls in this bar, each in ts order** — a wide
   bar over hourly funding applies each hourly settlement individually, never one
   lumped payment.
4. **Check liquidation on the MARK, after funding.** Funding settles first so a
   funding *receipt* can rescue a position a naive close-price check would have
   wrongly liquidated — and a *debit* can correctly push one under (§6.1 worked
   example). This ordering is the whole reason the sequence is locked.
5. **Process resting stop / limit / take-profit orders**, with the intrabar
   adverse-extreme-first policy on Tier-C (OHLCV-only) segments — the pessimistic
   path assumption, and every intrabar-triggered event flagged
   ``intrabar_ambiguous`` in the log so the tearsheet shows how much PnL rests on
   it.
6. **Build ``ctx``, call the strategy, route its signals** through the *shared*
   fill path (the same path paper trading uses), queuing market orders for the
   next bar's open (T+1).
7. **Record + emit** — every fill, funding payment and liquidation is an event on
   the append-only log (§2.10), so the run replays exactly (slice 3.5).

Slice 3.1 ships this spine with an honest Tier-C fill (``NaiveFillModel``), a
flat-maintenance liquidation trigger, and single-settlement funding. Slices 3.2
(CLOB fill fidelity), 3.3 (predicted/final funding + caps + hard gate) and 3.4
(margin tiers, cross/isolated) deepen the steps *without* reordering them — the
sequence above is the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from random import Random
from typing import Protocol

from flint.core.models import (
    Candle,
    Fill,
    FundingRate,
    MarkSnapshot,
    Order,
    OrderType,
    Position,
    Side,
    Signal,
    TimeInForce,
)
from flint.core.time import bar_end, last_before
from flint.engine.portfolio import (
    FILL,
    FUNDING,
    LIQUIDATION,
    ORDER_PLACED,
    RUN_FINISHED,
    RUN_STARTED,
    EventLog,
)

from .fills import FillContext, FillModel, NaiveFillModel
from .funding.settlement import settlement_payment
from .liquidation.check import (
    bankruptcy_price,
    is_liquidated,
    maintenance_requirement,
)
from .money import money
from .state import PortfolioState


@dataclass(frozen=True)
class EngineConfig:
    """The engine's locked-but-calibratable defaults for a run.

    ``maint_frac`` is the flat maintenance-margin fraction (slice 3.4 replaces it
    with the venue's size-tiered table); the fee rates are Hyperliquid base-tier
    placeholders that get their primary-source citation in slice 3.3 (D14).
    ``rng_seed`` seeds ``ctx.rng`` so a run — and its latency draws, once slice
    3.2 adds them — is deterministic.
    """

    maint_frac: float = 0.025
    taker_fee_rate: float = 0.00035
    maker_fee_rate: float = 0.0001
    rng_seed: int = 0


class Strategy(Protocol):
    """The minimal strategy seam the loop calls (slice 3.6 formalises it).

    ``on_candle`` receives the just-closed candle and a read-only ``ctx`` and
    returns a list of ``Signal``s (empty = do nothing). Slice 3.6 builds the full
    ``ctx`` visibility contract (§8.2) and the Signal→Order conversion rules
    (§8.1); 3.1 needs only the call seam so the loop is end-to-end.
    """

    def on_candle(self, candle: Candle, ctx: "EngineContext") -> list[Signal]:
        ...


class NoopStrategy:
    """A strategy that never trades — the default for engine-mechanics tests."""

    def on_candle(self, candle: Candle, ctx: "EngineContext") -> list[Signal]:
        return []


@dataclass
class EngineContext:
    """The read-only window a strategy sees (minimal in 3.1; §8.2 in 3.6).

    Slice 3.1 exposes only position lookup — enough to route ``close`` signals
    and prove the seam. The full accessor set (funding_rate, orderbook, account
    sizing helpers) and the strict as-of visibility contract land in slice 3.6.
    """

    state: PortfolioState
    default_venue: str
    now: int
    rng: Random

    def position(self, market: str, venue: str | None = None) -> Position | None:
        """The open position on ``(venue or default, market)``, or ``None``."""
        return self.state.position(venue or self.default_venue, market)


class BacktestEngine:
    """Walks candles through the locked per-bar sequence (§6.1)."""

    def __init__(
        self,
        event_log: EventLog,
        *,
        config: EngineConfig | None = None,
        fill_model: FillModel | None = None,
        state: PortfolioState | None = None,
    ) -> None:
        self._log = event_log
        self._cfg = config or EngineConfig()
        self._fill = fill_model or NaiveFillModel()
        self.state = state or PortfolioState()
        self._rng = Random(self._cfg.rng_seed)
        # Market orders wait one bar and fill at the next bar's open (T+1).
        self._pending_market: list[Order] = []
        # Resting stop/limit/TP orders live until triggered, cancelled, or the
        # run ends (GTC).
        self._resting: list[Order] = []
        self._coid = 0  # client-order-id counter (slice 3.5 makes it idempotent)

    # -- public entry point ----------------------------------------------------

    def run(
        self,
        candles: list[Candle],
        *,
        marks: dict[str, list[MarkSnapshot]] | None = None,
        funding: dict[str, list[FundingRate]] | None = None,
        strategy: Strategy | None = None,
    ) -> PortfolioState:
        """Run the loop over ``candles`` and return the final portfolio state.

        ``marks``/``funding`` are keyed by market, each ascending by ts. Both are
        plain in-memory fixtures — the engine is pure domain and does no I/O
        (§6): data arrives already loaded.
        """
        marks = marks or {}
        funding = funding or {}
        strat = strategy or NoopStrategy()
        self._log.emit(RUN_STARTED, {"engine": "backtest"})
        for candle in candles:
            self._process_bar(candle, marks, funding, strat)
        self._log.emit(RUN_FINISHED, {"engine": "backtest"})
        return self.state

    # -- the locked per-bar sequence (§6.1) ------------------------------------

    def _process_bar(
        self,
        candle: Candle,
        marks: dict[str, list[MarkSnapshot]],
        funding: dict[str, list[FundingRate]],
        strategy: Strategy,
    ) -> None:
        venue = candle.venue
        market = candle.market
        start = candle.ts
        end = bar_end(start, candle.resolution_s)
        market_marks = marks.get(market, [])
        bar_marks = [m for m in market_marks if start <= m.ts < end]

        # (2) T+1: market orders decided last bar fill at this bar's open.
        self._fill_pending_market(candle)
        # (3) Funding: every settlement in [start, end), each in ts order.
        self._settle_funding(candle, funding.get(market, []), market_marks, end)
        # (4) Liquidation on the mark, AFTER funding.
        self._check_liquidations(candle, bar_marks)
        # (5) Resting stop/limit/TP — adverse-extreme-first on Tier-C.
        self._process_resting(candle, bar_marks)
        # (6) Strategy sees a read-only ctx, returns signals; route them.
        ctx = EngineContext(
            state=self.state, default_venue=venue, now=start, rng=self._rng
        )
        signals = strategy.on_candle(candle, ctx)
        self._route(signals, venue)

    # -- (2) T+1 market fills --------------------------------------------------

    def _fill_pending_market(self, candle: Candle) -> None:
        """Fill queued market orders at this bar's open (their T+1 moment)."""
        if not self._pending_market:
            return
        still_pending: list[Order] = []
        for order in self._pending_market:
            if order.market != candle.market or order.venue != candle.venue:
                still_pending.append(order)  # a different market's bar
                continue
            ctx = self._fill_ctx(candle.open, candle.ts)
            fill = self._fill.fill(order, ctx)
            if fill is not None:
                self._apply_fill(order, fill)
        self._pending_market = still_pending

    # -- (3) funding -----------------------------------------------------------

    def _settle_funding(
        self,
        candle: Candle,
        rates: list[FundingRate],
        market_marks: list[MarkSnapshot],
        end: int,
    ) -> None:
        """Settle each funding row whose ts falls in [bar_start, bar_end), in order."""
        key = (candle.venue, candle.market)
        settlements = sorted(
            (r for r in rates if candle.ts <= r.ts < end), key=lambda r: r.ts
        )
        for rate in settlements:
            pos = self.state.positions.get(key)
            if pos is None:
                continue  # only positions open at the settlement ts are charged
            oracle = self._oracle_at(market_marks, rate.ts, candle.close)
            amount = settlement_payment(pos, rate.rate_hourly, oracle)
            acct = self.state.account(candle.venue)
            acct.credit(amount)
            acct.funding_paid += amount
            self._log.emit(
                FUNDING,
                {
                    "market": candle.market,
                    "venue": candle.venue,
                    "rate_hourly": rate.rate_hourly,
                    "price_basis": rate.price_basis,
                    "rate_type": rate.rate_type,
                    "oracle_price": oracle,
                    "size": pos.size,
                    "amount": str(amount),
                },
                ts=rate.ts,
            )

    @staticmethod
    def _oracle_at(
        marks: list[MarkSnapshot], settlement_ts: int, fallback: float
    ) -> float:
        """Oracle/index price at-or-before the settlement second (§6.4)."""
        snap = last_before(marks, settlement_ts + 1, key=lambda m: m.ts)
        return snap.index_price if snap is not None else fallback

    # -- (4) liquidation -------------------------------------------------------

    def _check_liquidations(
        self, candle: Candle, bar_marks: list[MarkSnapshot]
    ) -> None:
        """Liquidate any position on this market whose equity fell below maintenance."""
        key = (candle.venue, candle.market)
        pos = self.state.positions.get(key)
        if pos is None:
            return
        adverse, ambiguous = self._adverse_mark(pos.side, candle, bar_marks)
        acct = self.state.account(candle.venue)
        cash_before = float(acct.cash)
        equity = cash_before + pos.unrealized_pnl(adverse)
        maintenance = maintenance_requirement(pos, adverse, self._cfg.maint_frac)
        if not is_liquidated(equity, maintenance):
            return
        realized = (adverse - pos.entry_price) * pos.side.sign * pos.size
        acct.credit(money(realized))
        acct.realized_pnl += money(realized)
        del self.state.positions[key]
        self._log.emit(
            LIQUIDATION,
            {
                "market": candle.market,
                "venue": candle.venue,
                "side": pos.side,
                "size": pos.size,
                "mark_price": adverse,
                "equity": equity,
                "maintenance": maintenance,
                "bankruptcy_price": bankruptcy_price(pos, cash_before),
                "realized_pnl": str(money(realized)),
                "intrabar_ambiguous": ambiguous,
            },
            ts=candle.ts,
        )

    @staticmethod
    def _adverse_mark(
        side: Side, candle: Candle, bar_marks: list[MarkSnapshot]
    ) -> tuple[float, bool]:
        """The bar's adverse mark for ``side`` + whether the path was assumed.

        With recorded mark snapshots (Tier A/B) the adverse extreme is the worst
        mark in the bar and the timing is known-enough. On OHLCV-only segments
        (Tier C) it falls back to the candle's adverse extreme — low for a long,
        high for a short — and the caller flags the event ``intrabar_ambiguous``
        (§6.1 D11 pessimistic policy).
        """
        if bar_marks:
            prices = [m.mark_price for m in bar_marks]
            return (min(prices) if side is Side.LONG else max(prices)), False
        return (candle.low if side is Side.LONG else candle.high), True

    # -- (5) resting orders ----------------------------------------------------

    def _process_resting(
        self, candle: Candle, bar_marks: list[MarkSnapshot]
    ) -> None:
        """Trigger resting stop/limit/TP orders, adverse (stops) first on Tier-C."""
        if not self._resting:
            return
        ambiguous = not bar_marks  # OHLCV-only segment → path assumed
        triggered = [
            o
            for o in self._resting
            if o.market == candle.market
            and o.venue == candle.venue
            and self._is_triggered(o, candle)
        ]
        # Adverse-extreme-first: a protective stop is assumed to fire before a
        # favourable take-profit / limit within the same bar (§6.1 D11).
        triggered.sort(key=lambda o: 0 if o.type is OrderType.STOP else 1)
        for order in triggered:
            ctx = self._fill_ctx(order.price, candle.ts)
            fill = self._fill.fill(order, ctx)
            if fill is None:
                continue
            self._resting.remove(order)
            self._apply_fill(order, fill, intrabar_ambiguous=ambiguous)

    @staticmethod
    def _is_triggered(order: Order, candle: Candle) -> bool:
        """Tier-C price-cross trigger test for a resting order."""
        long_side = order.side is Side.LONG
        if order.type is OrderType.LIMIT:
            return candle.low <= order.price if long_side else candle.high >= order.price
        if order.type is OrderType.STOP:
            # buy-stop triggers above; sell-stop (protective long) triggers below.
            return candle.high >= order.price if long_side else candle.low <= order.price
        if order.type is OrderType.TAKE_PROFIT:
            return candle.low <= order.price if long_side else candle.high >= order.price
        return False

    # -- (6) routing signals ---------------------------------------------------

    def _route(self, signals: list[Signal], default_venue: str) -> None:
        """Convert signals to orders and enqueue them on the shared fill path.

        Slice 3.1 handles the market/close/limit routing needed to prove T+1 and
        the resting path; the full Signal→Order conversion (size_usd sizing at the
        execution bar's open, validation, per-(market,venue,action) dedup — §8.1)
        lands in slice 3.6.
        """
        for sig in signals:
            venue = sig.venue or default_venue
            if sig.is_close:
                pos = self.state.positions.get((venue, sig.market))
                if pos is None:
                    continue
                order = self._new_order(
                    market=sig.market,
                    venue=venue,
                    side=pos.side.opposite,
                    type=OrderType.MARKET,
                    size=pos.size,
                    tif=TimeInForce.IOC,
                    reduce_only=True,
                    margin_mode=pos.margin_mode,
                )
                self._pending_market.append(order)
                self._emit_placed(order)
                continue
            if sig.size <= 0:
                # size_usd sizing needs the next bar's open mark — deferred to
                # slice 3.6 with the sizing helpers; skip in 3.1.
                continue
            side = Side.LONG if sig.action == "long" else Side.SHORT
            if sig.limit_price > 0:
                order = self._new_order(
                    market=sig.market,
                    venue=venue,
                    side=side,
                    type=OrderType.LIMIT,
                    size=sig.size,
                    price=sig.limit_price,
                    tif=sig.tif or TimeInForce.GTC,
                    margin_mode=sig.margin_mode,
                )
                self._resting.append(order)
            else:
                order = self._new_order(
                    market=sig.market,
                    venue=venue,
                    side=side,
                    type=OrderType.MARKET,
                    size=sig.size,
                    tif=TimeInForce.IOC,
                    margin_mode=sig.margin_mode,
                )
                self._pending_market.append(order)
            self._emit_placed(order)

    # -- shared fill application -----------------------------------------------

    def _apply_fill(
        self, order: Order, fill: Fill, *, intrabar_ambiguous: bool = False
    ) -> None:
        """Apply a fill to cash + the position book and emit a FILL event."""
        acct = self.state.account(fill.venue)
        acct.cash -= money(fill.fee)
        acct.fees_paid += money(fill.fee)
        key = (fill.venue, fill.market)
        pos = self.state.positions.get(key)
        realized = 0.0
        if pos is None:
            self.state.positions[key] = Position(
                market=fill.market,
                venue=fill.venue,
                side=fill.side,
                size=fill.size,
                entry_price=fill.price,
                margin_mode=order.margin_mode,
            )
        elif pos.side is fill.side:
            total = pos.size + fill.size
            entry = (pos.entry_price * pos.size + fill.price * fill.size) / total
            self.state.positions[key] = replace(pos, size=total, entry_price=entry)
        else:
            closed = min(pos.size, fill.size)
            realized = (fill.price - pos.entry_price) * pos.side.sign * closed
            acct.credit(money(realized))
            acct.realized_pnl += money(realized)
            remaining = pos.size - fill.size
            if remaining > 0:
                self.state.positions[key] = replace(pos, size=remaining)
            elif remaining == 0:
                del self.state.positions[key]
            else:  # flip: close the old side, open the remainder on the fill side
                self.state.positions[key] = replace(
                    pos, side=fill.side, size=-remaining, entry_price=fill.price
                )
        self._log.emit(
            FILL,
            {
                "client_order_id": fill.client_order_id,
                "market": fill.market,
                "venue": fill.venue,
                "side": fill.side,
                "price": fill.price,
                "size": fill.size,
                "fee": fill.fee,
                "liquidity": fill.liquidity,
                "fidelity_tier": fill.fidelity_tier,
                "slippage_bps": fill.slippage_bps,
                "is_partial": fill.is_partial,
                "realized_pnl": str(money(realized)),
                "intrabar_ambiguous": intrabar_ambiguous,
            },
            ts=fill.ts,
        )

    # -- small helpers ---------------------------------------------------------

    def _fill_ctx(self, reference_price: float, ts: int) -> FillContext:
        return FillContext(
            reference_price=reference_price,
            ts=ts,
            taker_fee_rate=self._cfg.taker_fee_rate,
            maker_fee_rate=self._cfg.maker_fee_rate,
        )

    def _new_order(self, **kwargs) -> Order:
        coid = kwargs.pop("client_order_id", "") or self._next_coid()
        return Order(client_order_id=coid, **kwargs)

    def _next_coid(self) -> str:
        self._coid += 1
        return f"coid-{self._coid}"

    def _emit_placed(self, order: Order) -> None:
        self._log.emit(
            ORDER_PLACED,
            {
                "client_order_id": order.client_order_id,
                "market": order.market,
                "venue": order.venue,
                "side": order.side,
                "type": order.type,
                "size": order.size,
                "price": order.price,
                "tif": order.tif,
                "reduce_only": order.reduce_only,
            },
        )
