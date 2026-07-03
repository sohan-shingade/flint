"""Run Library + reproducibility export tests (slice 6.4, §11.2/§2.10).

The Run Library is the research memory the design pitches against "scattered notebooks":
runs persist through ``UserDataPort`` (never storage directly), tenant-scoped on every
call; ``compare`` warns when runs are not directly comparable; and a reproducibility
bundle re-executes **bit-for-bit** — the event-sourcing promise (§2.10) asserted as an
identical event stream.

D26: every input here is hand-authored (strategy source strings, param dicts, legacy row
dicts, event rows). No synthetic market data — the deterministic reproduction runner is a
pure function of seed+params emitting event dicts, not a fabricated price series.
"""

from __future__ import annotations

import pytest

from flint.adapters import InMemoryUserData
from flint.ports import TenantContext, UserDataPort
from flint.research import (
    DIFFERENT_ENGINE_WARNING,
    DIFFERENT_LAKE_WARNING,
    DIFFERENT_RANGE_WARNING,
    DataSource,
    ReproBundle,
    RunManifest,
    compare,
    content_hash,
    engine_version,
    export_bundle,
    import_legacy_runs,
    legacy_run_manifests,
    list_runs,
    load_run,
    persist_run,
    reproduce,
)

ALICE = TenantContext(tenant_id="alice")
BOB = TenantContext(tenant_id="bob")

# A tiny, real-looking strategy source string (hand-authored — not generated).
STRAT_SRC = "class S(Strategy):\n    def on_candle(self, c, h, ctx):\n        return []\n"


@pytest.fixture
def store() -> UserDataPort:
    return InMemoryUserData()


def _manifest(run_id: str = "run-1", **over) -> RunManifest:
    base = dict(
        run_id=run_id,
        strategy_name="sma_cross",
        strategy_source=STRAT_SRC,
        params={"fast": 5, "slow": 20},
        effective_start_ts=1_700_000_000_000,
        effective_end_ts=1_700_003_600_000,
        fidelity={"A": 0.9, "C": 0.1},
        metrics={"sharpe": 0.2886751, "dsr": 0.12, "max_drawdown": 0.1, "n_trials": 8},
        engine_version="2.0.0.dev0",
        seed=42,
        lake_revision="rev-abc",
        data_manifest=(
            DataSource("hyperliquid", "SOL-PERP", "candles", 1_700_000_000_000,
                       1_700_003_600_000, "rev-abc"),
            DataSource("hyperliquid", "SOL-PERP", "funding", 1_700_000_000_000,
                       1_700_003_600_000, "rev-abc"),
        ),
        tag="baseline",
        note="hypothesis: spread>5bps regime-dependent",
    )
    base.update(over)
    return RunManifest(**base)


# --- persistence + tenant scoping (§2.7) -------------------------------------


def test_persist_then_load_round_trips_every_field(store: UserDataPort):
    m = _manifest()
    persist_run(ALICE, store, m)
    got = load_run(ALICE, store, "run-1")

    assert got.strategy_name == "sma_cross"
    assert got.strategy_source == STRAT_SRC
    assert got.strategy_hash == content_hash(STRAT_SRC)
    assert got.params == {"fast": 5, "slow": 20}
    assert got.effective_start_ts == 1_700_000_000_000
    assert got.effective_end_ts == 1_700_003_600_000
    assert got.fidelity == {"A": 0.9, "C": 0.1}
    assert got.metrics["sharpe"] == pytest.approx(0.2886751)
    assert got.engine_version == "2.0.0.dev0"
    assert got.seed == 42
    assert got.lake_revision == "rev-abc"
    assert got.tag == "baseline"
    assert got.note.startswith("hypothesis")
    assert len(got.data_manifest) == 2
    assert got.data_manifest[0].venue == "hyperliquid"
    assert got.data_manifest[0].lake_revision == "rev-abc"
    assert got.provenance == "native"


def test_seed_none_is_preserved_not_coerced(store: UserDataPort):
    # A genuinely-unknown seed must survive as None (D26 — never faked to 0).
    persist_run(ALICE, store, _manifest(seed=None))
    assert load_run(ALICE, store, "run-1").seed is None


