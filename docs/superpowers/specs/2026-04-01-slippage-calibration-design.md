# Slippage Calibration — Design Spec

> Sub-project 4.4 of Phase 4 (ROADMAP.md §4.4)
> Date: 2026-04-01

## Overview

Build infrastructure to calibrate market impact coefficients from live execution data. Fits a volatility-and-volume-normalized power-law model, detects model drift, and updates VenueConfig. The tooling is built now; actual calibration happens when live fill data is available from Phase 1.

### Scope

**In scope:**
- `CalibrationEngine` — fits impact models from `live_fills` data
- `CalibrationReport` — fitted coefficients, confidence intervals, quality metrics
- `DriftReport` — detects when live fills diverge from the current model
- CLI: `flint calibrate --venue drift --market SOL-PERP`
- API: `POST /api/v1/calibrate`
- Config additions for calibration parameters
- Writes updated impact_coefficient to `flint.yaml` by default (`--dry-run` to skip)

**Out of scope:**
- Actual calibration (no live fill data yet)
- vAMM fill model (Sub-project 4.1)
- Transaction cost model (Sub-project 4.3)
- Auto-scheduled recalibration (manual CLI for now)

---

## 1. Impact Model

### Model Form

Primary model (power-law with normalization):
```
impact_bps = a * sigma * (Q / ADV)^b
```

Where:
- `sigma` = rolling realized volatility (24h trailing, computed from candle close-to-close returns)
- `Q` = fill size in base units (e.g., SOL contracts)
- `ADV` = average daily volume for the market (trailing 7d from candle data)
- `a` = calibration coefficient
- `b` = power-law exponent

Fallback model (square-root, fix b=0.5):
```
impact_bps = a * sigma * sqrt(Q / ADV)
```

Used when fewer than 300 fills are available (insufficient data to estimate `b` reliably).

### Model Selection

