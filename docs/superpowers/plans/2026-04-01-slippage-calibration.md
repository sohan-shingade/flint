# Slippage Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build calibration infrastructure to fit market impact coefficients from live fill data, detect model drift, and update VenueConfig — ready to use when live trading data is available.

**Architecture:** `CalibrationEngine` reads fills from FlintStore, computes ADV and volatility from candles, normalizes, and fits a power-law or square-root impact model via robust regression. Results stored in `CalibrationReport` dataclass. CLI writes to config by default, `--dry-run` to skip.

**Tech Stack:** `numpy` (statistics, regression), existing `FlintStore` (data), `typer` (CLI), `FastAPI` (API).

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `flint/config.py` | 2 calibration config fields | Modify |
| `flint/store.py` | Add `query_live_fills_by_venue()` method | Modify |
| `flint/backtest/calibration.py` | CalibrationEngine, CalibrationReport, DriftReport | Create |
| `flint/cli.py` | `flint calibrate` command | Modify |
| `flint/api/routes/backtest.py` | `POST /api/v1/calibrate` endpoint | Modify |
| `ROADMAP.md` | Mark §4.4 as implemented | Modify |
| `tests/test_calibration_config.py` | Config field tests | Create |
| `tests/test_calibration.py` | CalibrationEngine + report tests | Create |
| `tests/test_calibration_cli.py` | CLI command tests | Create |
| `tests/test_calibration_integration.py` | End-to-end integration tests | Create |

---

### Task 1: Config Additions

**Files:**
- Modify: `flint/config.py`
- Create: `tests/test_calibration_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_calibration_config.py`:

```python
"""Tests for calibration config fields."""
from flint.config import FlintConfig


class TestCalibrationConfig:
    def test_defaults(self):
        config = FlintConfig()
        assert config.calibration_drift_threshold_pct == 15.0
        assert config.calibration_min_fills == 100

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_CALIBRATION_DRIFT_THRESHOLD_PCT", "20.0")
        monkeypatch.setenv("FLINT_CALIBRATION_MIN_FILLS", "200")
        config = FlintConfig()
        assert config.calibration_drift_threshold_pct == 20.0
        assert config.calibration_min_fills == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_calibration_config.py -v`
Expected: FAIL — `FlintConfig` has no field `calibration_drift_threshold_pct`

- [ ] **Step 3: Add config fields**

In `flint/config.py`, add after the multi-venue section (after `live_multi_venue_auto_unwind`):

```python
    # --- Slippage Calibration ---
    calibration_drift_threshold_pct: float = 15.0
    calibration_min_fills: int = 100
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_calibration_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/config.py tests/test_calibration_config.py
git commit -m "feat: add calibration config fields (drift_threshold, min_fills)"
```

---

### Task 2: Store — Query Fills by Venue

**Files:**
- Modify: `flint/store.py`
- Modify: `tests/test_store_live.py` (or create a new test)

- [ ] **Step 1: Write the failing test**

Add to an existing test file or create `tests/test_calibration_store.py`:

```python
"""Tests for store calibration query methods."""
import pytest
from flint.store import FlintStore


class TestQueryLiveFillsByVenue:
    def test_returns_fills_for_venue_and_market(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        # Insert test fills
        store.insert_live_fill(
            fill_id="f1", order_id="o1", session_id="s1",
            market="SOL-PERP", side="long", price=150.0, size=10.0,
            fee=0.075, tx_sig="tx1", venue="drift", is_partial=False, ts=1000,
        )
        store.insert_live_fill(
            fill_id="f2", order_id="o2", session_id="s1",
            market="SOL-PERP", side="short", price=151.0, size=5.0,
            fee=0.04, tx_sig="tx2", venue="drift", is_partial=False, ts=1060,
        )
        store.insert_live_fill(
            fill_id="f3", order_id="o3", session_id="s2",
            market="SOL-PERP", side="long", price=149.0, size=8.0,
            fee=0.06, tx_sig="tx3", venue="hyperliquid", is_partial=False, ts=1120,
        )

        # Query drift fills only
        fills = store.query_live_fills_by_venue("drift", "SOL-PERP")
        assert len(fills) == 2
        assert all(f["venue"] == "drift" for f in fills)

        # Query hyperliquid fills
        fills_hl = store.query_live_fills_by_venue("hyperliquid", "SOL-PERP")
        assert len(fills_hl) == 1

        # Query with time range
        fills_range = store.query_live_fills_by_venue("drift", "SOL-PERP", start_ts=1050)
        assert len(fills_range) == 1
        assert fills_range[0]["ts"] == 1060

        store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_calibration_store.py -v`
Expected: FAIL — `FlintStore has no attribute 'query_live_fills_by_venue'`

- [ ] **Step 3: Add query method to store**

