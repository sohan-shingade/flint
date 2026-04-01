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
