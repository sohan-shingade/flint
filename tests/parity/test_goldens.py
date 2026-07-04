"""The §19.3 golden set — Nautilus held to the frozen legacy goldens (N10, §19.4).

Every golden is a hand-authored feed (D26) driven through the **Nautilus** bar lane
with a real trading strategy (positions opened via signals so the book, the
Nautilus cache, and the run-end reconciliation are all real). The legacy bar engine
was deleted in N10; each scenario's byte-exact event log was frozen from it at
commit ``300e9b2`` (see :mod:`.golden_store` and ``README.md``), and these tests
assert the Nautilus lane still reproduces it byte-for-byte, zero tolerance.

Two goldens beyond the hand-authored set:

* **cross-market** (``test_cross_market_*``) — two markets sharing timestamps in one
  run. ``EngineFeed`` canonicalizes candles to ``(ts, market)`` at the seam, so the
  Nautilus lane emits shared-ts events in one order regardless of the caller's feed
  order; the two feed orders therefore reproduce the *same* frozen golden.
* **real fragment** (``test_real_fragment_golden``) — one recorded HL day built from
  the committed truncated real Tardis SOL 2026-06-01 fixtures: candles aggregated
  from real trade prints + real predicted funding (settlement absent — see
  ``real_fragment`` for the D26 honesty note).
"""

from __future__ import annotations

import pytest

pytest.importorskip("nautilus_trader")

from flint.core.models import Signal  # noqa: E402
from flint.engine.api import EngineFeed  # noqa: E402
from flint.engine.portfolio.events import FUNDING, LIQUIDATION  # noqa: E402

from . import builders as b  # noqa: E402
from .golden_store import assert_matches_golden  # noqa: E402
from .harness import run_nautilus  # noqa: E402
from .real_fragment import MARKET as REAL_MARKET  # noqa: E402
from .real_fragment import feed_real_fragment  # noqa: E402

pytestmark = pytest.mark.parity


# --- the §19.3 golden set (Nautilus vs frozen golden — byte-exact) -----------
#
# case -> (strategy factory, feed factory, capital). The case name is also the
# golden file stem under goldens/, so the recorder and the test share one key.
GOLDENS = {
    # §6.4 funding worked example (long 10 @ oracle 100, +0.01% → −$0.10).
    "funding_6_4": (b.OpenLongAt1, b.feed_6_4, "100000"),
    # predicted/final divergence window: strategy sees only predicted, money moves
    # only on the final row.
    "predicted_final_divergence": (b.OpenLongAt1, b.feed_divergence, "100000"),
    # two settlements straddling a long→short flip.
    "multi_settlement_flip": (b.FlipMidway, b.feed_multi_flip, "100000"),
    # §6.5 liquidation worked example (long 10 @ 100, ≈$50 margin → liq ≈97.5).
    "liquidation_6_5": (b.OpenLongAt0, b.feed_6_5, "50"),
    # §6.1 funding-saves-position ordering (credit at step 3 rescues step-4 check).
    "funding_saves_position": (b.OpenLongAt0, b.feed_funding_saves, "50"),
    # cross-pool cascade (largest loss first; markets on disjoint bars → no shared ts).
    "cross_pool_cascade": (b.OpenSolAndEth, b.feed_cross_pool, "30"),
    # backstop forfeiture + insurance shortfall.
    "backstop_forfeiture": (b.OpenLongAt0, b.feed_backstop, "30"),
    # Tier-C intrabar-ambiguous liquidation (no marks → candle adverse extreme).
    "intrabar_ambiguous": (b.OpenLongAt0, b.feed_tier_c, "50"),
    # T+1 fill timing (open bar 1 → fill bar 2 open; close bar 4 → fill bar 5).
    "t_plus_1_fill": (b.LongThenClose, b.feed_ohlcv, "100000"),
    # size_usd materialization + residual (notional → size).
    "size_usd_materialization": (b.UsdLong, b.feed_ohlcv, "100000"),
    # oracle-band clip (a taker larger than in-band depth → partial + clip flag).
    "oracle_band_clip": (b.BigTaker, lambda: b.feed_with_book(far_asks=True), "100000"),
    # zero-volume reject (T+1 lands on a zero-volume bar → no_fill).
    "zero_volume_reject": (b.OpenLongAt1, lambda: b.feed_ohlcv(zero_vol_at=2), "100000"),
}


@pytest.mark.parametrize("case", list(GOLDENS))
def test_golden_matches_frozen_legacy_byte_for_byte(case):
    make_strategy, make_feed, capital = GOLDENS[case]
    log = run_nautilus(make_strategy, make_feed(), b.spec(capital), run_id=case)
    assert_matches_golden(log, case)


