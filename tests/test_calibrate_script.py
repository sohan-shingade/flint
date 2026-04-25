"""Tests for scripts/calibrate.py — Phase 3 T3.6."""
from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

# Load the script as a module (it's not a package).
_SCRIPT = Path(__file__).parent.parent / "scripts" / "calibrate.py"
_spec = importlib.util.spec_from_file_location("calibrate_script", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["calibrate_script"] = _mod
_spec.loader.exec_module(_mod)

_FillSample = _mod._FillSample
_compute_xy = _mod._compute_xy
_fit = _mod._fit
_read_current_coefficient = _mod._read_current_coefficient
_write_coefficient = _mod._write_coefficient
_render = _mod._render
load_fills = _mod.load_fills


def _gen_fills(n=50, noise=0.5, seed=7):
    """Generate synthetic fills where slippage ≈ 0.5 * sqrt(size) (bps)."""
    rng = np.random.default_rng(seed)
    samples = []
    for i in range(n):
        size = float(rng.uniform(0.1, 100.0))
        true_slip = 0.5 * np.sqrt(size)
        realized = true_slip * (1 + noise * rng.normal())
        samples.append(_FillSample(
            ts=1_700_000_000 + i * 60,
            market="SOL-PERP", venue="drift", side="long",
            size=size, price=150.0,
            realized_slippage_bps=max(0.0, realized),
        ))
    return samples


class TestFillIngest:
    def test_load_fills_round_trip(self, tmp_path):
        path = tmp_path / "fills.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts_epoch_s", "market", "venue", "side", "size",
                        "price", "realized_slippage_bps"])
            w.writerow([1700000000, "SOL-PERP", "drift", "long",
                        1.0, 150.0, 2.5])
        samples = load_fills(path)
        assert len(samples) == 1
        assert samples[0].realized_slippage_bps == 2.5

    def test_missing_columns_errors(self, tmp_path):
        path = tmp_path / "bad.csv"
        with open(path, "w", newline="") as f:
            f.write("ts_epoch_s,side,size\n1700000000,long,1.0\n")
        with pytest.raises(SystemExit):
            load_fills(path)


class TestXYExtraction:
    def test_realized_slippage_preferred(self):
        samples = [_FillSample(
            ts=1, market="X", venue="v", side="long",
            size=10.0, price=100.0, realized_slippage_bps=3.0
        )]
        xs, ys = _compute_xy(samples)
        assert xs.tolist() == [10.0]
        assert ys.tolist() == [3.0]

    def test_derived_from_mid_price(self):
        samples = [_FillSample(
            ts=1, market="X", venue="v", side="long",
            size=5.0, price=100.10, mid_price=100.0,
        )]
        xs, ys = _compute_xy(samples)
        # (100.10 - 100.00) / 100.00 * 10000 = 10 bps
        assert xs.tolist() == [5.0]
        assert ys[0] == pytest.approx(10.0, abs=1e-6)

    def test_skips_without_slippage_or_mid(self):
        samples = [_FillSample(
            ts=1, market="X", venue="v", side="long",
            size=5.0, price=100.0,
        )]
        xs, ys = _compute_xy(samples)
        assert len(xs) == 0


class TestFits:
    def test_sqrt_fit_recovers_coefficient(self):
        samples = _gen_fills(n=200, noise=0.1)
        xs, ys = _compute_xy(samples)
        result = _fit(xs, ys, "sqrt")
        # True coefficient is 0.5; allow a generous tolerance under noise.
        assert 0.3 < result.coefficient_a < 0.8
        assert result.cv_r_squared > 0.4

    def test_power_law_fit_runs(self):
        samples = _gen_fills(n=100, noise=0.1)
        xs, ys = _compute_xy(samples)
        result = _fit(xs, ys, "power_law")
        # Just verify it returned sane numbers.
        assert result.coefficient_a > 0
        assert 0.2 < result.exponent_b < 0.8  # sqrt-ish data


class TestYAMLRoundTrip:
    def test_read_then_write(self, tmp_path):
        yaml_path = tmp_path / "flint.yaml"
        import yaml
        yaml_path.write_text(yaml.safe_dump({
            "venues": {"drift": {"impact_coefficient": 0.5}}
        }), encoding="utf-8")
        assert _read_current_coefficient(yaml_path, "drift") == 0.5
        _write_coefficient(yaml_path, "drift", 0.6)
        assert _read_current_coefficient(yaml_path, "drift") == 0.6

    def test_read_missing_file_returns_none(self, tmp_path):
        assert _read_current_coefficient(tmp_path / "nope.yaml", "drift") is None


class TestReport:
    def test_report_pass_on_low_drift(self):
        samples = _gen_fills()
        xs, ys = _compute_xy(samples)
        power = _fit(xs, ys, "power_law")
        sqrtr = _fit(xs, ys, "sqrt")
        markdown, ok = _render(
            venue="drift", market="SOL-PERP", n=len(xs),
            power=power, sqrtr=sqrtr,
            current=sqrtr.coefficient_a,  # zero drift
            recommended=sqrtr.coefficient_a,
            drift_pct=0.0, drift_threshold=15.0,
            period_first_ts=1_700_000_000,
            period_last_ts=1_700_000_000 + 3600,
        )
        assert ok is True
        assert "✓" in markdown
        assert "power_law" in markdown or "sqrt" in markdown

    def test_report_fails_on_high_drift(self):
        samples = _gen_fills()
        xs, ys = _compute_xy(samples)
        power = _fit(xs, ys, "power_law")
        sqrtr = _fit(xs, ys, "sqrt")
        markdown, ok = _render(
            venue="drift", market="SOL-PERP", n=len(xs),
            power=power, sqrtr=sqrtr,
            current=0.1, recommended=1.0,  # 900% drift
            drift_pct=900.0, drift_threshold=15.0,
            period_first_ts=1_700_000_000,
            period_last_ts=1_700_000_000 + 3600,
        )
        assert ok is False
        assert "⚠" in markdown
        assert "Drift exceeds threshold" in markdown
