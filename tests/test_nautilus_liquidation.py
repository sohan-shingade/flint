"""N5 gate — §6.5 mark liquidation on the Nautilus lane, byte-for-byte (§A5).

The :class:`~flint.engine.nautilus.liquidation.FlintLiquidationModule` builds the
same ``key -> (mark, ambiguous)`` map the legacy ``loop._check_liquidations`` builds
and delegates to the pure ``liquidation/cascade.plan_liquidations`` verbatim, so the
Nautilus lane reproduces the legacy loop's LIQUIDATION events and post-trigger cash
exactly. Positions are opened through **signals** (not pre-seeded) so both books —
the shadow book and the Nautilus cache — track them and the run-end reconciliation
(§19.4) is a real cross-check; each scenario is then byte-compared against the frozen
legacy golden (``tests/parity/goldens/liq_*.json``) on the identical feed — the
legacy engine was deleted in N10.

The gates here are the plan's N5 gates:

* the §6.5 worked example (long 10 SOL, entry 100, ≈$50 margin → liq ≈ 97.5, no
  backstop) byte-exact,
* the **funding-saves-position** ordering golden (§6.1): a position that would
  liquidate at step (4) is rescued by a funding credit at step (3) in the same bar,
* the cross-pool cascade golden (largest-loss-first, running-cash coupling),
* the backstop forfeiture + insurance-shortfall golden,
* the isolated close-whole path,
* the intrabar-ambiguous flag (Tier-C adverse extreme vs recorded mark),
* run-end shadow↔Nautilus reconciliation exact after liquidations (positions zeroed
  on both books, cash matches).

All fixtures are hand-authored (D26). Gated on the ``nautilus`` extra.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytest.importorskip("nautilus_trader")

from flint.adapters import InMemoryUserData  # noqa: E402
from flint.core.models import (  # noqa: E402
    Candle,
    FundingRate,
    MarkSnapshot,
    OrderbookSnapshot,
    Signal,
)
from flint.engine.api import EngineFeed, EngineRunSpec  # noqa: E402
from flint.engine.fills import TradePrint  # noqa: E402
from flint.engine.loop import EngineConfig  # noqa: E402
from flint.engine.portfolio import EventLog  # noqa: E402
from flint.engine.portfolio.events import FUNDING, LIQUIDATION  # noqa: E402
from flint.engine.select import engine_for  # noqa: E402
from flint.ports import TenantContext  # noqa: E402
from flint.venues import HYPERLIQUID  # noqa: E402
from parity.golden_store import assert_matches_golden  # noqa: E402

VENUE = "hyperliquid"
SOL = "SOL-PERP"
ETH = "ETH-PERP"
HOUR_S = 3600
HOUR_MS = 3600 * 1000
T0 = 1_700_000_040_000


def _bar_index(candle: Candle) -> int:
    return round((candle.ts - T0) / HOUR_MS)


# --- hand-authored feed pieces -----------------------------------------------


def _c(i: int, o: float, h: float, low: float, cl: float, market: str = SOL) -> Candle:
    return Candle(
        ts=T0 + i * HOUR_MS,
        open=o,
        high=h,
        low=low,
        close=cl,
        volume=1000.0,
        market=market,
        resolution_s=HOUR_S,
        venue=VENUE,
    )


def _flat(i: int, market: str = SOL, px: float = 100.0) -> Candle:
    """A benign bar pinned around ``px`` — no liquidation pressure."""
    return _c(i, px, px + 0.1, px - 0.1, px, market)


def _mark(ts: int, mark_price: float, index_price: float, market: str = SOL) -> MarkSnapshot:
    return MarkSnapshot(
        market=market, ts=ts, mark_price=mark_price, index_price=index_price, venue=VENUE
    )


def _final(ts: int, rate_hourly: float, market: str = SOL) -> FundingRate:
    return FundingRate(
        market=market,
        ts=ts,
        rate_hourly=rate_hourly,
        interval_s=HOUR_S,
        price_basis="oracle",
        rate_type="final",
        venue=VENUE,
    )


def _book_open() -> dict:
    """A recorded book + trade at bar 1 (the T+1 fill bar) so the long fills at a clean
    100.0 (Tier A) — the §6.5 worked example needs entry 100.00, not a slippage price."""
    book = OrderbookSnapshot(SOL, T0 + HOUR_MS, ((99.9, 20.0),), ((100.0, 20.0),), VENUE)
    return {"books": {SOL: [book]}, "trades": {SOL: [TradePrint(100.0, 1.0, T0 + HOUR_MS)]}}


def _spec(initial_capital: str) -> EngineRunSpec:
    return EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        initial_capital=initial_capital,
        fund_venue=VENUE,
        mark_policy="close_derived",
    )


# --- hand-authored strategies ------------------------------------------------


class _OpenLongAt0:
    """Long ``size`` SOL at bar 0 (fills bar 1 open), then hold."""

    def __init__(self, size: float = 10.0, margin_mode: str = "cross") -> None:
        self._size = size
        self._mode = margin_mode

    def on_candle(self, candle, ctx):
        if candle.market == SOL and _bar_index(candle) == 0:
            return [Signal.long(SOL, VENUE, size=self._size, margin_mode=self._mode)]
        return []


class _OpenSolAndEth:
    """Open a cross SOL long and a cross ETH long, each on its market's first bar."""

    def on_candle(self, candle, ctx):
        if _bar_index(candle) == 0 and candle.market == SOL:
            return [Signal.long(SOL, VENUE, size=10.0)]
        if _bar_index(candle) == 2 and candle.market == ETH:
            return [Signal.long(ETH, VENUE, size=1.0)]
        return []