In `flint/store.py`, add after the `get_live_fills` method:

```python
    def query_live_fills_by_venue(
        self,
        venue: str,
        market: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> list:
        """Query fills from live_fills by venue and market.

        Returns list of dicts with keys: fill_id, order_id, session_id,
        market, side, price, size, fee, tx_sig, venue, is_partial, ts.
        """
        sql = (
            "SELECT fill_id, order_id, session_id, market, side, price, size, "
            "fee, tx_sig, venue, is_partial, ts FROM live_fills "
            "WHERE venue = ? AND market = ?"
        )
        params: list = [venue, market]
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        sql += " ORDER BY ts ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {"fill_id": r[0], "order_id": r[1], "session_id": r[2], "market": r[3],
             "side": r[4], "price": r[5], "size": r[6], "fee": r[7], "tx_sig": r[8],
             "venue": r[9], "is_partial": r[10], "ts": r[11]}
            for r in rows
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_calibration_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/store.py tests/test_calibration_store.py
git commit -m "feat: add query_live_fills_by_venue to FlintStore"
```

---

### Task 3: CalibrationReport + DriftReport Dataclasses

**Files:**
- Create: `flint/backtest/calibration.py` (partial — dataclasses only)
- Create: `tests/test_calibration.py` (partial — report tests only)

- [ ] **Step 1: Write the failing test**

Create `tests/test_calibration.py`:

```python
"""Tests for CalibrationEngine, CalibrationReport, DriftReport."""
import json
import pytest

from flint.backtest.calibration import CalibrationReport, DriftReport


class TestCalibrationReport:
    def test_create(self):
        report = CalibrationReport(
            venue="drift", market="SOL-PERP", num_fills=347,
            period_start=1700000000, period_end=1702600000,
            model_type="power_law", coefficient_a=142.3, exponent_b=0.53,
            a_ci=(98.1, 206.4), b_ci=(0.39, 0.67),
            r_squared=0.41, mae_bps=2.3, mape_pct=24.7, cv_r_squared=0.35,
            current_impact_coeff=0.01, recommended_impact_coeff=0.008,
        )
        assert report.model_type == "power_law"
        assert report.exponent_b == 0.53

    def test_to_dict(self):
        report = CalibrationReport(
            venue="drift", market="SOL-PERP", num_fills=100,
            period_start=1700000000, period_end=1702600000,
            model_type="sqrt", coefficient_a=100.0, exponent_b=0.5,
            a_ci=(80.0, 120.0), b_ci=(0.5, 0.5),
            r_squared=0.3, mae_bps=3.0, mape_pct=30.0, cv_r_squared=0.25,
            current_impact_coeff=0.01, recommended_impact_coeff=0.009,
        )
        d = report.to_dict()
        assert d["venue"] == "drift"
        assert d["model_type"] == "sqrt"
        # Verify JSON-serializable
        json.dumps(d)

    def test_summary_contains_key_fields(self):
        report = CalibrationReport(
            venue="drift", market="SOL-PERP", num_fills=347,
            period_start=1700000000, period_end=1702600000,
            model_type="power_law", coefficient_a=142.3, exponent_b=0.53,
            a_ci=(98.1, 206.4), b_ci=(0.39, 0.67),
            r_squared=0.41, mae_bps=2.3, mape_pct=24.7, cv_r_squared=0.35,
            current_impact_coeff=0.01, recommended_impact_coeff=0.008,
        )
        s = report.summary()
        assert "drift" in s
        assert "SOL-PERP" in s
        assert "power_law" in s
        assert "142.3" in s or "142.30" in s


class TestDriftReport:
    def test_needs_recalibration_above_threshold(self):
        report = DriftReport(
            venue="drift", market="SOL-PERP", num_fills=50,
            mean_predicted_bps=5.0, mean_actual_bps=6.0,
            divergence_pct=20.0, needs_recalibration=True,
        )
        assert report.needs_recalibration is True

    def test_no_recalibration_below_threshold(self):
        report = DriftReport(
            venue="drift", market="SOL-PERP", num_fills=50,
            mean_predicted_bps=5.0, mean_actual_bps=5.5,
            divergence_pct=10.0, needs_recalibration=False,
        )
        assert report.needs_recalibration is False

    def test_summary(self):
        report = DriftReport(
            venue="drift", market="SOL-PERP", num_fills=50,
            mean_predicted_bps=5.0, mean_actual_bps=6.0,
            divergence_pct=20.0, needs_recalibration=True,
        )
        s = report.summary()
        assert "drift" in s
        assert "20.0" in s or "20.00" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_calibration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flint.backtest.calibration'`

- [ ] **Step 3: Create calibration.py with dataclasses**

Create `flint/backtest/calibration.py`:

```python
"""CalibrationEngine — fits market impact models from live fill data.

Reads fills from FlintStore, computes ADV and volatility from candle data,
normalizes, and fits power-law or square-root impact models via robust regression.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("flint.calibration")


@dataclass
class CalibrationReport:
    """Result of impact model calibration."""
    venue: str
    market: str
    num_fills: int
    period_start: int
    period_end: int
    # Fitted model
    model_type: str                    # "power_law" or "sqrt"
    coefficient_a: float
    exponent_b: float                  # 0.5 for sqrt model
    a_ci: Tuple[float, float]          # 95% CI
    b_ci: Tuple[float, float]          # 95% CI
    # Quality metrics
    r_squared: float
    mae_bps: float
    mape_pct: float
    cv_r_squared: float
    # Current vs fitted
    current_impact_coeff: float
    recommended_impact_coeff: float

    def to_dict(self) -> dict:
        return {
            "venue": self.venue,
            "market": self.market,
            "num_fills": self.num_fills,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "model_type": self.model_type,
            "coefficient_a": self.coefficient_a,
            "exponent_b": self.exponent_b,
            "a_ci": list(self.a_ci),
            "b_ci": list(self.b_ci),
            "r_squared": self.r_squared,
            "mae_bps": self.mae_bps,
            "mape_pct": self.mape_pct,
            "cv_r_squared": self.cv_r_squared,
            "current_impact_coeff": self.current_impact_coeff,
            "recommended_impact_coeff": self.recommended_impact_coeff,
        }

    def summary(self) -> str:
        return (
            f"Slippage Calibration: {self.venue} / {self.market}\n"
            f"{'=' * 50}\n"
            f"Period:      {self.num_fills} fills\n"
            f"Model:       {self.model_type}\n"
            f"Coefficient: a = {self.coefficient_a:.2f} (CI: [{self.a_ci[0]:.1f}, {self.a_ci[1]:.1f}])\n"
            f"Exponent:    b = {self.exponent_b:.2f} (CI: [{self.b_ci[0]:.2f}, {self.b_ci[1]:.2f}])\n"
            f"\n"
            f"Fit Quality:\n"
            f"  R² (log-log):  {self.r_squared:.2f}\n"
            f"  MAE:           {self.mae_bps:.1f} bps\n"
            f"  MAPE:          {self.mape_pct:.1f}%\n"
            f"  CV R²:         {self.cv_r_squared:.2f}\n"
            f"\n"
            f"Current impact_coefficient: {self.current_impact_coeff:.4f}\n"
            f"Recommended:                {self.recommended_impact_coeff:.4f}\n"
            f"{'=' * 50}"
        )


@dataclass
class DriftReport:
    """Result of model drift detection."""
    venue: str
    market: str
    num_fills: int
    mean_predicted_bps: float
    mean_actual_bps: float
    divergence_pct: float
    needs_recalibration: bool

    def summary(self) -> str:
        status = "RECALIBRATE" if self.needs_recalibration else "OK"
        return (
            f"Drift Detection: {self.venue} / {self.market}\n"
            f"{'=' * 50}\n"
            f"Fills:     {self.num_fills}\n"
            f"Predicted: {self.mean_predicted_bps:.1f} bps\n"
            f"Actual:    {self.mean_actual_bps:.1f} bps\n"
            f"Divergence: {self.divergence_pct:.1f}%\n"
            f"Status:    {status}\n"
            f"{'=' * 50}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_calibration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/backtest/calibration.py tests/test_calibration.py
git commit -m "feat: add CalibrationReport and DriftReport dataclasses"
```

---

### Task 4: CalibrationEngine — calibrate() Method

**Files:**
- Modify: `flint/backtest/calibration.py`
- Modify: `tests/test_calibration.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_calibration.py`:

```python
from flint.backtest.calibration import CalibrationEngine
from flint.store import FlintStore
from flint.models import Candle


def _populate_store_with_synthetic_data(store, num_fills=200, true_a=100.0, true_b=0.5):
    """Insert synthetic fills and candles with known impact model."""
    import random
    random.seed(42)

    base_ts = 1700000000
    resolution_s = 3600

    # Insert candles (24h * 30 days = 720 candles)
    candles = []
    for i in range(720):
        ts = base_ts + i * resolution_s
        price = 150.0 + random.uniform(-5, 5)
        vol = 10000.0 + random.uniform(-2000, 2000)
        candles.append(Candle(
            ts=ts, open=price, high=price + 1, low=price - 1,
            close=price + 0.5, volume=vol,
            market="SOL-PERP", resolution_s=resolution_s,
        ))
    store.upsert_candles(candles)

    # Insert fills with known impact model: impact = a * sigma * (Q/ADV)^b
    # Use simplified version for testing
    adv = 10000.0 * 24  # approx daily volume
    sigma = 0.02  # 2% daily vol

    for i in range(num_fills):
        ts = base_ts + i * 3600 + 1800  # middle of each hour
        size = random.uniform(1.0, 50.0)
        participation = size / adv
        true_impact_pct = true_a * sigma * (participation ** true_b) / 10000
        noise = random.gauss(0, true_impact_pct * 0.3)  # 30% noise
        actual_impact_pct = max(0.00001, true_impact_pct + noise)
        mid_price = 150.0
        fill_price = mid_price * (1 + actual_impact_pct)

        store.insert_live_fill(
            fill_id=f"f{i}", order_id=f"o{i}", session_id="s1",
            market="SOL-PERP", side="long", price=fill_price, size=size,
            fee=fill_price * size * 0.0005, tx_sig=f"tx{i}",
            venue="drift", is_partial=False, ts=ts,
        )


class TestCalibrationEngine:
    def test_calibrate_recovers_known_model(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        _populate_store_with_synthetic_data(store, num_fills=200, true_a=100.0, true_b=0.5)

        engine = CalibrationEngine(store)
        report = engine.calibrate("drift", "SOL-PERP", lookback_days=30)

        assert report.venue == "drift"
        assert report.market == "SOL-PERP"
        assert report.num_fills == 200
        assert report.model_type in ("power_law", "sqrt")
        assert report.coefficient_a > 0
        assert 0.1 < report.exponent_b < 1.0  # Should be near 0.5
        assert report.r_squared > 0  # Should have some fit
        assert report.mae_bps >= 0
        assert report.recommended_impact_coeff > 0
        store.close()

    def test_calibrate_too_few_fills_raises(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        _populate_store_with_synthetic_data(store, num_fills=10)

        engine = CalibrationEngine(store)
        with pytest.raises(ValueError, match="fills"):
            engine.calibrate("drift", "SOL-PERP", lookback_days=30, min_fills=100)
        store.close()

    def test_calibrate_sqrt_model_when_few_fills(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        _populate_store_with_synthetic_data(store, num_fills=150)

        engine = CalibrationEngine(store)
        report = engine.calibrate("drift", "SOL-PERP", lookback_days=30, min_fills=100)
        # With < 300 fills, should prefer sqrt model
        assert report.model_type == "sqrt"
        assert report.exponent_b == 0.5
        store.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calibration.py::TestCalibrationEngine -v`
Expected: FAIL — `ImportError: cannot import name 'CalibrationEngine'`

- [ ] **Step 3: Implement CalibrationEngine.calibrate()**

Add to `flint/backtest/calibration.py` after the `DriftReport` class:

