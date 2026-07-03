"""N8 gate — the tick lane: native L2 matching + process-driven perp economics (§8.6/§A7b).

Where the bar lane prices every fill with Flint's own models and uses Nautilus only to
hold the account, the tick lane submits **untagged** orders that match against Nautilus's
real L2 book (native matching) and reads the resulting fills back off the bus. These
goldens pin that new machinery:

* native fills — a marketable limit crossing the book, a market order partialling across
  two price levels (hand-arithmetic VWAP + per-level taker fee), and an IOC remainder
  that cancels to ORDER_CANCELLED;
* the host subscribes to **only** the streams the strategy overrode a handler for (§8.6);
* ``on_funding`` sees **predicted** rows only (§6.4);
* funding settles between trades through the module's ``process`` (the §6.4 payload exact);
* a mark trips maintenance → LIQUIDATION + flatten + exact run-end reconciliation (§6.5);
* ``on_candle`` over the real trade tape equals Flint's ``CandleAggregator`` (§8.6);
* two runs over the real recorded fragment are byte-identical (determinism, §19.4);
* a full run folds a clean tearsheet with every order terminal (§19.2).

All hand-authored fixtures are unit inputs (fixed books/trades, never generated
"market-like" data); the real-fragment goldens run on the committed Tardis SOL day
(``tests/fixtures/tardis/``). Gated on the ``nautilus`` extra (D26).
"""

from __future__ import annotations

import gzip
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("nautilus_trader")

from flint.adapters import InMemoryUserData  # noqa: E402
from flint.core.models import (  # noqa: E402
    BookDelta,
    MarkSnapshot,
    Signal,
)
from flint.data import Kind  # noqa: E402
from flint.data.ingest.vendors import TardisCsvClient  # noqa: E402
from flint.engine.api import EngineFeed, EngineRunSpec  # noqa: E402
from flint.engine.fills import TradePrint  # noqa: E402
from flint.engine.funding.settlement import settlement_payment  # noqa: E402
from flint.engine.loop import EngineConfig  # noqa: E402
from flint.engine.nautilus.ticklane import TickLaneStrategy  # noqa: E402
from flint.engine.portfolio import EventLog  # noqa: E402
from flint.engine.portfolio.events import (  # noqa: E402
    FILL,
    FUNDING,
    LIQUIDATION,
    ORDER_CANCELLED,
)
from flint.engine.select import engine_for  # noqa: E402
from flint.engine.tearsheet import build_tearsheet, check_invariants  # noqa: E402
from flint.ports import TenantContext  # noqa: E402
from flint.strategy.tick import TickStrategy  # noqa: E402
from flint.venues import HYPERLIQUID  # noqa: E402

VENUE = "hyperliquid"
SOL = "SOL-PERP"
T0 = 1_700_000_040_000
CAP = "100000"
TAKER = 0.00045  # HYPERLIQUID.taker_fee_rate — the native taker commission per fill
CAP_DECIMAL = Decimal(CAP)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tardis"


# --- hand-authored feed pieces -----------------------------------------------


def _book(ts: int, *, bids=((99.9, 50.0),), asks=((100.0, 20.0),)) -> list[BookDelta]:
    """A snapshot book at ``ts`` (best-first bids/asks) as Flint BookDelta rows."""
    rows: list[BookDelta] = []
    seq = 0
    for side, levels in (("bid", bids), ("ask", asks)):
        for price, size in levels:
            rows.append(
                BookDelta(
                    market=SOL, ts=ts, seq=seq, side=side, price=price,
                    size=size, is_snapshot=True, venue=VENUE,
                )
            )
            seq += 1
    return rows


def _spec(capital: str = CAP) -> EngineRunSpec:
    return EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        initial_capital=capital,
        fund_venue=VENUE,
        mark_policy="close_derived",
    )


def _run(strategy, feed: EngineFeed, capital: str = CAP):
    log = EventLog(
        InMemoryUserData(), TenantContext.local(), run_id=f"tick-{id(strategy)}"
    )
    state = engine_for("nautilus")().run(
        feed, strategy, event_log=log, spec=_spec(capital)
    )
    return log, state