def test_run_is_tenant_scoped_no_cross_read(store: UserDataPort):
    persist_run(ALICE, store, _manifest("a1"))
    persist_run(BOB, store, _manifest("b1"))

    assert load_run(ALICE, store, "a1").run_id == "a1"
    # Bob's run is invisible to Alice — indistinguishable from never-existed (§2.7).
    with pytest.raises(KeyError):
        load_run(ALICE, store, "b1")


def test_list_runs_isolation_and_strategy_filter(store: UserDataPort):
    persist_run(ALICE, store, _manifest("a1", strategy_name="sma_cross"))
    persist_run(ALICE, store, _manifest("a2", strategy_name="breakout"))
    persist_run(BOB, store, _manifest("b1", strategy_name="sma_cross"))

    assert {m.run_id for m in list_runs(ALICE, store)} == {"a1", "a2"}
    assert {m.run_id for m in list_runs(BOB, store)} == {"b1"}
    only_sma = list_runs(ALICE, store, strategy="sma_cross")
    assert {m.run_id for m in only_sma} == {"a1"}


def test_persist_is_idempotent_on_run_id(store: UserDataPort):
    persist_run(ALICE, store, _manifest("run-1", note="v1"))
    persist_run(ALICE, store, _manifest("run-1", note="v2"))
    assert len(list_runs(ALICE, store)) == 1
    assert load_run(ALICE, store, "run-1").note == "v2"


# --- compare (§11.2/§13) -----------------------------------------------------


def test_compare_same_range_has_no_warnings(store: UserDataPort):
    persist_run(ALICE, store, _manifest("r1"))
    persist_run(ALICE, store, _manifest("r2"))
    cmp = compare(ALICE, store, ["r1", "r2"])
    assert cmp.warnings == ()
    assert cmp.run_ids == ("r1", "r2")


def test_compare_different_effective_range_warns(store: UserDataPort):
    persist_run(ALICE, store, _manifest("r1"))
    persist_run(ALICE, store, _manifest("r2", effective_end_ts=1_700_999_999_000))
    cmp = compare(ALICE, store, ["r1", "r2"])
    assert any(DIFFERENT_RANGE_WARNING in w for w in cmp.warnings)
    assert "not directly comparable" in cmp.describe()


def test_compare_different_engine_and_lake_warn(store: UserDataPort):
    persist_run(ALICE, store, _manifest("r1", engine_version="2.0.0.dev0",
                                        lake_revision="rev-abc"))
    persist_run(ALICE, store, _manifest("r2", engine_version="2.1.0",
                                        lake_revision="rev-xyz"))
    cmp = compare(ALICE, store, ["r1", "r2"])
    assert any(DIFFERENT_ENGINE_WARNING in w for w in cmp.warnings)
    assert any(DIFFERENT_LAKE_WARNING in w for w in cmp.warnings)


def test_compare_single_run_has_no_warnings(store: UserDataPort):
    persist_run(ALICE, store, _manifest("r1"))
    cmp = compare(ALICE, store, ["r1"])
    assert cmp.warnings == ()


def test_compare_metric_table_columns_align_with_runs(store: UserDataPort):
    persist_run(ALICE, store, _manifest("r1", metrics={"sharpe": 1.0}))
    persist_run(ALICE, store, _manifest("r2", metrics={"sharpe": 2.0, "sortino": 3.0}))
    table = compare(ALICE, store, ["r1", "r2"]).metric_table()
    assert table["sharpe"] == [1.0, 2.0]
    assert table["sortino"] == [None, 3.0]  # missing on r1 → None, not fabricated


def test_compare_is_tenant_scoped(store: UserDataPort):
    persist_run(ALICE, store, _manifest("a1"))
    persist_run(BOB, store, _manifest("b1"))
    with pytest.raises(KeyError):
        compare(ALICE, store, ["a1", "b1"])


# --- reproducibility bundle (§11.2/§2.10) ------------------------------------

# A recorded event stream (hand-authored, event-log row shape: kind/version/ts/seq/payload).
EVENTS = [
    {"kind": "order", "event_version": 1, "ts": 0, "seq": 0, "payload": {"side": "buy"}},
    {"kind": "fill", "event_version": 1, "ts": 1, "seq": 1, "payload": {"price": 100.0}},
    {"kind": "funding", "event_version": 1, "ts": 2, "seq": 2, "payload": {"amt": "0.5"}},
]