# --- guards: the goldens actually exercise the behaviour they claim ----------
# (a byte-identical run of two empty logs would "pass" parity vacuously.)


def test_funding_golden_actually_settles():
    log = run_nautilus(b.OpenLongAt1, b.feed_6_4(), b.spec())
    assert any(e.kind == FUNDING for e in log.read())


def test_liquidation_golden_actually_liquidates():
    log = run_nautilus(b.OpenLongAt0, b.feed_6_5(), b.spec("50"))
    assert any(e.kind == LIQUIDATION for e in log.read())


# --- cross-market golden (N3 flag): shared-ts multi-market interleaving -------


def _feed_cross_market(*, sol_first: bool, same_ts: bool) -> EngineFeed:
    """Two markets that share bar timestamps in one run (the N3 stress).

    ``sol_first`` sets the feed order at each shared bar; ``same_ts`` puts both
    funding settlements on the same ts. Real HL funding settles hourly for every
    market at once, so shared-ts multi-market settlement is realistic, not
    contrived.
    """
    order = [(b.SOL, 100.0), (b.ETH, 50.0)] if sol_first else [(b.ETH, 50.0), (b.SOL, 100.0)]
    candles = [b.flat(i, m, px=px) for i in range(6) for m, px in order]
    marks = {
        b.SOL: [b.mark(b.T0 + i * b.HOUR_MS, 100.0, 100.0, b.SOL) for i in range(6)],
        b.ETH: [b.mark(b.T0 + i * b.HOUR_MS, 50.0, 50.0, b.ETH) for i in range(6)],
    }
    sol_ts = b.T0 + 3 * b.HOUR_MS
    eth_ts = sol_ts if same_ts else b.T0 + 2 * b.HOUR_MS
    funding = {b.SOL: [b.final(sol_ts, 0.001, b.SOL)], b.ETH: [b.final(eth_ts, 0.002, b.ETH)]}
    return EngineFeed(candles=candles, marks=marks, funding=funding)


def test_cross_market_shared_ts_byte_parity():
    # SOL-first feed order at shared timestamps. EngineFeed canonicalizes candles to
    # (ts, market), so the Nautilus lane emits the same interleaving the frozen
    # golden captured — the full byte diff holds.
    log = run_nautilus(
        lambda: b.OpenSolAndEth(sol_at=0, eth_at=0),
        _feed_cross_market(sol_first=True, same_ts=True),
        b.spec(),
        run_id="cross_market_shared_ts",
    )
    assert_matches_golden(log, "cross_market_shared_ts")


def test_cross_market_feed_order_is_canonicalized():
    """The N9 fix: EngineFeed normalizes candles to (ts, market), so the feed order
    the caller happened to build in no longer affects the event stream.

    SOL-first and ETH-first feeds over the same shared-ts data both reproduce the
    *same* frozen golden through the Nautilus lane — the divergence the N6 harness
    once flagged is closed at the seam, not merely masked when the caller pre-sorts.
    """
    # EngineFeed canonicalizes at construction regardless of the order passed in.
    sol_first = _feed_cross_market(sol_first=True, same_ts=True)
    eth_first = _feed_cross_market(sol_first=False, same_ts=True)
    assert [(c.ts, c.market) for c in sol_first.candles] == [
        (c.ts, c.market) for c in eth_first.candles
    ], "EngineFeed should sort candles to a canonical (ts, market) order"

    # Both feed orders reproduce the one frozen golden byte-for-byte.
    for feed in (sol_first, eth_first):
        log = run_nautilus(
            lambda: b.OpenSolAndEth(sol_at=0, eth_at=0), feed, b.spec()
        )
        assert_matches_golden(log, "cross_market_shared_ts")


# --- real recorded fragment (one HL day, §19.3) ------------------------------


class _RealFragmentLong:
    """Open a long on the first real candle (fills T+1), then hold — a real
    trading position over the recorded prints."""

    def __init__(self) -> None:
        self._opened = False

    def on_candle(self, candle, ctx):
        if not self._opened:
            self._opened = True
            return [Signal.long(REAL_MARKET, b.VENUE, size=10.0)]
        return []


def test_real_fragment_golden():
    # Candles aggregated from real SOL trade prints + real predicted funding
    # (settlement absent — see real_fragment). Parity holds on the fill / order /
    # per-bar-equity path over real recorded data.
    log = run_nautilus(_RealFragmentLong, feed_real_fragment(), b.spec(), run_id="real_fragment")
    assert_matches_golden(log, "real_fragment")


def test_real_fragment_actually_trades():
    # Guard: the fragment produces a real fill (not a vacuous empty-log parity).
    log = run_nautilus(_RealFragmentLong, feed_real_fragment(), b.spec())
    kinds = [e.kind for e in log.read()]
    assert "fill" in kinds and "equity" in kinds