```python
class CalibrationEngine:
    """Fits market impact models from live fill data."""

    def __init__(self, store):
        self._store = store

    def calibrate(
        self,
        venue: str,
        market: str,
        lookback_days: int = 30,
        min_fills: int = 100,
    ) -> CalibrationReport:
        """Fit impact model from historical fills.

        Model: impact_bps = a * sigma * (Q / ADV)^b
        Fits power-law (2 params) if >= 300 fills, else sqrt (fix b=0.5).
        """
        import time as _time
        from ..execution.venue_config import get_venue_config

        end_ts = int(_time.time())
        start_ts = end_ts - lookback_days * 86400

        # Read fills
        fills = self._store.query_live_fills_by_venue(venue, market, start_ts=start_ts, end_ts=end_ts)
        if len(fills) < min_fills:
            raise ValueError(
                f"Insufficient fills for calibration: {len(fills)} < {min_fills}. "
                f"Need at least {min_fills} fills for reliable calibration."
            )

        # Read candles for ADV and volatility
        candles = self._store.query_candles(market, 3600, start_ts=start_ts, end_ts=end_ts)
        if not candles:
            raise ValueError(f"No candle data available for {market}")

        # Compute ADV (average daily volume, trailing 7d approximation)
        volumes = [c.volume for c in candles]
        daily_volumes = []
        for i in range(0, len(volumes), 24):
            chunk = volumes[i:i+24]
            if chunk:
                daily_volumes.append(sum(chunk))
        adv = np.mean(daily_volumes) if daily_volumes else np.mean(volumes) * 24

        # Compute rolling volatility (close-to-close log returns)
        closes = np.array([c.close for c in candles])
        if len(closes) > 1:
            log_returns = np.diff(np.log(closes))
            sigma = float(np.std(log_returns)) * np.sqrt(24)  # Annualize to daily
        else:
            sigma = 0.02  # Default 2% daily vol

        if sigma < 1e-8:
            sigma = 0.02

        # Compute realized impact per fill
        # impact = |fill_price - candle_close| / candle_close * 10000
        candle_by_ts = {c.ts: c.close for c in candles}
        x_data = []  # participation rates
        y_data = []  # vol-adjusted impact

        for fill in fills:
            # Find closest candle
            closest_ts = min(candle_by_ts.keys(), key=lambda t: abs(t - fill["ts"]), default=None)
            if closest_ts is None:
                continue
            mid_price = candle_by_ts[closest_ts]
            if mid_price <= 0:
                continue

            impact_bps = abs(fill["price"] - mid_price) / mid_price * 10000
            if impact_bps < 0.01:
                continue  # Skip near-zero impact (noise)

            participation = fill["size"] / adv if adv > 0 else 0
            if participation <= 0:
                continue

            vol_adjusted = impact_bps / sigma if sigma > 0 else impact_bps

            x_data.append(participation)
            y_data.append(vol_adjusted)

        if len(x_data) < min_fills:
            raise ValueError(f"Only {len(x_data)} valid fills after filtering (need {min_fills})")

        x = np.array(x_data)
        y = np.array(y_data)

        # Winsorize at 2.5th/97.5th percentiles
        x, y = self._winsorize(x, y)

        # Decide model type
        use_power_law = len(x) >= 300

        if use_power_law:
            a, b, a_ci, b_ci, r2 = self._fit_power_law(x, y)
            # Also fit sqrt for comparison via CV
            a_sqrt, _, a_ci_sqrt, _, r2_sqrt = self._fit_sqrt(x, y)
            cv_r2_pl = self._cross_validate(x, y, fix_b=None)
            cv_r2_sqrt = self._cross_validate(x, y, fix_b=0.5)

            # Model selection: prefer sqrt unless power-law is significantly better
            if cv_r2_pl > cv_r2_sqrt + 0.05 and not (b_ci[0] <= 0.5 <= b_ci[1]):
                model_type = "power_law"
                cv_r2 = cv_r2_pl
            else:
                model_type = "sqrt"
                a, b = a_sqrt, 0.5
                a_ci, b_ci = a_ci_sqrt, (0.5, 0.5)
                r2 = r2_sqrt
                cv_r2 = cv_r2_sqrt
        else:
            a, b, a_ci, b_ci, r2 = self._fit_sqrt(x, y)
            model_type = "sqrt"
            cv_r2 = self._cross_validate(x, y, fix_b=0.5)

        # Compute MAE and MAPE in original bps space
        if model_type == "power_law":
            y_pred = a * (x ** b)
        else:
            y_pred = a * np.sqrt(x)

        y_original = y * sigma  # un-normalize
        y_pred_bps = y_pred * sigma

        mae = float(np.mean(np.abs(y_original - y_pred_bps)))
        mape = float(np.mean(np.abs((y_original - y_pred_bps) / np.maximum(y_original, 0.01)))) * 100

        # Compute recommended impact_coefficient
        # The existing model uses: impact_pct = k * sqrt(participation)
        # Our model: impact_bps = a * sigma * (participation)^b → impact_pct = a * sigma * participation^b / 10000
        # Mapping: k ≈ a * sigma / 10000 (when b ≈ 0.5)
        recommended_k = a * sigma / 10000

        current_config = get_venue_config(venue)

        period_start = min(f["ts"] for f in fills)
        period_end = max(f["ts"] for f in fills)

        return CalibrationReport(
            venue=venue, market=market, num_fills=len(x),
            period_start=period_start, period_end=period_end,
            model_type=model_type, coefficient_a=float(a), exponent_b=float(b),
            a_ci=(float(a_ci[0]), float(a_ci[1])),
            b_ci=(float(b_ci[0]), float(b_ci[1])),
            r_squared=float(r2), mae_bps=float(mae), mape_pct=float(mape),
            cv_r_squared=float(cv_r2),
            current_impact_coeff=current_config.impact_coefficient,
            recommended_impact_coeff=float(recommended_k),
        )

    def _winsorize(self, x, y, lower=2.5, upper=97.5):
        """Winsorize both arrays at given percentiles."""
        y_low, y_high = np.percentile(y, [lower, upper])
        mask = (y >= y_low) & (y <= y_high)
        return x[mask], y[mask]

    def _fit_power_law(self, x, y):
        """Fit log(y) = log(a) + b * log(x) via robust regression."""
        log_x = np.log(x)
        log_y = np.log(np.maximum(y, 1e-10))

        # Huber-like robust fit: use iteratively reweighted least squares
        a, b, r2 = self._robust_ols(log_x, log_y)
        a_exp = np.exp(a)  # Convert from log space

        # Bootstrap CI
        a_ci, b_ci = self._bootstrap_ci(x, y, fix_b=None)
        return a_exp, b, a_ci, b_ci, r2

    def _fit_sqrt(self, x, y):
        """Fit y = a * sqrt(x), i.e., fix b=0.5, estimate a only."""
        sqrt_x = np.sqrt(x)
        # a = sum(y * sqrt_x) / sum(sqrt_x^2) — OLS with no intercept
        a = float(np.sum(y * sqrt_x) / np.sum(sqrt_x ** 2))

        y_pred = a * sqrt_x
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        a_ci, _ = self._bootstrap_ci(x, y, fix_b=0.5)
        return a, 0.5, a_ci, (0.5, 0.5), r2

    def _robust_ols(self, log_x, log_y):
        """OLS fit with outlier detection (simple Huber-like approach)."""
        # Initial OLS
        n = len(log_x)
        X = np.column_stack([np.ones(n), log_x])
        try:
            beta = np.linalg.lstsq(X, log_y, rcond=None)[0]
        except np.linalg.LinAlgError:
            return 0.0, 0.5, 0.0

        intercept, slope = beta[0], beta[1]

        # Iterative reweighting: downweight residuals > 1.5 * MAD
        for _ in range(3):
            residuals = log_y - (intercept + slope * log_x)
            mad = np.median(np.abs(residuals))
            if mad < 1e-10:
                break
            weights = np.where(np.abs(residuals) <= 1.5 * mad, 1.0, 1.5 * mad / np.abs(residuals))
            W = np.diag(weights)
            try:
                beta = np.linalg.lstsq(W @ X, W @ log_y, rcond=None)[0]
            except np.linalg.LinAlgError:
                break
            intercept, slope = beta[0], beta[1]

        # R-squared
        y_pred = intercept + slope * log_x
        ss_res = np.sum((log_y - y_pred) ** 2)
        ss_tot = np.sum((log_y - np.mean(log_y)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        return intercept, slope, r2

    def _bootstrap_ci(self, x, y, fix_b=None, n_boot=200):
        """Bootstrap 95% confidence intervals."""
        n = len(x)
        a_samples = []
        b_samples = []

        for _ in range(n_boot):
            idx = np.random.randint(0, n, size=n)
            x_s, y_s = x[idx], y[idx]

            if fix_b is not None:
                sqrt_x = x_s ** fix_b
                a_s = float(np.sum(y_s * sqrt_x) / np.sum(sqrt_x ** 2))
                a_samples.append(a_s)
                b_samples.append(fix_b)
            else:
                log_x = np.log(x_s)
                log_y = np.log(np.maximum(y_s, 1e-10))
                intercept, slope, _ = self._robust_ols(log_x, log_y)
                a_samples.append(np.exp(intercept))
                b_samples.append(slope)

        a_arr = np.array(a_samples)
        b_arr = np.array(b_samples)
        a_ci = (float(np.percentile(a_arr, 2.5)), float(np.percentile(a_arr, 97.5)))
        b_ci = (float(np.percentile(b_arr, 2.5)), float(np.percentile(b_arr, 97.5)))
        return a_ci, b_ci

    def _cross_validate(self, x, y, fix_b=None, k=5):
        """K-fold cross-validation, returns mean R-squared."""
        n = len(x)
        indices = np.arange(n)
        np.random.shuffle(indices)
        fold_size = n // k
        r2_scores = []

        for i in range(k):
            test_idx = indices[i * fold_size:(i + 1) * fold_size]
            train_idx = np.concatenate([indices[:i * fold_size], indices[(i + 1) * fold_size:]])

            x_train, y_train = x[train_idx], y[train_idx]
            x_test, y_test = x[test_idx], y[test_idx]

            if fix_b is not None:
                sqrt_x = x_train ** fix_b
                a = float(np.sum(y_train * sqrt_x) / np.sum(sqrt_x ** 2))
                y_pred = a * (x_test ** fix_b)
            else:
                log_x = np.log(x_train)
                log_y = np.log(np.maximum(y_train, 1e-10))
                intercept, slope, _ = self._robust_ols(log_x, log_y)
                y_pred = np.exp(intercept) * (x_test ** slope)

            ss_res = np.sum((y_test - y_pred) ** 2)
            ss_tot = np.sum((y_test - np.mean(y_test)) ** 2)
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
            r2_scores.append(max(r2, -1.0))  # Cap at -1 for degenerate folds

        return float(np.mean(r2_scores))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_calibration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/backtest/calibration.py tests/test_calibration.py
git commit -m "feat: implement CalibrationEngine.calibrate() with power-law/sqrt model fitting"
```