The engine fits both models and selects via 5-fold cross-validation:
1. Fit power-law (2 params: a, b) and square-root (1 param: a)
2. Compare CV R-squared
3. If power-law CV R-squared > sqrt CV R-squared + 0.05: use power-law
4. Otherwise: use square-root (simpler model, Occam's razor)
5. If 95% CI on `b` contains 0.5: prefer square-root regardless

### Fitting Method

1. **Compute realized impact** per fill: `impact_bps = |fill_price - mid_price| / mid_price * 10000`
   - `mid_price` = candle close at fill timestamp (best available proxy)
2. **Compute normalization factors** from candle data:
   - `ADV` = mean daily volume over trailing 7 days
   - `sigma` = std of close-to-close log returns over trailing 24h
3. **Normalize**: `x = Q / ADV`, `y = impact_bps / sigma`
4. **Winsorize** at 2.5th/97.5th percentiles to handle outlier fills
5. **Fit in log-log space**: `log(y) = log(a) + b * log(x)`
   - Primary: Huber robust regression (downweights outliers)
   - Reference: OLS (for comparison)
6. **Confidence intervals**: bootstrapped 95% CI on `a` and `b`
7. **Cross-validate**: 5-fold CV, compute out-of-sample R-squared

---

## 2. CalibrationEngine

**New file:** `flint/backtest/calibration.py`

### Interface

```python
class CalibrationEngine:
    def __init__(self, store: FlintStore): ...

    def calibrate(
        self,
        venue: str,
        market: str,
        lookback_days: int = 30,
        min_fills: int = 100,
    ) -> CalibrationReport:
        """Fit impact model from historical fills.

        Reads fills from live_fills table, computes ADV and volatility
        from candles table, normalizes, and fits power-law/sqrt model.

        Raises ValueError if fewer than min_fills available.
        """

    def detect_drift(
        self,
        venue: str,
        market: str,
        window_days: int = 7,
    ) -> DriftReport:
        """Compare recent fills against current impact model.

        Returns DriftReport with divergence metrics and
        recalibration recommendation.
        """
```

### CalibrationReport

```python
@dataclass
class CalibrationReport:
    venue: str
    market: str
    num_fills: int
    period_start: int                  # Unix timestamp
    period_end: int
    # Fitted model
    model_type: str                    # "power_law" or "sqrt"
    coefficient_a: float
    exponent_b: float                  # 0.5 for sqrt model
    a_ci: Tuple[float, float]          # 95% CI
    b_ci: Tuple[float, float]          # 95% CI
    # Quality metrics
    r_squared: float                   # In-sample R-squared (log-log space)
    mae_bps: float                     # Mean absolute error in original bps
    mape_pct: float                    # Mean absolute percentage error
    cv_r_squared: float                # 5-fold cross-validated R-squared
    # Current vs fitted
    current_impact_coeff: float        # Current VenueConfig value
    recommended_impact_coeff: float    # New value to write

    def summary(self) -> str: ...      # Human-readable report
    def to_dict(self) -> dict: ...     # JSON-serializable
```

### DriftReport

```python
@dataclass
class DriftReport:
    venue: str
    market: str
    num_fills: int
    mean_predicted_bps: float
    mean_actual_bps: float
    divergence_pct: float              # |predicted - actual| / max(actual, 1)
    needs_recalibration: bool          # divergence > 15%

    def summary(self) -> str: ...
```

Threshold: `divergence_pct > 15%` triggers `needs_recalibration = True`. Configurable via `calibration_drift_threshold_pct` in config.

---

## 3. CLI Integration

**Modify:** `flint/cli.py`

```
flint calibrate --venue drift --market SOL-PERP --lookback 30
```

**Default behavior:** Fits model, writes updated `impact_coefficient` to `flint.yaml`, prints report summary.

**Flags:**
- `--dry-run` — prints report only, does not write to config
- `--venue` (required) — which venue to calibrate
- `--market` (required) — which market
- `--lookback` (default 30) — days of fill data to use

**Output example:**
```
Slippage Calibration: drift / SOL-PERP
──────────────────────────────────────────
Period:     2026-03-01 to 2026-03-31 (347 fills)
Model:      power_law
Coefficient: a = 142.3 (CI: [98.1, 206.4])
Exponent:    b = 0.53  (CI: [0.39, 0.67])

Fit Quality:
  R² (log-log):  0.41
  MAE:           2.3 bps
  MAPE:          24.7%
  CV R²:         0.35

Current impact_coefficient: 0.010
Recommended:                0.008
──────────────────────────────────────────
Updated flint.yaml with new impact_coefficient.
```

### Config Write

When writing to `flint.yaml`, the engine:
1. Reads current YAML
2. Updates the venue's `impact_coefficient` under the venue config section
3. Writes back with preserved formatting
4. Logs the change

---

## 4. API Integration

**Modify:** `flint/api/routes/backtest.py`

```
POST /api/v1/calibrate
Body: { "venue": "drift", "market": "SOL-PERP", "lookback_days": 30 }
Response: CalibrationReport.to_dict()
```

API is read-only — returns the report but does not write to config. The UI can display the report and suggest the user run `flint calibrate` from CLI to apply.

---

## 5. Config Additions

**Modify:** `flint/config.py`

```python
# --- Calibration ---
calibration_drift_threshold_pct: float = 15.0
calibration_min_fills: int = 100
```

---

## 6. Dependencies

No new dependencies beyond what's installed:
- `numpy` — for log transforms, statistics, cross-validation
- `scipy` — for confidence intervals (optional, can use bootstrap with numpy)

If `scipy` is not available, fall back to numpy-only bootstrap for CIs.

---

## 7. ROADMAP Update

After implementation, update ROADMAP.md §4.4 with "Implemented" checkboxes.

---

## 8. Testing Strategy

All tests mocked — no live data needed.

- **CalibrationEngine.calibrate()**: Generate synthetic fills from a known model (a=100, b=0.5), add Gaussian noise, verify fitted coefficients recover true values within CI. Test with < 100 fills raises ValueError. Test model selection: when true b=0.5, sqrt model should be selected.
- **CalibrationEngine.detect_drift()**: Generate fills matching current model (no drift) — verify `needs_recalibration=False`. Generate fills with 25% higher impact — verify `needs_recalibration=True` at 15% threshold.
- **CalibrationReport**: Test `summary()` contains key fields. Test `to_dict()` is JSON-serializable.
- **DriftReport**: Test divergence computation. Test threshold comparison.
- **CLI**: Test `flint calibrate --help` exits 0. Test `--dry-run` flag accepted.
- **Config**: Test `calibration_drift_threshold_pct` and `calibration_min_fills` defaults.