# --- runners -----------------------------------------------------------------


def _run(engine_name: str, make_strategy, feed: EngineFeed, capital: str):
    log = EventLog(
        InMemoryUserData(), TenantContext.local(), run_id=f"{engine_name}-{id(feed)}"
    )
    state = engine_for(engine_name)().run(
        feed, make_strategy(), event_log=log, spec=_spec(capital)
    )
    return log, state


def _liq_events(log: EventLog) -> list[dict]:
    return [e.payload for e in log.read() if e.kind == LIQUIDATION]


def _kinds(log: EventLog) -> list[str]:
    return [e.kind for e in log.read()]


# --- feeds per scenario ------------------------------------------------------


def _feed_6_5() -> EngineFeed:
    """§6.5 worked example: long 10 @ **100.00** (Tier-A book), ≈$50 margin, dives to 97."""
    candles = [_flat(0), _flat(1), _c(2, 100.0, 100.1, 97.0, 97.0), _flat(3, px=97.0)]
    return EngineFeed(
        candles=candles,
        marks={SOL: [_mark(T0 + 2 * HOUR_MS + 60_000, 97.0, 97.0)]},
        funding={},
        **_book_open(),
    )


def _feed_backstop() -> EngineFeed:
    """A gap far past maintenance (entry 100, dive to 95 on ≈$30) → backstop + shortfall."""
    candles = [_flat(0), _flat(1), _c(2, 100.0, 100.1, 95.0, 95.0), _flat(3, px=95.0)]
    return EngineFeed(
        candles=candles,
        marks={SOL: [_mark(T0 + 2 * HOUR_MS + 60_000, 95.0, 95.0)]},
        funding={},
        **_book_open(),
    )


def _feed_isolated() -> EngineFeed:
    """An isolated long stands alone against its own (zero, on-open) margin (§6.5).

    Fill-opened positions carry no isolated collateral in v1 (allocation-on-open is a
    later refinement, identical on both engines), so an isolated long closes whole the
    instant it is underwater — exercising the isolated path independent of any pool.
    """
    candles = [_flat(0), _flat(1), _c(2, 100.0, 100.1, 96.0, 96.0), _flat(3, px=96.0)]
    return EngineFeed(
        candles=candles,
        marks={SOL: [_mark(T0 + HOUR_MS + 60_000, 99.0, 99.0)]},
        funding={},
    )


def _feed_tier_c() -> EngineFeed:
    """Tier-C (no marks): the dive is evaluated on the candle low, flagged ambiguous."""
    candles = [_flat(0), _flat(1), _c(2, 100.0, 100.1, 96.0, 100.0), _flat(3)]
    return EngineFeed(candles=candles, marks={}, funding={})


def _feed_marked() -> EngineFeed:
    """Same dive with a recorded in-bar mark → path known enough → not ambiguous."""
    candles = [_flat(0), _flat(1), _c(2, 100.0, 100.1, 96.0, 100.0), _flat(3)]
    return EngineFeed(
        candles=candles,
        marks={SOL: [_mark(T0 + 2 * HOUR_MS + 60_000, 96.0, 96.0)]},
        funding={},
    )


