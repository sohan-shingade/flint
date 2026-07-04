"""One-shot recorder: freeze the legacy bar engine's event logs as parity goldens.

Run **once**, at commit ``300e9b2`` (pre-N10), while the legacy bar engine still
existed:

    .venv/bin/python tests/parity/record_goldens.py

It drives every parity scenario — the §19.3 golden set (``test_goldens.py``), the
real recorded fragment, the cross-market interleaving, the ``test_engine_seam.py``
warm-start + seam scenarios, and the per-phase legacy comparisons in
``test_nautilus_funding`` / ``_liquidation`` / ``_bar_lane`` / ``_skeleton`` —
through the **legacy** engine and writes each one's stripped event log (§19.4b) to
``goldens/<name>.json`` via :mod:`golden_store`.

After N10 the legacy engine is deleted, so this script raises
``UnknownEngineError`` if re-run against current history. That is intentional: the
goldens are **frozen artifacts** (§19.4). Regenerating one requires checking out
pre-N10 history — see ``README.md``. The script is kept in the tree as the
provenance record of how the goldens were produced; it is not collected by pytest
(no ``test_`` functions).

The scenario feeds/strategies are imported from the test modules that assert
against these goldens, so the recorded input and the asserted input are one source
of truth — a converted test can never drift from the golden it checks.
"""

from __future__ import annotations

import pathlib
import sys

# The recorder mirrors how pytest imports these modules: the ``parity`` package and
# the top-level ``test_*`` modules both resolve with ``tests/`` on the path, and
# ``flint`` with the repo root on the path. Insert both so the absolute imports
# below work whether or not the invoker set PYTHONPATH.
_HERE = pathlib.Path(__file__).resolve()
_TESTS_DIR = _HERE.parent.parent
_REPO_ROOT = _TESTS_DIR.parent
for _p in (_REPO_ROOT, _TESTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from flint.adapters import InMemoryUserData  # noqa: E402
from flint.engine.api import EngineFeed, EngineRunSpec  # noqa: E402
from flint.engine.loop import EngineConfig, NoopStrategy  # noqa: E402
from flint.engine.portfolio import EventLog  # noqa: E402
from flint.engine.select import engine_for  # noqa: E402
from flint.ports import TenantContext  # noqa: E402
from flint.venues import HYPERLIQUID  # noqa: E402

from parity import builders as pb  # noqa: E402
from parity import golden_store  # noqa: E402


def _run_legacy(make_strategy, feed, spec: EngineRunSpec) -> EventLog:
    """Run one scenario through the legacy bar engine and return its event log."""
    log = EventLog(InMemoryUserData(), TenantContext.local(), run_id="record-legacy")
    engine_for("legacy-bar")().run(feed, make_strategy(), event_log=log, spec=spec)
    return log


def _scenarios() -> dict[str, object]:
    """name -> thunk producing the legacy EventLog for that scenario.

    Thunks (not eager logs) so a fresh feed / seed-state / strategy is built per
    scenario, matching how each test constructs its own inputs.
    """
    scenarios: dict[str, object] = {}

    # --- the §19.3 golden set (parity/test_goldens.py::GOLDENS) ----------------
    from parity.real_fragment import feed_real_fragment
    from parity.test_goldens import GOLDENS, _feed_cross_market, _RealFragmentLong

    for case, (make_strategy, make_feed, capital) in GOLDENS.items():
        scenarios[case] = (
            lambda ms=make_strategy, mf=make_feed, cap=capital: _run_legacy(
                ms, mf(), pb.spec(cap)
            )
        )

    # cross-market shared-ts interleaving (feed-order canonicalization golden).
    scenarios["cross_market_shared_ts"] = lambda: _run_legacy(
        lambda: pb.OpenSolAndEth(sol_at=0, eth_at=0),
        _feed_cross_market(sol_first=True, same_ts=True),
        pb.spec(),
    )
    # one real recorded HL day (candles from real prints + predicted-only funding).
    scenarios["real_fragment"] = lambda: _run_legacy(
        _RealFragmentLong, feed_real_fragment(), pb.spec()
    )

    # --- the engine seam: the N1 scenario + the §6.7 warm-start runs -----------
    import test_engine_seam as seam

    def _seam_spec(**over) -> EngineRunSpec:
        kw = dict(
            config=EngineConfig(),
            venue_spec=HYPERLIQUID,
            fund_venue=seam.VENUE,
            mark_policy="close_derived",
        )
        kw.update(over)
        return EngineRunSpec(**kw)

    # The N1 conformance scenario (routing, T+1, funding, cascade) — recorded so the
    # coverage survives as a Nautilus golden once the legacy "direct run" is gone.
    scenarios["seam_scenario"] = lambda: _run_legacy(
        seam._strategy, seam._scenario_feed(), _seam_spec(initial_capital="1000")
    )
    # §6.7 warm-start: fresh seed state per run (the legacy engine mutates it).
    scenarios["warm_add_to_carried_long"] = lambda: _run_legacy(
        seam._add_long_once, seam._warm_feed(), _seam_spec(initial_state=seam._warm_seed_state())
    )
    scenarios["warm_seed_close"] = lambda: _run_legacy(
        seam._close_once, seam._warm_feed(), _seam_spec(initial_state=seam._warm_seed_state())
    )
    scenarios["warm_partial_reduce"] = lambda: _run_legacy(
        seam._reduce_half_once, seam._warm_feed(), _seam_spec(initial_state=seam._warm_seed_state())
    )

    # --- per-phase legacy comparisons: funding (N4) ----------------------------
    import test_nautilus_funding as nf

    for case, (make_strategy, make_feed) in nf._PARITY_CASES.items():
        scenarios[f"funding_{case}"] = (
            lambda ms=make_strategy, mf=make_feed: _run_legacy(ms, mf(), nf._spec())
        )
    # test_no_final_rows_registers_no_module: a predicted-only feed settles nothing.
    scenarios["funding_no_final"] = lambda: _run_legacy(
        nf._OpenLongHold,
        EngineFeed(
            candles=nf._candles(),
            marks=nf._marks(),
            funding={nf.MARKET: [nf._predicted(nf.T0, 0.05)]},
        ),
        nf._spec(),
    )

    # --- per-phase legacy comparisons: liquidation (N5) ------------------------
    import test_nautilus_liquidation as liq

    for case, (make_strategy, make_feed, capital) in liq._PARITY_CASES.items():
        scenarios[f"liq_{case}"] = (
            lambda ms=make_strategy, mf=make_feed, cap=capital: _run_legacy(
                ms, mf(), liq._spec(cap)
            )
        )

    # --- per-phase legacy comparisons: bar lane (N3) ---------------------------
    import test_nautilus_bar_lane as bl

    for case, (make_strategy, make_feed) in bl.CASES.items():
        scenarios[f"barlane_{case}"] = (
            lambda ms=make_strategy, mf=make_feed: _run_legacy(ms, mf(), bl._spec())
        )

    # --- per-phase legacy comparison: skeleton (N2) EQUITY line ----------------
    import test_nautilus_skeleton as sk

    scenarios["skeleton_noop"] = lambda: _run_legacy(NoopStrategy, sk._feed(), sk._spec())

    return scenarios


def main() -> None:
    scenarios = _scenarios()
    for name, thunk in scenarios.items():
        log = thunk()
        golden_store.write_golden(name, log)
        print(f"wrote goldens/{name}.json ({len(log.read())} events)")
    print(f"\n{len(scenarios)} goldens written to {golden_store.GOLDENS_DIR}")


if __name__ == "__main__":
    main()
