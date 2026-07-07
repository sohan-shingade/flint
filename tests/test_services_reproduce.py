"""``reproduce_run`` — the §2.10 promise as a service: re-run, compare streams.

A finished template run is re-executed from its bundle + recorded summary into a
throwaway store and the two event streams must match bit-for-bit. Fixtures are
the hand-authored recorded-fragment tables the backtest service tests use (D26).
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from flint.adapters import InMemoryUserData
from flint.data import DataManager
from flint.data.ranges import Kind
from flint.data.sources import InMemoryCacheSource
from flint.ports import TenantContext
from flint.services import (
    BacktestRequest,
    NotFoundError,
    ValidationError,
    reproduce_run,
    run_backtest,
)

ALICE = TenantContext(tenant_id="alice")
BOB = TenantContext(tenant_id="bob")

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
T0 = 1_700_000_000_000
H = 3_600_000
N = 6


def _candles(n: int = N) -> pa.Table:
    return pa.table(
        {
            "ts": [T0 + i * H for i in range(n)],
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": [1000.0] * n,
            "market": [MARKET] * n,
            "resolution_s": [3600] * n,
            "venue": [VENUE] * n,
        }
    )


def _funding(n: int = N) -> pa.Table:
    ts_pred = [T0 + i * H for i in range(n)]
    ts_fin = [T0 + i * H + 60_000 for i in range(n)]
    return pa.table(
        {
            "ts": ts_pred + ts_fin,
            "rate_hourly": [0.001] * (2 * n),
            "interval_s": [3600] * (2 * n),
            "price_basis": ["oracle"] * (2 * n),
            "rate_type": ["predicted"] * n + ["final"] * n,
            "venue": [VENUE] * (2 * n),
            "market": [MARKET] * (2 * n),
        }
    )


def _covered_end(n: int = N) -> int:
    return T0 + (n - 1) * H + 60_000 + 1


def _data() -> DataManager:
    cache = InMemoryCacheSource()
    cache.store(VENUE, MARKET, Kind.CANDLES, _candles())
    cache.store(VENUE, MARKET, Kind.FUNDING, _funding())
    return DataManager(sources=[cache])


def _request(**over) -> BacktestRequest:
    base = dict(
        run_id="run-1",
        strategy="funding_harvest",
        universe=(MARKET,),
        venues=(VENUE,),
        start_ms=T0,
        end_ms=_covered_end(),
        resolution_s=3600,
        seed=7,
    )
    base.update(over)
    return BacktestRequest(**base)


def test_a_finished_run_reproduces_bit_for_bit() -> None:
    ud = InMemoryUserData()
    data = _data()
    out = run_backtest(ALICE, _request(), user_data=ud, data=data)
    assert out.verdict == "ok"

    payload = reproduce_run(ALICE, "run-1", user_data=ud, data=data)

    assert payload["reproduced"] is True
    assert payload["n_original"] == payload["n_reproduced"] > 0
    assert payload["mismatch_index"] is None
    assert "reproduced bit-for-bit" in payload["detail"]


def test_reproduction_does_not_pollute_the_run_library() -> None:
    ud = InMemoryUserData()
    data = _data()
    run_backtest(ALICE, _request(), user_data=ud, data=data)
    from flint.services import list_runs

    before = [r["run_id"] for r in list_runs(ALICE, user_data=ud)]
    reproduce_run(ALICE, "run-1", user_data=ud, data=data)
    after = [r["run_id"] for r in list_runs(ALICE, user_data=ud)]
    assert before == after == ["run-1"]


def test_unknown_run_is_not_found() -> None:
    with pytest.raises(NotFoundError):
        reproduce_run(ALICE, "no-such-run", user_data=InMemoryUserData(), data=_data())


def test_another_tenants_run_is_not_found() -> None:
    ud = InMemoryUserData()
    data = _data()
    run_backtest(ALICE, _request(), user_data=ud, data=data)
    with pytest.raises(NotFoundError):
        reproduce_run(BOB, "run-1", user_data=ud, data=data)


def test_a_rejected_run_is_refused_loudly() -> None:
    ud = InMemoryUserData()
    # candles only — the funding hard gate rejects the run (§6.4).
    cache = InMemoryCacheSource()
    cache.store(VENUE, MARKET, Kind.CANDLES, _candles())
    data = DataManager(sources=[cache])
    out = run_backtest(ALICE, _request(), user_data=ud, data=data)
    assert out.verdict == "rejected"

    with pytest.raises(ValidationError):
        reproduce_run(ALICE, "run-1", user_data=ud, data=data)