def _events(log: EventLog, kind: str) -> list[dict]:
    return [e.payload for e in log.read() if e.kind == kind]


# --- hand-authored strategies ------------------------------------------------


class _LimitCrossOnFirstTrade(TickStrategy):
    """A marketable limit BUY (price above the ask) placed on the first trade print."""

    def __init__(self) -> None:
        self._done = False

    def on_trade(self, trade, ctx):
        if self._done:
            return None
        self._done = True
        return [Signal.long(SOL, VENUE, size=5.0, limit_price=100.15)]


class _MarketBuyOnFirstTrade(TickStrategy):
    def __init__(self, size: float) -> None:
        self._size = size
        self._done = False

    def on_trade(self, trade, ctx):
        if self._done:
            return None
        self._done = True
        return [Signal.long(SOL, VENUE, size=self._size)]


# --- native-fill goldens -----------------------------------------------------


def test_limit_order_crosses_book_and_fills_at_the_best_ask():
    # A BUY limit @100.15 crosses asks [100.0:20]; it takes the best ask at 100.0 (not
    # its own limit price), for the full 5, as a taker. fee = 5 * 100.0 * 0.00045.
    feed = EngineFeed(
        candles=[],
        marks={SOL: [MarkSnapshot(SOL, T0, 100.0, 100.0, VENUE)]},
        trades={SOL: [TradePrint(100.0, 1.0, T0 + 1000)]},
        book_deltas={SOL: _book(T0, asks=((100.0, 20.0), (100.1, 100.0)))},
    )
    log, state = _run(_LimitCrossOnFirstTrade(), feed)
    fills = _events(log, FILL)
    assert len(fills) == 1
    f = fills[0]
    assert f["price"] == 100.0  # the resting ask, not the 100.15 limit
    assert f["size"] == 5.0
    assert f["liquidity"] == "taker"
    assert f["fidelity_tier"] == "native-l2"
    assert f["is_partial"] is False
    assert f["fee"] == pytest.approx(5.0 * 100.0 * TAKER)  # 0.225
    pos = state.position(VENUE, SOL)
    assert pos.side.value == "long"
    assert (pos.size, pos.entry_price) == (5.0, 100.0)
    assert state.account(VENUE).cash == CAP_DECIMAL - Decimal("0.225")


def test_market_order_partials_across_two_levels_with_vwap_entry():
    # A market BUY of 25 vs asks [100.0:20, 100.1:100] fills 20 @ 100.0 then 5 @ 100.1 —
    # two FILLs, each a taker at its level's price, then a VWAP entry of 100.02.
    feed = EngineFeed(
        candles=[],
        marks={SOL: [MarkSnapshot(SOL, T0, 100.0, 100.0, VENUE)]},
        trades={SOL: [TradePrint(100.0, 1.0, T0 + 1000)]},
        book_deltas={SOL: _book(T0, asks=((100.0, 20.0), (100.1, 100.0)))},
    )
    log, state = _run(_MarketBuyOnFirstTrade(25.0), feed)
    fills = _events(log, FILL)
    assert len(fills) == 2
    assert (fills[0]["price"], fills[0]["size"], fills[0]["is_partial"]) == (100.0, 20.0, True)
    assert (fills[1]["price"], fills[1]["size"], fills[1]["is_partial"]) == (100.1, 5.0, False)
    assert fills[0]["fee"] == pytest.approx(20.0 * 100.0 * TAKER)  # 0.9
    assert fills[1]["fee"] == pytest.approx(5.0 * 100.1 * TAKER)  # 0.225225
    pos = state.position(VENUE, SOL)
    assert pos.size == 25.0
    # VWAP = (20*100.0 + 5*100.1) / 25 = 2500.5 / 25 = 100.02
    assert pos.entry_price == pytest.approx(100.02)
    total_fee = Decimal("0.9") + Decimal("0.225225")
    assert state.account(VENUE).cash == CAP_DECIMAL - total_fee