def _feed_funding_saves() -> EngineFeed:
    """§6.1 golden: the step-(4) dive would liquidate, but a step-(3) credit rescues it.

    Long 10 @ 100 (Tier-A book) on ≈$50. The dive bar's adverse mark is 97 (equity ≈ 20
    < maint 24.25), but a −3%/hr final settles first on the oracle 100 (long receives
    ≈ +$30), lifting equity over maintenance so the position survives.
    """
    candles = [_flat(0), _flat(1), _c(2, 100.0, 100.1, 97.0, 100.0), _flat(3)]
    return EngineFeed(
        candles=candles,
        marks={SOL: [_mark(T0 + 2 * HOUR_MS + 60_000, 97.0, 100.0)]},
        funding={SOL: [_final(T0 + 2 * HOUR_MS + 60_000, -0.03)]},
        **_book_open(),
    )


def _feed_funding_debits() -> EngineFeed:
    """Mirror image: a +3%/hr debit settles first and pushes the same position under."""
    candles = [_flat(0), _flat(1), _c(2, 100.0, 100.1, 97.0, 100.0), _flat(3)]
    return EngineFeed(
        candles=candles,
        marks={SOL: [_mark(T0 + 2 * HOUR_MS + 60_000, 97.0, 100.0)]},
        funding={SOL: [_final(T0 + 2 * HOUR_MS + 60_000, 0.03)]},
        **_book_open(),
    )


def _feed_cross_pool() -> EngineFeed:
    """Two cross legs share one pool; SOL craters, the larger loss closes first (§6.5).

    SOL opens (bar 0, fills bar 1), ETH opens (bar 2, fills bar 3, benign → carries a
    100 mark), then SOL dives to 98 at bar 4. The pool breaches; SOL (the larger loss)
    liquidates and the pool then clears, so ETH survives.
    """
    candles = [
        _flat(0, SOL),
        _flat(1, SOL),
        _flat(2, ETH),
        _flat(3, ETH),
        _c(4, 100.0, 100.1, 98.0, 98.0, SOL),
        _flat(5, SOL, px=98.0),
    ]
    return EngineFeed(
        candles=candles,
        marks={
            ETH: [_mark(T0 + 3 * HOUR_MS + 60_000, 100.0, 100.0, ETH)],
            SOL: [_mark(T0 + 4 * HOUR_MS + 60_000, 98.0, 98.0, SOL)],
        },
        funding={},
    )


# case -> (strategy factory, feed factory, capital)
_PARITY_CASES = {
    "golden_6_5": (lambda: _OpenLongAt0(), _feed_6_5, "50"),
    "backstop_forfeiture": (lambda: _OpenLongAt0(), _feed_backstop, "30"),
    "isolated_close_whole": (
        lambda: _OpenLongAt0(margin_mode="isolated"),
        _feed_isolated,
        "50",
    ),
    "tier_c_ambiguous": (lambda: _OpenLongAt0(), _feed_tier_c, "50"),
    "recorded_mark_not_ambiguous": (lambda: _OpenLongAt0(), _feed_marked, "50"),
    "funding_saves_position": (lambda: _OpenLongAt0(), _feed_funding_saves, "50"),
    "funding_debit_liquidates": (lambda: _OpenLongAt0(), _feed_funding_debits, "50"),
    "cross_pool_cascade": (lambda: _OpenSolAndEth(), _feed_cross_pool, "30"),
}


# --- the byte-for-byte gate --------------------------------------------------


@pytest.mark.parametrize("case", list(_PARITY_CASES))
def test_liquidation_matches_frozen_golden_byte_for_byte(case):
    make_strategy, make_feed, capital = _PARITY_CASES[case]
    nautilus_log, _ = _run("nautilus", make_strategy, make_feed(), capital)
    assert_matches_golden(nautilus_log, f"liq_{case}")


# --- §6.5 worked-example value golden ----------------------------------------