---

### Task 5: CalibrationEngine — detect_drift()

**Files:**
- Modify: `flint/backtest/calibration.py`
- Modify: `tests/test_calibration.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_calibration.py`:

```python
class TestDriftDetection:
    def test_no_drift_when_model_matches(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        _populate_store_with_synthetic_data(store, num_fills=200, true_a=100.0, true_b=0.5)

        engine = CalibrationEngine(store)
        report = engine.detect_drift("drift", "SOL-PERP", window_days=30)

        assert report.venue == "drift"
        assert report.num_fills > 0
        # With default k=0.01, synthetic data should be close enough
        assert isinstance(report.divergence_pct, float)
        store.close()

    def test_drift_detected_with_wrong_model(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        # Use very high true_a so fills diverge significantly from default k=0.01
        _populate_store_with_synthetic_data(store, num_fills=200, true_a=5000.0, true_b=0.5)

        engine = CalibrationEngine(store)
        report = engine.detect_drift("drift", "SOL-PERP", window_days=30, threshold_pct=15.0)

        assert report.needs_recalibration is True
        assert report.divergence_pct > 15.0
        store.close()

    def test_drift_returns_no_recalibration_when_close(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        _populate_store_with_synthetic_data(store, num_fills=200, true_a=100.0, true_b=0.5)

        engine = CalibrationEngine(store)
        # Use very high threshold so it never triggers
        report = engine.detect_drift("drift", "SOL-PERP", window_days=30, threshold_pct=99.0)

        assert report.needs_recalibration is False
        store.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calibration.py::TestDriftDetection -v`
