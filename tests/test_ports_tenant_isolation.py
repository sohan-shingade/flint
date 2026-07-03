"""The two-tenant cross-leak test + port contract tests (§2.7).

The design promises the tenant seam is *real, not claimed*. These tests are that
proof: two ``TenantContext``s write through ``UserDataPort`` and neither can read
the other's rows, and the port signatures themselves enforce which calls are
tenant-scoped and which are not.
"""

from __future__ import annotations

import inspect

import pyarrow as pa
import pytest

from flint.adapters import (
    InMemoryEventBus,
    InMemoryMarketData,
    InMemoryUserData,
    InProcessJobRunner,
)
from flint.ports import (
    MarketDataPort,
    ResourceQuota,
    RunRecord,
    TenantContext,
    UserDataPort,
)

ALICE = TenantContext(tenant_id="alice")
BOB = TenantContext(tenant_id="bob")


# --- the two-tenant cross-leak test (§2.7) --------------------------------


def test_tenant_cannot_read_other_tenants_run():
    store = InMemoryUserData()
    store.save_run(ALICE, RunRecord(run_id="r1"))
    store.save_run(BOB, RunRecord(run_id="r2"))

    # Each tenant reads only its own row.
    assert store.load_run(ALICE, "r1").run_id == "r1"
    assert store.load_run(BOB, "r2").run_id == "r2"

    # A cross-tenant load fails exactly as if the row did not exist.
    with pytest.raises(KeyError):
        store.load_run(BOB, "r1")
    with pytest.raises(KeyError):
        store.load_run(ALICE, "r2")


def test_list_runs_never_shows_another_tenant():
    store = InMemoryUserData()
    store.save_run(ALICE, RunRecord(run_id="r1"))
    store.save_run(ALICE, RunRecord(run_id="r2"))
    store.save_run(BOB, RunRecord(run_id="r3"))

    assert {r.run_id for r in store.list_runs(ALICE)} == {"r1", "r2"}
    assert {r.run_id for r in store.list_runs(BOB)} == {"r3"}


def test_cross_leak_error_does_not_reveal_existence():
    # The error for "owned by another tenant" is indistinguishable from
    # "never existed" — a probing tenant learns nothing.
    store = InMemoryUserData()
    store.save_run(ALICE, RunRecord(run_id="secret"))

    with pytest.raises(KeyError) as owned:
        store.load_run(BOB, "secret")
    with pytest.raises(KeyError) as absent:
        store.load_run(BOB, "never-existed")
    assert str(owned.value) == str(absent.value)


def test_event_bus_is_tenant_scoped():
    bus = InMemoryEventBus()
    seen: list[str] = []
    bus.subscribe(ALICE, "fills", lambda p: seen.append(p["id"]))

    bus.publish(BOB, "fills", {"id": "bob-fill"})  # different tenant, ignored
    bus.publish(ALICE, "fills", {"id": "alice-fill"})

    assert seen == ["alice-fill"]


# --- port contract tests: the seam is in the signatures --------------------


def _abstract_methods(cls) -> list[str]:
    return sorted(cls.__abstractmethods__)


def test_user_data_port_every_method_takes_tenant_first():
    # UserDataPort is the tenant-scoped half: every call threads a TenantContext.
    for name in _abstract_methods(UserDataPort):
        params = list(inspect.signature(getattr(UserDataPort, name)).parameters)
        assert params[:2] == ["self", "tenant"], name


def test_market_data_port_is_tenant_agnostic():
    # MarketDataPort is shared across tenants — no method may take a tenant.
    for name in _abstract_methods(MarketDataPort):
        params = list(inspect.signature(getattr(MarketDataPort, name)).parameters)
        assert "tenant" not in params, name


def test_all_ports_are_abstract():
    # None of the ports can be instantiated directly — they are promises.
    for port in (UserDataPort, MarketDataPort):
        with pytest.raises(TypeError):
            port()  # type: ignore[abstract]


# --- adapters honour their port contracts ----------------------------------


def test_market_data_arrow_roundtrip_is_half_open():
    store = InMemoryMarketData()
    table = pa.table({"ts": [100, 200, 300], "close": [1.0, 2.0, 3.0]})
    assert store.save_candles("hyperliquid", "SOL-PERP", table) == 3

    got = store.load_candles("hyperliquid", "SOL-PERP", 100, 300)
    assert got.column("ts").to_pylist() == [100, 200]  # 300 excluded (half-open)


def test_job_runner_records_quota_it_cannot_enforce():
    runner = InProcessJobRunner()
    quota = ResourceQuota.default()
    result = runner.submit(ALICE, lambda: 42, quota)
    assert result == 42
    assert runner.last_quota is quota