def test_ioc_remainder_cancels_with_order_cancelled():
    # A market BUY of 30 vs total ask depth 25 fills 20 @ 100.0 + 5 @ 100.1, then the
    # 5-lot remainder has nothing to match against → the IOC order cancels (§6.2).
    feed = EngineFeed(
        candles=[],
        marks={SOL: [MarkSnapshot(SOL, T0, 100.0, 100.0, VENUE)]},
        trades={SOL: [TradePrint(100.0, 1.0, T0 + 1000)]},
        book_deltas={SOL: _book(T0, asks=((100.0, 20.0), (100.1, 5.0)))},
    )
    log, state = _run(_MarketBuyOnFirstTrade(30.0), feed)
    fills = _events(log, FILL)
    assert [(f["price"], f["size"]) for f in fills] == [(100.0, 20.0), (100.1, 5.0)]
    cancels = _events(log, ORDER_CANCELLED)
    assert len(cancels) == 1
    assert cancels[0]["reason"] == "ioc_remainder"
    # Only the fillable 25 landed on the book; the remainder never became a position.
    assert state.position(VENUE, SOL).size == 25.0


# --- subscribe only the overridden streams (§8.6) ----------------------------


def test_host_subscribes_only_the_overridden_handler_streams(monkeypatch):
    # A trades-only strategy must cause exactly one subscription — the trade stream —
    # so an unrequested quote/book/candle never crosses the GIL into Python (§8.6).
    calls: list[str] = []
    for name in (
        "subscribe_trade_ticks",
        "subscribe_quote_ticks",
        "subscribe_order_book_deltas",
        "subscribe_bars",
        "subscribe_data",
    ):
        original = getattr(TickLaneStrategy, name)

        def _spy(self, *a, _name=name, _orig=original, **k):
            calls.append(_name)
            return _orig(self, *a, **k)

        monkeypatch.setattr(TickLaneStrategy, name, _spy)

    feed = EngineFeed(
        candles=[],
        marks={SOL: [MarkSnapshot(SOL, T0, 100.0, 100.0, VENUE)]},
        trades={SOL: [TradePrint(100.0, 1.0, T0 + 1000)]},
        book_deltas={SOL: _book(T0)},
    )
    _run(_MarketBuyOnFirstTrade(1.0), feed)
    assert calls == ["subscribe_trade_ticks"]


# --- on_funding sees predicted rows only (§6.4) ------------------------------


class _RecordFunding(TickStrategy):
    def __init__(self) -> None:
        self.seen: list[tuple[str, float]] = []

    def on_funding(self, rate, ctx):
        self.seen.append((rate.rate_type, rate.rate_hourly))
        return []


def _funding_row(ts: int, rate: float, rate_type: str):
    from flint.core.models import FundingRate

    return FundingRate(
        market=SOL, ts=ts, rate_hourly=rate, interval_s=3600,
        price_basis="oracle", rate_type=rate_type, venue=VENUE,
    )


def test_on_funding_receives_only_predicted_rows():
    strat = _RecordFunding()
    feed = EngineFeed(
        candles=[],
        marks={SOL: [MarkSnapshot(SOL, T0 + i * 1000, 100.0, 100.0, VENUE) for i in range(4)]},
        trades={SOL: [TradePrint(100.0, 1.0, T0 + 1000), TradePrint(100.0, 1.0, T0 + 6000)]},
        book_deltas={SOL: _book(T0)},
        funding={
            SOL: [
                _funding_row(T0 + 2000, 0.02, "predicted"),
                _funding_row(T0 + 3000, 0.0001, "final"),  # settles via the module, not on_funding
            ]
        },
    )
    _run(strat, feed)
    # The final row never reached the handler; only the predicted observation did.
    assert strat.seen == [("predicted", 0.02)]


# --- funding settles between trades via process() (§6.4) ---------------------