Expected: FAIL — `CalibrationEngine.detect_drift` does not exist or has wrong signature

- [ ] **Step 3: Implement detect_drift()**

Add to `CalibrationEngine` class in `flint/backtest/calibration.py`:

```python
    def detect_drift(
        self,
        venue: str,
        market: str,
        window_days: int = 7,
        threshold_pct: float = 15.0,
    ) -> DriftReport:
        """Compare recent fills against current impact model.

        Uses the venue's current impact_coefficient to predict impact,
        compares against actual observed impact.
        """
        import time as _time
        from ..execution.venue_config import get_venue_config

        end_ts = int(_time.time())
        start_ts = end_ts - window_days * 86400

        fills = self._store.query_live_fills_by_venue(venue, market, start_ts=start_ts, end_ts=end_ts)
        candles = self._store.query_candles(market, 3600, start_ts=start_ts, end_ts=end_ts)

        if not fills or not candles:
            return DriftReport(
                venue=venue, market=market, num_fills=0,
                mean_predicted_bps=0, mean_actual_bps=0,
                divergence_pct=0, needs_recalibration=False,
            )

        config = get_venue_config(venue)
        k = config.impact_coefficient

        candle_by_ts = {c.ts: c for c in candles}
        volumes = [c.volume for c in candles]
        adv = sum(volumes) / max(len(volumes) / 24, 1) if volumes else 1.0

        predicted_bps_list = []
        actual_bps_list = []

        for fill in fills:
            closest_ts = min(candle_by_ts.keys(), key=lambda t: abs(t - fill["ts"]), default=None)
            if closest_ts is None:
                continue
            candle = candle_by_ts[closest_ts]
            if candle.close <= 0 or candle.volume <= 0:
                continue

            # Predicted impact using current sqrt model
            participation = fill["size"] / candle.volume
            predicted_pct = k * math.sqrt(participation)
            predicted_bps = predicted_pct * 10000

            # Actual impact
            actual_bps = abs(fill["price"] - candle.close) / candle.close * 10000

            if actual_bps > 0.01:  # Skip near-zero
                predicted_bps_list.append(predicted_bps)
                actual_bps_list.append(actual_bps)

        if not actual_bps_list:
            return DriftReport(
                venue=venue, market=market, num_fills=0,
                mean_predicted_bps=0, mean_actual_bps=0,
                divergence_pct=0, needs_recalibration=False,
            )

        mean_pred = float(np.mean(predicted_bps_list))
        mean_actual = float(np.mean(actual_bps_list))
        divergence = abs(mean_pred - mean_actual) / max(mean_actual, 1.0) * 100

        return DriftReport(
            venue=venue, market=market, num_fills=len(actual_bps_list),
            mean_predicted_bps=mean_pred, mean_actual_bps=mean_actual,
            divergence_pct=float(divergence),
            needs_recalibration=divergence > threshold_pct,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_calibration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/backtest/calibration.py tests/test_calibration.py
git commit -m "feat: add drift detection to CalibrationEngine (15% threshold)"
```

---

### Task 6: CLI Command

**Files:**
- Modify: `flint/cli.py`
- Create: `tests/test_calibration_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_calibration_cli.py`:

```python
"""Tests for flint calibrate CLI command."""
from typer.testing import CliRunner
from flint.cli import app


class TestCalibrateCLI:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(app, ["calibrate", "--help"])
        assert result.exit_code == 0
        assert "venue" in result.output.lower()
        assert "market" in result.output.lower()

    def test_dry_run_flag_accepted(self):
        runner = CliRunner()
        result = runner.invoke(app, ["calibrate", "--help"])
        assert "dry-run" in result.output.lower() or "dry_run" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_calibration_cli.py -v`
Expected: FAIL — no `calibrate` command

