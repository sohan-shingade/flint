"""The §19.3 golden set through the consolidated parity harness (N6, §19.4).

Every golden is a hand-authored feed (D26) driven through **both** engines with a
real trading strategy — positions opened via signals so both books (the shadow
book and the Nautilus cache) track them and the run-end reconciliation is a real
cross-check. :func:`~tests.parity.harness.assert_parity` applies both §19.4
layers: input parity first (localizer), then the full byte diff (the contract).

The per-phase tests (``tests/test_nautilus_*.py``) already gate each engine phase;
this suite is the CI-required *superset* the N9 default flip waits on.

Two goldens beyond the hand-authored set:

* **cross-market** (``test_cross_market_*``) — the N3-flagged divergence, now
  verified: two markets sharing timestamps in one run. It is an **xfail** because
  the Nautilus lane and the legacy lane interleave per-market events differently
  at shared timestamps (documented in the test + reported to main). Crucially the
  *input-parity* layer still passes, so the divergence is proven to be ordering,
  never the settlement math.
* **real fragment** (``test_real_fragment_golden``) — one recorded HL day, built
  from the committed truncated real Tardis SOL 2026-06-01 fixtures: candles
  aggregated from real trade prints + real predicted funding (settlement absent,
  see ``real_fragment`` for the D26 honesty note).
"""

from __future__ import annotations

import pytest

pytest.importorskip("nautilus_trader")

from flint.core.models import Signal  # noqa: E402
from flint.engine.api import EngineFeed  # noqa: E402
from flint.engine.portfolio.events import FUNDING, LIQUIDATION  # noqa: E402

from . import builders as b  # noqa: E402
from .harness import (  # noqa: E402
    assert_parity,
    layer_a_matches,
    layer_b_matches,
    parity_diff,
    run_both,
)
from .real_fragment import MARKET as REAL_MARKET  # noqa: E402
from .real_fragment import feed_real_fragment  # noqa: E402

pytestmark = pytest.mark.parity


# --- the §19.3 golden set (single lane — byte-exact) -------------------------
#
# case -> (strategy factory, feed factory, capital)
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
def test_golden_matches_legacy_byte_for_byte(case):
    make_strategy, make_feed, capital = GOLDENS[case]
    legacy_log, nautilus_log = run_both(make_strategy, make_feed(), b.spec(capital))
    assert_parity(legacy_log, nautilus_log)


# --- guards: the goldens actually exercise the behaviour they claim ----------
# (a byte-identical run of two empty logs would "pass" parity vacuously.)


def test_funding_golden_actually_settles():
    legacy_log, _ = run_both(b.OpenLongAt1, b.feed_6_4(), b.spec())
    assert any(e.kind == FUNDING for e in legacy_log.read())


def test_liquidation_golden_actually_liquidates():
    legacy_log, _ = run_both(b.OpenLongAt0, b.feed_6_5(), b.spec("50"))
    assert any(e.kind == LIQUIDATION for e in legacy_log.read())


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


@pytest.mark.xfail(
    reason=(
        "N6 FINDING (N3 flag confirmed, reported to main): the Nautilus lane and the "
        "legacy lane interleave per-market EQUITY/FUNDING events differently when two "
        "markets share bar timestamps. Legacy emits in candle-feed order; the Nautilus "
        "lane emits in its canonical (market-name-sorted) order. The divergence is "
        "event ORDERING only — the input-parity layer (§19.4a) passes, so the same "
        "settlement inputs reach the same pure math in both engines; only the (ts, seq) "
        "interleaving of the surrounding stream differs. Not fixed here (N6 touches no "
        "flint/ source); tracked for the N9 flip. See test_cross_market_divergence_is_"
        "ordering_only for the precise characterisation."
    ),
    strict=True,
)
def test_cross_market_shared_ts_byte_parity():
    # SOL-first feed order at shared timestamps → the legacy interleaving differs
    # from Nautilus's market-sorted one → the full byte diff diverges.
    legacy_log, nautilus_log = run_both(
        lambda: b.OpenSolAndEth(sol_at=0, eth_at=0),
        _feed_cross_market(sol_first=True, same_ts=True),
        b.spec(),
    )
    assert_parity(legacy_log, nautilus_log)


def test_cross_market_divergence_is_ordering_only():
    """Characterise the xfail: layer (a) clean, layer (b) diverges — pure ordering.

    This test PASSES: it pins the finding as ordering-only (not a math/reach bug)
    so the divergence stays understood while ``test_cross_market_shared_ts_byte_
    parity`` documents the byte-diff via xfail.
    """
    # same-ts, SOL-first feed order → byte diff diverges, input parity does not.
    legacy_log, nautilus_log = run_both(
        lambda: b.OpenSolAndEth(sol_at=0, eth_at=0),
        _feed_cross_market(sol_first=True, same_ts=True),
        b.spec(),
    )
    assert layer_a_matches(legacy_log, nautilus_log), (
        "input parity should hold — the settlement math is engine-independent:\n"
        + parity_diff(legacy_log, nautilus_log)
    )
    assert not layer_b_matches(legacy_log, nautilus_log), "expected the byte diff to diverge"

    # When the feed order matches Nautilus's canonical market sort (ETH < SOL),
    # even the byte diff agrees — proving the divergence is feed-order vs canonical
    # order, nothing deeper.
    legacy2, nautilus2 = run_both(
        lambda: b.OpenSolAndEth(sol_at=0, eth_at=0),
        _feed_cross_market(sol_first=False, same_ts=True),
        b.spec(),
    )
    assert layer_b_matches(legacy2, nautilus2), parity_diff(legacy2, nautilus2)


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
    legacy_log, nautilus_log = run_both(
        _RealFragmentLong, feed_real_fragment(), b.spec()
    )
    assert_parity(legacy_log, nautilus_log)


def test_real_fragment_actually_trades():
    # Guard: the fragment produces a real fill (not a vacuous empty-log parity).
    legacy_log, _ = run_both(_RealFragmentLong, feed_real_fragment(), b.spec())
    kinds = [e.kind for e in legacy_log.read()]
    assert "fill" in kinds and "equity" in kinds