def test_funding_settles_between_trades_through_process():
    # Long 10 on the first trade; a FINAL row dated between the two trades settles in a
    # later exchange step (process-driven), stamped at the row ts — the §6.4 −$0.10.
    feed = EngineFeed(
        candles=[],
        marks={SOL: [MarkSnapshot(SOL, T0 + t, 100.0, 100.0, VENUE) for t in (0, 5000, 10000)]},
        trades={SOL: [TradePrint(100.0, 1.0, T0 + 1000), TradePrint(100.0, 1.0, T0 + 10000)]},
        book_deltas={SOL: _book(T0, asks=((100.0, 50.0),))},
        funding={SOL: [_funding_row(T0 + 5000, 0.0001, "final")]},
    )
    log, state = _run(_MarketBuyOnFirstTrade(10.0), feed)
    funding = _events(log, FUNDING)
    assert len(funding) == 1
    ev = funding[0]
    expected = settlement_payment(_Pos(+1, 10.0), 0.0001, 100.0, 3600)
    assert Decimal(ev["amount"]) == expected == Decimal("-0.10")
    assert ev["settled_rate_hourly"] == 0.0001
    assert ev["size"] == 10.0
    assert ev["oracle_price"] == 100.0
    assert ev["price_basis"] == "oracle"
    assert ev["rate_type"] == "final"
    assert ev["rate_capped"] is False
    # Stamped at the row ts, which lies strictly between the two trade prints.
    settle = next(e for e in log.read() if e.kind == FUNDING)
    assert settle.ts == T0 + 5000
    assert T0 + 1000 < settle.ts < T0 + 10000
    # The fill (from trade 1) is logged before the settlement — funding follows the open.
    kinds = [e.kind for e in log.read()]
    assert kinds.index(FILL) < kinds.index(FUNDING)
    assert state.account(VENUE).funding_paid == Decimal("-0.10")


class _Pos:
    """A minimal position stand-in for the pure ``settlement_payment`` in assertions."""

    def __init__(self, sign: int, size: float) -> None:
        from flint.core.models import Side

        self.side = Side.LONG if sign > 0 else Side.SHORT
        self.size = size


# --- tick liquidation on a mark (§6.5) ---------------------------------------


def test_mark_trips_maintenance_liquidates_and_flattens():
    # Long 10 @ 100 on ~$50 (20x). A later mark at 97 drops equity below maintenance;
    # the liquidation module's process values on the recorded mark (exact, not
    # ambiguous), flattens through the host, and both books agree at run end.
    feed = EngineFeed(
        candles=[],
        marks={
            SOL: [
                MarkSnapshot(SOL, T0, 100.0, 100.0, VENUE),
                MarkSnapshot(SOL, T0 + 8000, 97.0, 97.0, VENUE),
            ]
        },
        trades={SOL: [TradePrint(100.0, 1.0, T0 + 1000), TradePrint(97.0, 1.0, T0 + 9000)]},
        book_deltas={SOL: _book(T0, asks=((100.0, 50.0),))},
    )
    log, state = _run(_MarketBuyOnFirstTrade(10.0), feed, capital="50")
    liqs = _events(log, LIQUIDATION)
    assert len(liqs) == 1
    liq = liqs[0]
    assert liq["market"] == SOL
    assert liq["mark_price"] == 97.0  # priced on the recorded mark
    assert liq["intrabar_ambiguous"] is False  # a tick mark is an exact observation
    assert liq["margin_mode"] == "cross"
    assert liq["backstop"] is False
    # Flattened on both books (a clean return means reconciliation held at run end).
    assert state.position(VENUE, SOL) is None
    assert state.account(VENUE).realized_pnl < 0


# --- real committed fragment: on_candle == CandleAggregator + determinism -----


def _fx(name: str) -> bytes:
    return gzip.decompress((FIXTURES / name).read_bytes())


def _real_trades() -> list[TradePrint]:
    tables = TardisCsvClient.parse(
        "trades", _fx("hyperliquid_trades_2026-06-01_SOL.csv.gz"), market="SOL", venue=VENUE
    )
    return [
        TradePrint(price=r["price"], size=r["size"], ts=r["ts"])
        for r in tables[Kind.TRADES].to_pylist()
    ]


def _real_book_deltas() -> list[BookDelta]:
    tables = TardisCsvClient.parse(
        "incremental_book_L2",
        _fx("hyperliquid_incremental_book_L2_2026-06-01_SOL.csv.gz"),
        market="SOL",
        venue=VENUE,
    )
    return [
        BookDelta(
            market="SOL", ts=r["ts"], seq=r["seq"], side=r["side"],
            price=r["px"], size=r["sz"], is_snapshot=r["is_snapshot"], venue=VENUE,
        )
        for r in tables[Kind.BOOK_DELTA].to_pylist()
    ]


