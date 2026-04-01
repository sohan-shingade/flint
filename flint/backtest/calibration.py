"""CalibrationEngine — fits market impact models from live fill data."""
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
    model_type: str
    coefficient_a: float
    exponent_b: float
    a_ci: Tuple[float, float]
    b_ci: Tuple[float, float]
    r_squared: float
    mae_bps: float
    mape_pct: float
    cv_r_squared: float
    current_impact_coeff: float
    recommended_impact_coeff: float

    def to_dict(self) -> dict:
        return {
            "venue": self.venue, "market": self.market,
            "num_fills": self.num_fills, "period_start": self.period_start,
            "period_end": self.period_end, "model_type": self.model_type,
            "coefficient_a": self.coefficient_a, "exponent_b": self.exponent_b,
            "a_ci": list(self.a_ci), "b_ci": list(self.b_ci),
            "r_squared": self.r_squared, "mae_bps": self.mae_bps,
            "mape_pct": self.mape_pct, "cv_r_squared": self.cv_r_squared,
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
            f"\nFit Quality:\n"
            f"  R² (log-log):  {self.r_squared:.2f}\n"
            f"  MAE:           {self.mae_bps:.1f} bps\n"
            f"  MAPE:          {self.mape_pct:.1f}%\n"
            f"  CV R²:         {self.cv_r_squared:.2f}\n"
            f"\nCurrent impact_coefficient: {self.current_impact_coeff:.4f}\n"
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
        Uses Huber-like robust regression with bootstrap CIs.
        """
        import time as _time
        from ..execution.venue_config import get_venue_config

        end_ts = int(_time.time())
        start_ts = end_ts - lookback_days * 86400

        fills = self._store.query_live_fills_by_venue(venue, market, start_ts=start_ts, end_ts=end_ts)
        if len(fills) < min_fills:
            raise ValueError(
                f"Insufficient fills for calibration: {len(fills)} < {min_fills}. "
                f"Need at least {min_fills} fills."
            )

        candles = self._store.query_candles(market, 3600, start_ts=start_ts, end_ts=end_ts)
        if not candles:
            raise ValueError(f"No candle data available for {market}")

        # Compute ADV
        volumes = [c.volume for c in candles]
        daily_volumes = []
        for i in range(0, len(volumes), 24):
            chunk = volumes[i:i + 24]
            if chunk:
                daily_volumes.append(sum(chunk))
        adv = np.mean(daily_volumes) if daily_volumes else np.mean(volumes) * 24

        # Compute rolling volatility
        closes = np.array([c.close for c in candles])
        if len(closes) > 1:
            log_returns = np.diff(np.log(closes))
            sigma = float(np.std(log_returns)) * np.sqrt(24)
        else:
            sigma = 0.02
        if sigma < 1e-8:
            sigma = 0.02

        # Compute realized impact per fill
        candle_by_ts = {c.ts: c.close for c in candles}
        x_data = []
        y_data = []

        for fill in fills:
            closest_ts = min(candle_by_ts.keys(), key=lambda t: abs(t - fill["ts"]), default=None)
            if closest_ts is None:
                continue
            mid_price = candle_by_ts[closest_ts]
            if mid_price <= 0:
                continue
            impact_bps = abs(fill["price"] - mid_price) / mid_price * 10000
            if impact_bps < 0.01:
                continue
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

        # Winsorize
        x, y = self._winsorize(x, y)

        # Model selection
        use_power_law = len(x) >= 300

        if use_power_law:
            a, b, a_ci, b_ci, r2 = self._fit_power_law(x, y)
            a_sqrt, _, a_ci_sqrt, _, r2_sqrt = self._fit_sqrt(x, y)
            cv_r2_pl = self._cross_validate(x, y, fix_b=None)
            cv_r2_sqrt = self._cross_validate(x, y, fix_b=0.5)

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

        # Compute MAE/MAPE
        y_pred = a * (x ** b)
        y_original = y * sigma
        y_pred_bps = y_pred * sigma
        mae = float(np.mean(np.abs(y_original - y_pred_bps)))
        mape = float(np.mean(np.abs((y_original - y_pred_bps) / np.maximum(y_original, 0.01)))) * 100

        # Map to impact_coefficient: k ≈ a * sigma / 10000
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
        y_low, y_high = np.percentile(y, [lower, upper])
        mask = (y >= y_low) & (y <= y_high)
        return x[mask], y[mask]

    def _fit_power_law(self, x, y):
        log_x = np.log(x)
        log_y = np.log(np.maximum(y, 1e-10))
        intercept, slope, r2 = self._robust_ols(log_x, log_y)
        a_exp = np.exp(intercept)
        a_ci, b_ci = self._bootstrap_ci(x, y, fix_b=None)
        return a_exp, slope, a_ci, b_ci, r2

    def _fit_sqrt(self, x, y):
        sqrt_x = np.sqrt(x)
        a = float(np.sum(y * sqrt_x) / np.sum(sqrt_x ** 2))
        y_pred = a * sqrt_x
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        a_ci, _ = self._bootstrap_ci(x, y, fix_b=0.5)
        return a, 0.5, a_ci, (0.5, 0.5), r2

    def _robust_ols(self, log_x, log_y):
        n = len(log_x)
        X = np.column_stack([np.ones(n), log_x])
        try:
            beta = np.linalg.lstsq(X, log_y, rcond=None)[0]
        except np.linalg.LinAlgError:
            return 0.0, 0.5, 0.0
        intercept, slope = beta[0], beta[1]

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

        y_pred = intercept + slope * log_x
        ss_res = np.sum((log_y - y_pred) ** 2)
        ss_tot = np.sum((log_y - np.mean(log_y)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        return intercept, slope, r2

    def _bootstrap_ci(self, x, y, fix_b=None, n_boot=200):
        n = len(x)
        a_samples, b_samples = [], []
        for _ in range(n_boot):
            idx = np.random.randint(0, n, size=n)
            x_s, y_s = x[idx], y[idx]
            if fix_b is not None:
                sqrt_x = x_s ** fix_b
                denom = np.sum(sqrt_x ** 2)
                a_s = float(np.sum(y_s * sqrt_x) / denom) if denom > 0 else 0
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
                denom = np.sum(sqrt_x ** 2)
                a = float(np.sum(y_train * sqrt_x) / denom) if denom > 0 else 0
                y_pred = a * (x_test ** fix_b)
            else:
                log_x = np.log(x_train)
                log_y = np.log(np.maximum(y_train, 1e-10))
                intercept, slope, _ = self._robust_ols(log_x, log_y)
                y_pred = np.exp(intercept) * (x_test ** slope)
            ss_res = np.sum((y_test - y_pred) ** 2)
            ss_tot = np.sum((y_test - np.mean(y_test)) ** 2)
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
            r2_scores.append(max(r2, -1.0))
        return float(np.mean(r2_scores))