def test_bundle_carries_code_params_manifest_engine_seed_and_events(store: UserDataPort):
    persist_run(ALICE, store, _manifest("run-1"), events=EVENTS)
    b = export_bundle(ALICE, store, "run-1")

    assert b.strategy_source == STRAT_SRC
    assert b.params == {"fast": 5, "slow": 20}
    assert len(b.data_manifest) == 2
    assert b.engine_version == "2.0.0.dev0"
    assert b.seed == 42
    assert b.lake_revision == "rev-abc"
    assert list(b.events) == EVENTS
    assert b.verify_integrity() is True


def test_bundle_integrity_catches_tampered_source(store: UserDataPort):
    persist_run(ALICE, store, _manifest("run-1"), events=EVENTS)
    b = export_bundle(ALICE, store, "run-1")
    tampered = ReproBundle(**{**b.__dict__, "strategy_source": STRAT_SRC + "# evil\n"})
    assert tampered.verify_integrity() is False


def test_bundle_json_round_trips_identically(store: UserDataPort):
    persist_run(ALICE, store, _manifest("run-1"), events=EVENTS)
    b = export_bundle(ALICE, store, "run-1")
    b2 = ReproBundle.from_json(b.to_json())
    assert b2.strategy_hash == b.strategy_hash
    assert list(b2.events) == list(b.events)
    assert b2.params == b.params
    assert b2.seed == b.seed
    assert b2.verify_integrity() is True


def test_export_bundle_is_tenant_scoped(store: UserDataPort):
    persist_run(BOB, store, _manifest("b1"), events=EVENTS)
    with pytest.raises(KeyError):
        export_bundle(ALICE, store, "b1")


# --- event-sourcing: re-execution is bit-for-bit (§2.10) ---------------------


def _deterministic_runner(bundle: ReproBundle):
    """A faithful re-executor: emits an event stream that is a pure function of the
    bundle's seed + params. Not market data (D26) — a reproducibility fixture that
    stands in for the engine driven from the bundle. Given the same seed it always
    emits the same stream; a different seed changes the fill price."""
    seed = bundle.seed if bundle.seed is not None else 0
    fast = bundle.params.get("fast", 0)
    return [
        {"kind": "order", "event_version": 1, "ts": 0, "seq": 0,
         "payload": {"side": "buy", "size": fast}},
        {"kind": "fill", "event_version": 1, "ts": 1, "seq": 1,
         "payload": {"price": 100.0 + seed}},
    ]


def test_reproduce_is_bit_for_bit_identical(store: UserDataPort):
    # Record the exact stream the deterministic runner produces, then re-run it.
    m = _manifest("run-1", seed=7, params={"fast": 3, "slow": 9})
    recorded = _deterministic_runner(
        ReproBundle(run_id="run-1", engine_version="", seed=7,
                    strategy_name="", strategy_source="", strategy_hash="",
                    params={"fast": 3, "slow": 9}, data_manifest=(),
                    effective_start_ts=None, effective_end_ts=None,
                    lake_revision="", events=())
    )
    persist_run(ALICE, store, m, events=recorded)

    b = export_bundle(ALICE, store, "run-1")
    result = reproduce(b, _deterministic_runner)

    assert result.reproduced is True
    assert result.mismatch_index is None
    assert result.n_original == result.n_reproduced == 2
    assert "bit-for-bit" in result.describe()


def test_reproduce_detects_divergence_with_index(store: UserDataPort):
    persist_run(ALICE, store, _manifest("run-1", seed=7), events=EVENTS)
    b = export_bundle(ALICE, store, "run-1")

    def _wrong(bundle):
        # Diverges at the second event.
        out = [dict(e) for e in bundle.events]
        out[1] = {**out[1], "payload": {"price": 999.0}}
        return out

    result = reproduce(b, _wrong)
    assert result.reproduced is False
    assert result.mismatch_index == 1
    assert "NOT reproduced" in result.describe()