class _RecordCandles(TickStrategy):
    def __init__(self) -> None:
        self.candles: list[tuple] = []

    def on_candle(self, candle, ctx):
        self.candles.append(
            (candle.ts, candle.open, candle.high, candle.low, candle.close, candle.volume)
        )
        return []


def test_on_candle_over_the_real_tape_equals_the_candle_aggregator():
    # The candles the strategy's on_candle sees (Nautilus INTERNAL aggregation of the
    # real trade prints) equal Flint's CandleAggregator over the same fragment (§8.6).
    from tests.parity.real_fragment import real_fragment_candles

    strat = _RecordCandles()
    feed = EngineFeed(
        candles=[],
        marks={},
        trades={"SOL": _real_trades()},
        book_deltas={"SOL": _real_book_deltas()},
    )
    # The real fragment's market symbol is the raw coin "SOL" (Tardis is per-coin).
    _run_market(strat, feed, market="SOL")
    expected = [
        (c.ts, c.open, c.high, c.low, c.close, c.volume) for c in real_fragment_candles()
    ]
    assert expected, "the real fragment should close at least one 60s candle"
    assert len(strat.candles) == len(expected)
    # ts + OHLC are bit-exact: prices are observed at bar boundaries, never summed.
    # Volume is the one accumulated field, so Flint's CandleAggregator (float sum) and
    # Nautilus's fixed-point ``Quantity`` sum of the same 2-decimal (size_precision)
    # trade sizes agree exactly *at the size precision* and differ only by float
    # accumulation noise below it (~1e-12 over this tape) — a genuine missing/extra
    # trade would move volume by at least one size increment (0.01), far above this.
    size_precision = HYPERLIQUID.size_decimals
    for got, exp in zip(strat.candles, expected):
        assert got[:5] == exp[:5]
        assert round(got[5], size_precision) == round(exp[5], size_precision)


def _run_market(strategy, feed: EngineFeed, *, market: str):
    """Run a tick feed whose market symbol is ``market`` (the real fragment's raw coin)."""
    spec = EngineRunSpec(
        config=EngineConfig(), venue_spec=HYPERLIQUID, initial_capital=CAP,
        fund_venue=VENUE, mark_policy="close_derived",
    )
    log = EventLog(InMemoryUserData(), TenantContext.local(), run_id=f"tick-{id(strategy)}")
    state = engine_for("nautilus")().run(feed, strategy, event_log=log, spec=spec)
    return log, state


class _BuyOnceReal(TickStrategy):
    def __init__(self) -> None:
        self._done = False

    def on_trade(self, trade, ctx):
        if self._done:
            return None
        self._done = True
        return [Signal.long("SOL", VENUE, size=1.0)]


def test_two_runs_on_the_real_fragment_are_byte_identical():
    # Determinism (§19.4): a strategy that trades once against the real recorded book
    # produces the identical event log across two independent runs.
    def once():
        feed = EngineFeed(
            candles=[], marks={},
            trades={"SOL": _real_trades()},
            book_deltas={"SOL": _real_book_deltas()},
        )
        log, _ = _run_market(_BuyOnceReal(), feed, market="SOL")
        return [e.to_row() for e in log.read()]

    assert once() == once()


# --- tearsheet fold + invariants (§19.2) -------------------------------------


def test_a_full_run_folds_a_clean_tearsheet():
    feed = EngineFeed(
        candles=[],
        marks={SOL: [MarkSnapshot(SOL, T0, 100.0, 100.0, VENUE)]},
        trades={SOL: [TradePrint(100.0, 1.0, T0 + 1000)]},
        book_deltas={SOL: _book(T0)},
    )
    log, _ = _run(_MarketBuyOnFirstTrade(5.0), feed)
    events = log.read()
    assert any(e.kind == FILL for e in events)
    tearsheet = build_tearsheet(events)
    check_invariants(tearsheet, {})  # every order reaches a terminal state
