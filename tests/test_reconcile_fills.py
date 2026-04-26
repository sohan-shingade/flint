"""Tests for scripts/reconcile_fills.py matching logic — Phase 1 T1.4."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

# Load the script as a module for direct testing of its pure functions.
_SCRIPT = Path(__file__).parent.parent / "scripts" / "reconcile_fills.py"
_spec = importlib.util.spec_from_file_location("reconcile_fills", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["reconcile_fills"] = _mod
_spec.loader.exec_module(_mod)

Fill = _mod.Fill
_match_fills = _mod._match_fills
reconcile = _mod.reconcile
_price_bps_delta = _mod._price_bps_delta


def _f(ts=1000, market="SOL-PERP", venue="drift", side="long",
       size=1.0, price=150.0, source="engine"):
    return Fill(ts=ts, market=market, venue=venue, side=side,
                size=size, price=price, source=source)


class TestMatching:
    def test_exact_match(self):
        ef = [_f(ts=1000, price=150.0, source="engine")]
        vf = [_f(ts=1000, price=150.0, source="venue")]
        matches, e_only, v_only = _match_fills(ef, vf)
        assert len(matches) == 1
        assert not e_only and not v_only

    def test_ts_within_window(self):
        ef = [_f(ts=1000)]
        vf = [_f(ts=1045)]  # 45s later, within 60s window
        matches, e_only, v_only = _match_fills(ef, vf, ts_window_s=60)
        assert len(matches) == 1

    def test_ts_outside_window(self):
        ef = [_f(ts=1000)]
        vf = [_f(ts=1120)]  # 120s later, outside 60s window
        matches, e_only, v_only = _match_fills(ef, vf, ts_window_s=60)
        assert not matches
        assert len(e_only) == 1 and len(v_only) == 1

    def test_side_mismatch_no_match(self):
        ef = [_f(side="long")]
        vf = [_f(side="short")]
        matches, e_only, v_only = _match_fills(ef, vf)
        assert not matches

    def test_size_mismatch_no_match(self):
        ef = [_f(size=1.0)]
        vf = [_f(size=2.0)]
        matches, e_only, v_only = _match_fills(ef, vf)
        assert not matches

    def test_size_close_enough(self):
        ef = [_f(size=1.0)]
        vf = [_f(size=1.0005)]  # within 0.1% default tol
        matches, _, _ = _match_fills(ef, vf)
        assert len(matches) == 1

    def test_nearest_ts_wins(self):
        ef = [_f(ts=1050)]
        vf_a = _f(ts=1080, source="venue")
        vf_b = _f(ts=1055, source="venue")
        matches, _, _ = _match_fills(ef, [vf_a, vf_b])
        # Should match the closer one (1055)
        assert len(matches) == 1
        assert matches[0][1] is vf_b

    def test_venue_used_once(self):
        ef1 = _f(ts=1000, source="engine")
        ef2 = _f(ts=1010, source="engine")
        vf = _f(ts=1005, source="venue")
        matches, e_only, v_only = _match_fills([ef1, ef2], [vf])
        assert len(matches) == 1
        assert len(e_only) == 1  # the second engine fill has nothing to match
        assert not v_only

    def test_venue_market_mismatch(self):
        ef = [_f(market="SOL-PERP", venue="drift")]
        vf = [_f(market="SOL-PERP", venue="hyperliquid")]
        matches, _, _ = _match_fills(ef, vf)
        assert not matches


class TestPriceBpsDelta:
    def test_zero_venue_price(self):
        assert _price_bps_delta(100.0, 0.0) == 0.0

    def test_positive_delta(self):
        # engine 100.10, venue 100.00 → +10 bps
        assert _price_bps_delta(100.10, 100.00) == pytest.approx(10.0, abs=1e-6)

    def test_negative_delta(self):
        assert _price_bps_delta(99.90, 100.00) == pytest.approx(-10.0, abs=1e-6)


class TestReconcileAggregate:
    def test_summary_numbers(self):
        ef = [_f(ts=1000, price=150.0), _f(ts=2000, price=151.0),
              _f(ts=3000, price=152.0)]
        vf = [_f(ts=1000, price=150.015, source="venue"),  # +1 bps
              _f(ts=2000, price=151.015, source="venue"),  # +1 bps
              _f(ts=3000, price=152.0, source="venue")]    # 0 bps
        result = reconcile(ef, vf)
        assert result["engine_count"] == 3
        assert result["venue_count"] == 3
        assert result["match_count"] == 3
        assert result["engine_only_count"] == 0
        assert result["venue_only_count"] == 0
        # p95 ≤ ~1 bps
        assert result["price_bps_p95"] < 2.0

    def test_orphan_reporting(self):
        ef = [_f(ts=1000), _f(ts=2000)]
        vf = [_f(ts=1000, source="venue")]
        result = reconcile(ef, vf)
        assert result["engine_count"] == 2
        assert result["venue_count"] == 1
        assert result["match_count"] == 1
        assert result["engine_only_count"] == 1
        assert result["venue_only_count"] == 0


class TestBpsHistogram:
    def test_histogram_present_with_matches(self):
        from scripts.reconcile_fills import reconcile
        ef = [_f(ts=1000, price=100.0)]
        # 100.07 vs 100.0 → ~7 bps delta — lands in the 5-10 bucket.
        vf = [_f(ts=1000, price=100.07, source="venue")]
        result = reconcile(ef, vf)
        bins = result["price_bps_histogram"]
        assert isinstance(bins, list)
        assert len(bins) == 8
        assert sum(b["count"] for b in bins) == 1
        five_to_ten = next(b for b in bins if b["lo"] == 5.0)
        assert five_to_ten["count"] == 1

    def test_histogram_open_ended_top_bucket(self):
        from scripts.reconcile_fills import reconcile
        # 200 bps = $2 delta on $100 → catch in the 100+ bucket
        ef = [_f(ts=1000, price=100.0)]
        vf = [_f(ts=1000, price=102.0, source="venue")]
        result = reconcile(ef, vf)
        top = result["price_bps_histogram"][-1]
        assert top["hi"] is None
        assert top["count"] == 1

    def test_histogram_empty_when_no_matches(self):
        from scripts.reconcile_fills import reconcile
        ef = [_f(ts=1000)]
        vf = [_f(ts=99999, source="venue")]  # ts gap > window
        result = reconcile(ef, vf)
        bins = result["price_bps_histogram"]
        assert sum(b["count"] for b in bins) == 0