def test_reproduce_is_sensitive_to_seed(store: UserDataPort):
    # The same runner under a different seed must NOT reproduce — proves the seed is a
    # real determinism input, not decoration (§2.10 / §11.2).
    recorded = _deterministic_runner(
        ReproBundle(run_id="run-1", engine_version="", seed=7, strategy_name="",
                    strategy_source="", strategy_hash="", params={"fast": 3},
                    data_manifest=(), effective_start_ts=None, effective_end_ts=None,
                    lake_revision="", events=())
    )
    persist_run(ALICE, store, _manifest("run-1", seed=7, params={"fast": 3}),
                events=recorded)
    b = export_bundle(ALICE, store, "run-1")

    # Re-export with a mutated seed and confirm the stream diverges.
    reseeded = ReproBundle(**{**b.__dict__, "seed": 8})
    result = reproduce(reseeded, _deterministic_runner)
    assert result.reproduced is False
    assert result.mismatch_index == 1  # fill price shifts with the seed


def test_reproduce_length_mismatch_reports_none_index(store: UserDataPort):
    persist_run(ALICE, store, _manifest("run-1"), events=EVENTS)
    b = export_bundle(ALICE, store, "run-1")
    result = reproduce(b, lambda bundle: list(bundle.events)[:1])
    assert result.reproduced is False
    assert result.mismatch_index == 1  # prefix matches; length differs at index 1


# --- legacy run-metadata import (task #2 carry-forward, §19.6) ----------------


def test_legacy_manifests_use_honest_sentinels_no_fabrication():
    # Hand-authored legacy rows (run metadata, NOT market data — D26 permits these).
    strategy_rows = [
        {"name": "old_sma", "source": "def strat(): ...", "created_ts": 1_600_000_000_000},
    ]
    journal_rows = [
        {"strategy": "old_sma", "ts": 1_600_000_000_000, "equity": "1000.0"},
        {"strategy": "old_sma", "ts": 1_600_003_600_000, "equity": "1042.5"},
    ]
    [m] = legacy_run_manifests(strategy_rows=strategy_rows, journal_rows=journal_rows)

    assert m.provenance == "legacy"
    assert m.engine_version == "legacy"
    assert m.lake_revision == "legacy"
    assert m.seed is None  # unknown, never fabricated to 0 (D26)
    assert m.strategy_source == "def strat(): ..."
    # Recorded endpoints preserved verbatim as exact strings (money stays exact, §5).
    assert m.metrics["final_equity"] == "1042.5"
    assert m.metrics["n_equity_points"] == 2
    assert m.effective_start_ts == 1_600_000_000_000
    assert m.effective_end_ts == 1_600_003_600_000


def test_legacy_journal_only_strategy_still_inventoried():
    # Equity recorded but the strategy row is gone → still imported, empty source.
    [m] = legacy_run_manifests(
        journal_rows=[{"strategy": "ghost", "ts": 1_600_000_000_000, "equity": "500"}],
    )
    assert m.strategy_name == "ghost"
    assert m.strategy_source == ""
    assert m.strategy_hash == content_hash("")  # honest hash of empty, not faked
    assert m.provenance == "legacy"


def test_import_legacy_runs_persists_tenant_scoped_and_idempotent(store: UserDataPort):
    strategy_rows = [{"name": "old_sma", "source": "x", "created_ts": 1}]
    journal_rows = [{"strategy": "old_sma", "ts": 1, "equity": "1000"}]

    ids = import_legacy_runs(ALICE, store, strategy_rows=strategy_rows,
                             journal_rows=journal_rows)
    assert ids == ["legacy:old_sma"]
    assert load_run(ALICE, store, "legacy:old_sma").provenance == "legacy"

    # Re-import is a no-op (idempotent on the legacy:<name> run_id).
    import_legacy_runs(ALICE, store, strategy_rows=strategy_rows, journal_rows=journal_rows)
    assert len(list_runs(ALICE, store)) == 1

    # Bob sees none of Alice's imported legacy runs (§2.7).
    assert list_runs(BOB, store) == []


def test_engine_version_is_stamped_and_stable():
    v = engine_version()
    assert isinstance(v, str) and v  # non-empty; "unknown" only if metadata absent
