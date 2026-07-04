"""Tier-2 end-to-end smoke — a ``TickStrategy`` over the committed Tardis tape.

This module drives the one chain the tick lane has never actually run
end-to-end through the *services* front door:

    committed Tardis bytes -> TardisFetcher/VendorBackfiller
      -> DurableCacheSource (+ asserted CoverageLedger)
      -> DataManager.prepare(granularity="ticks")   (§B7 tier resolution)
      -> §6.4 funding hard gate
      -> run_backtest_source (validate + sandboxed child, D25)
      -> TickStrategy on the Nautilus native-L2 tick lane
      -> structured ok payload with FILL events + a timestamped equity curve.

The engine level of that lane already has goldens
(``test_nautilus_ticklane.py`` runs ``engine_for("nautilus")().run(feed, ...)``
with a hand-built feed carrying trades + book deltas), and the *data* level is
pinned by ``test_data_tiers.py``. What was never composed is the middle: the
``services`` layer that wires a resolved ``PreparedData`` into the tick feed and
sandboxes a user ``TickStrategy``. Composing it here surfaced two real seam gaps
(reported to the team lead; see the xfail reason on the happy-path test), so the
headline test is an ``xfail(strict=True)`` — it documents the composed outcome we
want and flips the suite red the moment the seam is wired, prompting the marker's
removal. The funding-gate / data-chain half *does* compose today and is asserted
green over the real committed tape.

All fixtures are the committed real Tardis fragments (truncated first-of-month
SOL files, ``tests/fixtures/tardis/``) plus a hand-authored FINAL funding series
(a fixed unit input, D26 — the derivative_ticker fixture carries *predicted*
rows at a different time slice, which by §6.4 never satisfies the hard gate).
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from flint.data import (
    DataManager,
    GranularityUnavailableError,
    Kind,
    TimeRange,
)
from flint.data.ingest.vendors import TardisFetcher, VendorBackfiller
from flint.data.store import DurableCacheSource
from flint.ports import TenantContext

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tardis"
META_URL = "https://api.tardis.dev/v1/exchanges/hyperliquid"
VENUE = "hyperliquid"
MARKET = "SOL-PERP"
ALICE = TenantContext(tenant_id="alice")

# The whole 2026-06-01 fixture day; the fetcher clips to what the truncated files
# actually hold. The trades fragment spans the window below (300 real prints).
DAY_START_MS = 1_780_272_000_000
DAY_MS = 86_400_000
# Exact bounds of the committed trades fragment (first print ts .. last print ts+1),
# so the requested window contains every recorded trade and no empty tail.
TRADES_START_MS = 1_780_272_002_861
TRADES_END_MS = 1_780_272_185_650


def _dataset_url(dataset: str) -> str:
    return f"https://datasets.tardis.dev/v1/hyperliquid/{dataset}/2026/06/01/SOL.csv.gz"


class _FakeBytesTransport:
    """Recorded-bytes transport (the ``test_data_vendors`` pattern): URL -> bytes."""

    def __init__(self, responses: dict[str, bytes]) -> None:
        self._responses = responses

    def get_bytes(self, url: str, *, headers: dict | None = None) -> bytes | None:
        return self._responses.get(url)


class _FakeSecrets:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get_secret(self, tenant: TenantContext, name: str) -> str | None:
        return self._secrets.get(name)


def _final_funding_rows() -> pa.Table:
    """A hand-authored FINAL/settled funding series covering the trades window.

    Unit input (D26): the committed derivative_ticker fixture carries only
    *predicted* rows, and at a later time slice, so the §6.4 hard gate (which
    consults the FINAL series alone) needs a settled series authored by hand. The
    envelope [first ts, last ts] covers the requested window so the gate passes.
    """
    ts = [TRADES_START_MS, (TRADES_START_MS + TRADES_END_MS) // 2, TRADES_END_MS - 1]
    n = len(ts)
    return pa.table(
        {
            "ts": ts,
            "rate_hourly": [0.001] * n,
            "interval_s": [3600] * n,
            "price_basis": ["oracle"] * n,
            "rate_type": ["final"] * n,
            "venue": [VENUE] * n,
            "market": [MARKET] * n,
        }
    )


def _tardis_store(tmp_path: Path, *, with_final_funding: bool) -> DurableCacheSource:
    """Backfill the real trades tape into a DurableCacheSource and assert coverage.

    Mirrors ``test_data_vendors`` (fetcher over recorded bytes) into a durable
    lake, then asserts the TRADES coverage ledger for the fragment window — a
    tick-scale kind never self-asserts coverage on ``store`` (§9.0: its ingester
    owns the assertion), so the ledger stands in for the recorder/vendor ingester.
    FINAL funding (optional) is hand-authored; its non-tick envelope is inferred.
    """
    responses = {
        META_URL: (FIXTURES / "exchange_hyperliquid.json").read_bytes(),
        _dataset_url("trades"): (
            FIXTURES / "hyperliquid_trades_2026-06-01_SOL.csv.gz"
        ).read_bytes(),
    }
    fetcher = TardisFetcher(
        _FakeBytesTransport(responses),
        _FakeSecrets({"TARDIS_API_KEY": "byo-key"}),
        TenantContext(tenant_id="t1"),
        meta_cache_dir=tmp_path / "_meta",
    )
    store = DurableCacheSource(tmp_path / "lake")

    result = VendorBackfiller(fetcher, store).backfill(
        VENUE, MARKET, Kind.TRADES, TimeRange(DAY_START_MS, DAY_START_MS + DAY_MS)
    )
    assert result.rows_written == 300  # the truncated real day fragment
    # The vendor ingester's coverage assertion (tick kinds are ledger-mandatory).
    store.coverage_ledger(VENUE, MARKET, Kind.TRADES).assert_covered(
        TimeRange(TRADES_START_MS, TRADES_END_MS), "tardis"
    )
    if with_final_funding:
        store.store(VENUE, MARKET, Kind.FUNDING, _final_funding_rows())
    return store


# --- the data chain + funding hard gate compose over the real tape (green) ----


def test_ticks_tier_data_chain_composes_and_the_funding_gate_fires(tmp_path):
    """The Tier-2 *data* half, end-to-end over the committed Tardis trades tape.

    Two halves, both asserted against the real fragment through a DurableCacheSource:

    1. With hand-authored FINAL funding, ``prepare(granularity="ticks")`` resolves
       the ticks tier (§B7) over the exact fragment window and lands all 300 real
       trade prints in ``prepared.tables`` — the data chain composes and the tick
       tape reaches the composition layer.
    2. Without FINAL funding, the same request is a structured
       ``GranularityUnavailableError`` (§6.4/§B7 — the ticks tier requires the
       settled funding series, which the predicted-only fixture never provides).
       The per-kind coverage names the gap precisely: TRADES fully covered, FUNDING
       empty. This is the payload ``services`` renders as a ``verdict="rejected"``
       run (never a stack trace, §19.1) — the funding hard gate the engine relies on.
    """
    window = TimeRange(TRADES_START_MS, TRADES_END_MS)
    kinds = [Kind.CANDLES, Kind.FUNDING, Kind.OI]  # services.backtest._KINDS

    # (1) Happy data path: the ticks tier resolves and carries the real trades.
    dm = DataManager(sources=[_tardis_store(tmp_path / "ok", with_final_funding=True)])
    prepared = dm.prepare([MARKET], [VENUE], kinds, window, granularity="ticks")
    assert prepared.granularity == "ticks"
    assert not prepared.clipped
    trades = prepared.tables[(VENUE, MARKET, Kind.TRADES)]
    assert trades.num_rows == 300  # the whole real fragment reached prepared.tables

    # (2) The §6.4 funding hard gate on the same tier, FINAL series absent.
    dm_nofund = DataManager(
        sources=[_tardis_store(tmp_path / "gap", with_final_funding=False)]
    )
    with pytest.raises(GranularityUnavailableError) as err:
        dm_nofund.prepare([MARKET], [VENUE], kinds, window, granularity="ticks")

    exc = err.value
    assert exc.requested_tier == "ticks"
    leg_cov = next(iter(exc.coverage.values()))
    # The structured rejection names the gap: trades present, settled funding empty.
    assert leg_cov[Kind.TRADES].ranges == (window,)
    assert leg_cov[Kind.FUNDING].is_empty


# --- the full services chain: TickStrategy -> tick lane -> FILLs (xfail) -------

_TICK_SOURCE = """
from flint.strategy import TickStrategy
from flint.core.models import Signal