def test_6_5_worked_example_liq_price_and_no_backstop():
    log, state = _run("nautilus", lambda: _OpenLongAt0(), _feed_6_5(), "50")
    liqs = _liq_events(log)
    assert len(liqs) == 1
    liq = liqs[0]
    # The HL cross formula (≈97.44) rounds to the spec's ≈97.5 (§6.5).
    assert abs(liq["liq_price"] - 97.5) < 0.1
    assert liq["margin_mode"] == "cross"
    assert liq["backstop"] is False
    assert liq["insurance_fund_solvent"] is True
    assert liq["adl_rank"] == "pnl_pct_x_leverage"
    assert Decimal(liq["insurance_shortfall"]) == Decimal("0")
    assert liq["mark_price"] == 97.0  # priced on the MARK, not the close
    # The position is gone on both books (reconciliation ran clean at run end).
    assert state.position(VENUE, SOL) is None


def test_backstop_forfeits_margin_and_records_shortfall():
    log, state = _run("nautilus", lambda: _OpenLongAt0(), _feed_backstop(), "30")
    liq = _liq_events(log)[0]
    assert liq["backstop"] is True
    # The solvent vault caps the loss at the backing (realized == −cash-before) and
    # records the covered gap; the account is left at exactly zero (§6.5).
    assert Decimal(liq["insurance_shortfall"]) > 0
    assert Decimal(liq["realized_pnl"]) == -_cash_before_liq(log)
    assert state.account(VENUE).cash == Decimal("0")  # vault absorbs the rest


def _cash_before_liq(log: EventLog) -> Decimal:
    """The shadow cash the instant before the liquidation (its realized = −backing)."""
    cash = Decimal("0")
    for e in log.read():
        if e.kind == LIQUIDATION:
            return cash
        if e.kind == "equity":
            cash = Decimal(e.payload["cash"])
    raise AssertionError("no liquidation in the log")


def test_isolated_position_closes_whole_on_its_own_path():
    log, _ = _run(
        "nautilus", lambda: _OpenLongAt0(margin_mode="isolated"), _feed_isolated(), "50"
    )
    liq = _liq_events(log)[0]
    assert liq["margin_mode"] == "isolated"


def test_tier_c_liquidation_is_flagged_ambiguous():
    log, _ = _run("nautilus", lambda: _OpenLongAt0(), _feed_tier_c(), "50")
    liq = _liq_events(log)[0]
    assert liq["mark_price"] == 96.0  # the candle's adverse extreme, not the close
    assert liq["intrabar_ambiguous"] is True


def test_recorded_mark_liquidation_is_not_flagged_ambiguous():
    log, _ = _run("nautilus", lambda: _OpenLongAt0(), _feed_marked(), "50")
    liq = _liq_events(log)[0]
    assert liq["intrabar_ambiguous"] is False


# --- §6.1 funding-before-liquidation ordering --------------------------------


def test_funding_receipt_rescues_the_position():
    log, state = _run("nautilus", lambda: _OpenLongAt0(), _feed_funding_saves(), "50")
    # Funding settled first (step 3) → the step-(4) check never liquidates.
    assert LIQUIDATION not in _kinds(log)
    assert FUNDING in _kinds(log)
    assert state.position(VENUE, SOL) is not None


def test_funding_debit_pushes_the_position_under():
    log, state = _run("nautilus", lambda: _OpenLongAt0(), _feed_funding_debits(), "50")
    # Same position, opposite-sign rate: the debit settles first and it liquidates.
    kinds = _kinds(log)
    assert kinds.index(FUNDING) < kinds.index(LIQUIDATION)  # funding before liquidation
    assert state.position(VENUE, SOL) is None


# --- cross-pool cascade ------------------------------------------------------


def test_cross_pool_closes_largest_loss_first_and_the_pool_then_clears():
    log, state = _run("nautilus", lambda: _OpenSolAndEth(), _feed_cross_pool(), "30")
    liqs = _liq_events(log)
    assert len(liqs) == 1  # only the largest-loss leg; the pool clears after it
    assert liqs[0]["market"] == SOL
    assert state.position(VENUE, SOL) is None
    assert state.position(VENUE, ETH) is not None  # ETH survives


# --- run-end reconciliation --------------------------------------------------


def test_reconciliation_is_exact_after_a_liquidation():
    # A clean return (no InvariantError) is itself the proof that shadow cash ==
    # Nautilus balance and the shadow position set == the Nautilus cache at run end,
    # with a liquidation having flattened a position on both books mid-run.
    _, state = _run("nautilus", lambda: _OpenLongAt0(), _feed_6_5(), "50")
    assert state.position(VENUE, SOL) is None
    assert state.account(VENUE).realized_pnl < 0  # the liquidation loss landed
