"""D-6.4-replay (Rust ledgers) — Python ↔ Rust parity.

Pin every method's output to 1e-9 across both ledgers. Hot-path
ledgers in the engine read fees/funding/borrow per-bar, so any
divergence between the Rust implementation and the canonical Python
class would silently corrupt PnL.
"""
from __future__ import annotations

import pytest

flint_core = pytest.importorskip("flint_core")

from flint.execution.borrow_ledger import BorrowLedger as PyBL  # noqa: E402
from flint.execution.funding_ledger import FundingLedger as PyFL  # noqa: E402
from flint.models import BorrowSnapshot, FundingRate  # noqa: E402

EPS = 1e-9


# ─── FundingLedger ────────────────────────────────────────────────

class TestFundingLedgerParity:
    def test_latest_matches(self):
        py = PyFL()
        rs = flint_core.FundingLedger()
        for i, rate in enumerate([0.0001, 0.0002, 0.00015]):
            ts = 1700000000 + i * 3600
            py.add(FundingRate(market="SOL-PERP", ts=ts, rate=rate,
                                oracle_price=150.0, mark_price=150.1, slot=0,
                                source="drift"))
            rs.add("SOL-PERP", "drift", ts, rate)
        assert abs((py.latest("SOL-PERP") or 0.0) - (rs.latest("SOL-PERP") or 0.0)) < EPS

    def test_recent_pairs_match(self):
        py = PyFL()
        rs = flint_core.FundingLedger()
        for i in range(20):
            ts = 100 + i
            rate = 0.0001 * i
            py.add(FundingRate(market="SOL-PERP", ts=ts, rate=rate,
                                oracle_price=150.0, mark_price=150.1, slot=0,
                                source="drift"))
            rs.add("SOL-PERP", "drift", ts, rate)
        py_pairs = py.recent("SOL-PERP", 5)
        rs_pairs = rs.recent("SOL-PERP", 5)
        assert len(py_pairs) == len(rs_pairs) == 5
        for (pt, pr), (rt, rr) in zip(py_pairs, rs_pairs):
            assert pt == rt
            assert abs(pr - rr) < EPS

    def test_by_venue_groups_match(self):
        py = PyFL()
        rs = flint_core.FundingLedger()
        for i, (venue, rate) in enumerate([("drift", 0.0001),
                                            ("hyperliquid", 0.0002),
                                            ("drift", 0.00015)]):
            ts = 100 + i
            py.add(FundingRate(market="SOL-PERP", ts=ts, rate=rate,
                                oracle_price=150.0, mark_price=150.1, slot=0,
                                source=venue))
            rs.add("SOL-PERP", venue, ts, rate)
        py_bv = py.by_venue("SOL-PERP", 24)
        rs_bv = rs.by_venue("SOL-PERP", 24)
        assert sorted(py_bv.keys()) == sorted(rs_bv.keys())
        for venue, py_list in py_bv.items():
            rs_list = rs_bv[venue]
            assert len(py_list) == len(rs_list)
            for (pt, pr), (rt, rr) in zip(py_list, rs_list):
                assert pt == rt
                assert abs(pr - rr) < EPS

    def test_unknown_market_returns_empty_in_both(self):
        py = PyFL()
        rs = flint_core.FundingLedger()
        assert py.latest("MISSING") is None
        assert rs.latest("MISSING") is None
        assert py.recent("MISSING") == []
        assert rs.recent("MISSING") == []
        assert py.by_venue("MISSING") == {}
        assert rs.by_venue("MISSING") == {}


# ─── BorrowLedger ─────────────────────────────────────────────────

class TestBorrowLedgerParity:
    def test_latest_and_recent_match(self):
        py = PyBL()
        rs = flint_core.BorrowLedger()
        for i in range(10):
            ts = 100 + i * 60
            rate = 0.0001 + 0.00001 * i
            cum = 0.001 * i
            py.record(BorrowSnapshot(
                market="JUP-PERP", ts=ts, rate_hourly=rate,
                utilization=0.5, cumulative_rate=cum, source="rpc",
            ))
            rs.record("JUP-PERP", ts, rate, cum)
        assert abs((py.latest("JUP-PERP") or 0.0) - (rs.latest("JUP-PERP") or 0.0)) < EPS
        py_r = py.recent("JUP-PERP", 5)
        rs_r = rs.recent("JUP-PERP", 5)
        for (pt, pr), (rt, rr) in zip(py_r, rs_r):
            assert pt == rt
            assert abs(pr - rr) < EPS

    def test_cumulative_at_matches_across_window(self):
        py = PyBL()
        rs = flint_core.BorrowLedger()
        for i, (ts, cum) in enumerate([(10, 0.005), (20, 0.010),
                                        (30, 0.015), (40, 0.020)]):
            py.record(BorrowSnapshot(
                market="JUP-PERP", ts=ts, rate_hourly=0.0001,
                utilization=0.5, cumulative_rate=cum, source="rpc",
            ))
            rs.record("JUP-PERP", ts, 0.0001, cum)
        for query_ts in [5, 10, 15, 20, 35, 100]:
            py_v = py.cumulative_at("JUP-PERP", query_ts)
            rs_v = rs.cumulative_at("JUP-PERP", query_ts)
            if py_v is None:
                assert rs_v is None, f"py None vs rs {rs_v} at ts={query_ts}"
            else:
                assert rs_v is not None
                assert abs(py_v - rs_v) < EPS

    def test_total_paid_and_payments(self):
        py = PyBL()
        rs = flint_core.BorrowLedger()
        for amount in [1.5, 2.0, 0.75]:
            py.add_paid(amount)
            py.record_payment({
                "market": "JUP-PERP", "ts": 100, "cost": amount,
                "cum_entry": 0.001, "cum_exit": 0.002,
            })
            rs.add_paid(amount)
            rs.record_payment("JUP-PERP", 100, amount, 0.001, 0.002)
        assert abs(py.total_paid - rs.total_paid) < EPS
        assert len(py.payments) == rs.payments_count()