class BuyOnce(TickStrategy):
    def on_trade(self, trade, ctx):
        if getattr(self, "_bought", False):
            return None
        self._bought = True
        return [Signal.long("SOL-PERP", "hyperliquid", size=1.0)]
"""


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Tier-2 seam not wired: a TickStrategy cannot run end-to-end through "
        "run_backtest_source. Two real services-layer gaps, either fatal: "
        "(1) validate_strategy's probe (_DISPATCH_STRATEGY) calls the bar signature "
        "on_candle(candle, history, ctx) on any *Strategy subclass, but "
        "TickStrategy.on_candle(candle, ctx) takes two args -> spurious TypeError -> "
        "verdict='invalid' before the engine; (2) build_engine_inputs / _assemble "
        "(and the run_backtest_in_sandbox codec + _child_backtest feed) carry only "
        "candles/funding/marks, so the TRADES/QUOTES/BOOK_DELTA in prepared.tables "
        "never reach the tick feed -> on_trade never fires -> no FILL. Remove this "
        "marker when both seams are wired."
    ),
)
def test_tickstrategy_runs_through_services_over_the_tape_end_to_end(tmp_path):
    """The headline Tier-2 chain: a user ``TickStrategy`` walked by the native-L2
    tick lane through ``run_backtest_source(granularity="ticks")`` over the real
    committed Tardis trades tape, producing a structured ``ok`` run with fills.

    Asserts the *composed* outcome: verdict ok, engine stamped ``nautilus`` at the
    resolved ``ticks`` granularity, at least one FILL in the tenant-scoped event
    log, a non-empty ``[ts, value]`` equity curve, and a persisted run record.
    Fails today at the first seam gap (see the xfail reason); it is the executable
    definition of "the tick lane composes through services".
    """
    pytest.importorskip("nautilus_trader")
    from flint.adapters import InMemoryUserData
    from flint.engine.portfolio import EventLog
    from flint.engine.portfolio.events import FILL
    from flint.services import run_backtest_source

    store = _tardis_store(tmp_path, with_final_funding=True)
    data = DataManager(sources=[store])
    user_data = InMemoryUserData()

    outcome = run_backtest_source(
        ALICE,
        source=_TICK_SOURCE,
        run_id="tick-e2e",
        universe=(MARKET,),
        venues=(VENUE,),
        start_ms=TRADES_START_MS,
        end_ms=TRADES_END_MS,
        resolution_s=60,
        granularity="ticks",
        engine="auto",
        user_data=user_data,
        data=data,
    )

    assert outcome.verdict == "ok"
    assert outcome.summary["engine"] == "nautilus"
    assert outcome.summary["granularity"] == "ticks"

    events = EventLog(user_data, ALICE, "tick-e2e").read()
    fills = [e for e in events if e.kind == FILL]
    assert fills, "a BuyOnce TickStrategy over the real trade tape must fill once"

    equity_curve = outcome.summary["equity_curve"]
    assert equity_curve and all(len(pt) == 2 for pt in equity_curve)

    record = user_data.load_run(ALICE, "tick-e2e")
    assert record.status == "done"