- [ ] **Step 3: Add calibrate command to cli.py**

In `flint/cli.py`, add the calibrate command (after the parity command):

```python
@app.command()
def calibrate(
    venue: str = typer.Argument(..., help="Venue to calibrate (e.g. drift, hyperliquid)"),
    market: str = typer.Option("SOL-PERP", help="Market to calibrate"),
    lookback: int = typer.Option(30, help="Days of fill data to use"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print report without writing to config"),
):
    """Calibrate slippage impact model from live fill data."""
    from flint.config import load_config
    from flint.store import FlintStore
    from flint.backtest.calibration import CalibrationEngine

    config = load_config()
    store = FlintStore(config.db_path)

    try:
        engine = CalibrationEngine(store)
        report = engine.calibrate(
            venue=venue, market=market,
            lookback_days=lookback,
            min_fills=config.calibration_min_fills,
        )
        console.print(report.summary())

        if not dry_run:
            _write_impact_to_yaml(venue, report.recommended_impact_coeff)
            console.print(f"\n[green]Updated flint.yaml with impact_coefficient={report.recommended_impact_coeff:.6f}[/green]")
        else:
            console.print("\n[yellow]Dry run — config not updated.[/yellow]")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    finally:
        store.close()


def _write_impact_to_yaml(venue: str, impact_coeff: float) -> None:
    """Update impact_coefficient for a venue in flint.yaml."""
    import os
    import yaml

    yaml_path = os.path.join(os.getcwd(), "flint.yaml")
    if not os.path.exists(yaml_path):
        # Create minimal yaml if it doesn't exist
        data = {}
    else:
        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f) or {}

    venues = data.setdefault("venues", {})
    venue_data = venues.setdefault(venue, {})
    venue_data["impact_coefficient"] = round(impact_coeff, 6)

    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_calibration_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/cli.py tests/test_calibration_cli.py
git commit -m "feat: add flint calibrate CLI command with --dry-run option"
```

---

### Task 7: API Endpoint

**Files:**
- Modify: `flint/api/routes/backtest.py`

- [ ] **Step 1: Add the calibrate endpoint**

In `flint/api/routes/backtest.py`, add after the parity endpoint:

```python
@router.post("/calibrate")
def run_calibration(req: dict, request: Request):
    """Run slippage calibration (read-only, does not write to config)."""
    from ...backtest.calibration import CalibrationEngine

    store = getattr(request.app.state, "store", None)
    if store is None:
        from fastapi import HTTPException
        raise HTTPException(500, "Store not available")

    venue = req.get("venue", "drift")
    market = req.get("market", "SOL-PERP")
    lookback_days = req.get("lookback_days", 30)

    try:
        engine = CalibrationEngine(store)
        report = engine.calibrate(venue=venue, market=market, lookback_days=lookback_days)
        return report.to_dict()
    except ValueError as e:
        return {"error": str(e)}
```

- [ ] **Step 2: Run existing tests for regressions**

Run: `pytest tests/ -k "backtest or calibrat" -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add flint/api/routes/backtest.py
git commit -m "feat: add POST /api/v1/calibrate endpoint (read-only)"
```

---

### Task 8: ROADMAP Update

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Update ROADMAP.md §4.4**

Find the §4.4 Slippage Calibration section and add after the existing checklist items:

```markdown
**Implemented:**
- [x] `CalibrationEngine` with power-law and square-root model fitting (`flint/backtest/calibration.py`)
- [x] Volatility + ADV normalization for regime-robust calibration
- [x] Huber-like robust regression with iterative reweighting
- [x] Bootstrap 95% confidence intervals on coefficients
- [x] 5-fold cross-validation for model selection (power-law vs sqrt)
- [x] Drift detection with configurable threshold (default 15%)
- [x] CLI: `flint calibrate --venue <venue> --market <market>` (writes to config by default, `--dry-run` to skip)
- [x] API: `POST /api/v1/calibrate` (read-only)
- [x] `CalibrationReport` and `DriftReport` dataclasses with summary() and to_dict()
- [x] `query_live_fills_by_venue()` store method for fill retrieval
```

- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: update ROADMAP §4.4 with slippage calibration implementation notes"
```

---

## Task Dependencies

```
Task 1 (Config) ──────────────────┐
Task 2 (Store Query) ─────────────┤
                                   ├──→ Task 4 (calibrate()) ──→ Task 5 (detect_drift())
Task 3 (Report Dataclasses) ──────┘                                      │
                                                                         ├──→ Task 6 (CLI)
                                                                         ├──→ Task 7 (API)
                                                                         └──→ Task 8 (ROADMAP)
```

**Parallelizable:** Tasks 1, 2, 3 have no dependencies between them.
**Sequential:** Task 4 needs 1+2+3. Task 5 needs 4. Tasks 6, 7, 8 need 5.
