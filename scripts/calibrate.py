#!/usr/bin/env python
"""Slippage calibration pipeline — Phase 3 T3.6.

Fits impact-curve models (power-law + sqrt) to user-provided fill CSV,
emits a markdown report, and optionally writes calibrated coefficients
back to flint.yaml. Drift warning fires when the new coefficient differs
from the stored one by more than 15%.

Usage:
    python scripts/calibrate.py \\
        --fills ./data/custom/drift_fills.csv \\
        --market SOL-PERP \\
        --venue drift \\
        --out artifacts/calibration/drift_SOL-PERP_2026-04.md

    # Write new coefficients back to flint.yaml (requires --force on >15% drift):
    python scripts/calibrate.py ... --write-yaml flint.yaml

Exits non-zero on drift > threshold unless --force passed. CI-gate-ready.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from flint.backtest.calibration import CalibrationEngine, CalibrationReport


# ---------------------------------------------------------------------------
# Fill ingest (shares schema with Phase 1.6 CustomCSVProvider)
# ---------------------------------------------------------------------------

class _FillSample:
    __slots__ = ("ts", "market", "venue", "side", "size", "price",
                 "mid_price", "realized_slippage_bps")

    def __init__(self, ts, market, venue, side, size, price,
                 mid_price=None, realized_slippage_bps=None):
        self.ts = int(ts)
        self.market = market
        self.venue = venue
        self.side = side
        self.size = float(size)
        self.price = float(price)
        self.mid_price = float(mid_price) if mid_price else None
        self.realized_slippage_bps = (
            float(realized_slippage_bps) if realized_slippage_bps is not None
            else None
        )


def load_fills(path: Path) -> List[_FillSample]:
    samples = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        required = {"ts_epoch_s", "market", "venue", "side", "size", "price"}
        if not required.issubset(reader.fieldnames or set()):
            raise SystemExit(
                f"{path}: missing required columns "
                f"{required - set(reader.fieldnames or [])}"
            )
        for row in reader:
            samples.append(_FillSample(
                ts=row["ts_epoch_s"],
                market=row["market"],
                venue=row["venue"],
                side=row["side"],
                size=row["size"],
                price=row["price"],
                mid_price=row.get("mid_price"),
                realized_slippage_bps=row.get("realized_slippage_bps"),
            ))
    return samples


def _compute_xy(samples: List[_FillSample]) -> Tuple[np.ndarray, np.ndarray]:
    """Extract (size, |slippage_bps|) arrays for the fitter.

    If the CSV doesn't contain realized_slippage_bps, derives it from
    (fill_price, mid_price). If neither is available, bails — we can't
    calibrate impact without realized impact measurements.
    """
    xs = []
    ys = []
    for s in samples:
        if s.realized_slippage_bps is not None:
            slip = abs(s.realized_slippage_bps)
        elif s.mid_price:
            if s.side in ("long", "buy"):
                slip = abs((s.price - s.mid_price) / s.mid_price) * 10_000
            else:
                slip = abs((s.mid_price - s.price) / s.mid_price) * 10_000
        else:
            continue
        if s.size <= 0 or slip < 0:
            continue
        xs.append(s.size)
        ys.append(slip)
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)


# ---------------------------------------------------------------------------
# Direct fitters (bypass CalibrationEngine.calibrate which reads from store)
# ---------------------------------------------------------------------------

def _fit(xs: np.ndarray, ys: np.ndarray, model: str) -> CalibrationReport:
    """Thin wrapper that exposes CalibrationEngine._fit_* on raw arrays."""
    # Build a no-op store shim; the engine's internal fitters don't touch it.
    class _ShimStore:
        pass

    engine = CalibrationEngine(_ShimStore())
    if model == "power_law":
        a, b, a_ci, b_ci, r2 = engine._fit_power_law(xs, ys)
    elif model == "sqrt":
        a, b, a_ci, b_ci, r2 = engine._fit_sqrt(xs, ys)
    else:
        raise ValueError(f"Unknown model {model!r}")

    # Very simple CV: 80/20 holdout repeated 5 times.
    cv_scores = []
    rng = np.random.default_rng(42)
    n = len(xs)
    for _ in range(5):
        idx = rng.permutation(n)
        split = int(n * 0.8)
        tr, ho = idx[:split], idx[split:]
        if model == "power_law":
            a_tr, b_tr, _, _, _ = engine._fit_power_law(xs[tr], ys[tr])
            pred = a_tr * np.power(xs[ho], b_tr)
        else:
            a_tr, _, _, _, _ = engine._fit_sqrt(xs[tr], ys[tr])
            pred = a_tr * np.sqrt(xs[ho])
        ss_res = float(np.sum((ys[ho] - pred) ** 2))
        ss_tot = float(np.sum((ys[ho] - ys[ho].mean()) ** 2) or 1.0)
        cv_scores.append(1.0 - ss_res / ss_tot)
    cv_r2 = float(np.mean(cv_scores))

    # MAE / MAPE on in-sample
    if model == "power_law":
        pred = a * np.power(xs, b)
    else:
        pred = a * np.sqrt(xs)
    mae = float(np.mean(np.abs(ys - pred)))
    mape = float(np.mean(np.abs((ys - pred) / np.maximum(ys, 1e-9)) * 100))

    return CalibrationReport(
        venue="", market="",
        num_fills=int(n),
        period_start=0, period_end=0,
        model_type=model,
        coefficient_a=float(a),
        exponent_b=float(b),
        a_ci=(float(a_ci[0]), float(a_ci[1])),
        b_ci=(float(b_ci[0]), float(b_ci[1])),
        r_squared=float(r2),
        mae_bps=mae,
        mape_pct=mape,
        cv_r_squared=cv_r2,
        current_impact_coeff=0.0,
        recommended_impact_coeff=0.0,
    )


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------

def _read_current_coefficient(flint_yaml: Path, venue: str) -> Optional[float]:
    if not flint_yaml.exists():
        return None
    try:
        import yaml
        data = yaml.safe_load(flint_yaml.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    venues = (data.get("venues") or {})
    v = venues.get(venue) or {}
    return v.get("impact_coefficient")


def _write_coefficient(flint_yaml: Path, venue: str, new_coeff: float) -> None:
    import yaml
    data = yaml.safe_load(flint_yaml.read_text(encoding="utf-8")) or {}
    data.setdefault("venues", {}).setdefault(venue, {})["impact_coefficient"] = \
        float(new_coeff)
    flint_yaml.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _render(venue: str, market: str, n: int, power: CalibrationReport,
            sqrtr: CalibrationReport, current: Optional[float],
            recommended: float, drift_pct: float, drift_threshold: float,
            period_first_ts: int, period_last_ts: int) -> Tuple[str, bool]:
    drift_ok = drift_pct <= drift_threshold
    best_model = "power_law" if power.cv_r_squared >= sqrtr.cv_r_squared else "sqrt"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def _ck(ok):
        return "✓" if ok else "⚠"

    lines = []
    lines.append(f"# Slippage Calibration — {venue} / {market}")
    lines.append("")
    lines.append(f"**Generated:** {now}")
    lines.append(f"**Fills:** {n}")
    lines.append(f"**Period:** {datetime.fromtimestamp(period_first_ts, timezone.utc).strftime('%Y-%m-%d')} → "
                 f"{datetime.fromtimestamp(period_last_ts, timezone.utc).strftime('%Y-%m-%d')}")
    lines.append(f"**Best model (by CV R²):** `{best_model}`")
    lines.append("")
    lines.append("## Fits")
    lines.append("")
    lines.append("| Model | a | b | R² (in-sample) | R² (CV) | MAE (bps) | MAPE |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    lines.append(
        f"| power_law | `{power.coefficient_a:.4f}` | `{power.exponent_b:.3f}` "
        f"| `{power.r_squared:.3f}` | `{power.cv_r_squared:.3f}` "
        f"| `{power.mae_bps:.2f}` | `{power.mape_pct:.1f}%` |"
    )
    lines.append(
        f"| sqrt | `{sqrtr.coefficient_a:.4f}` | `0.500` "
        f"| `{sqrtr.r_squared:.3f}` | `{sqrtr.cv_r_squared:.3f}` "
        f"| `{sqrtr.mae_bps:.2f}` | `{sqrtr.mape_pct:.1f}%` |"
    )
    lines.append("")
    lines.append("## Drift vs stored impact_coefficient")
    lines.append("")
    if current is None:
        lines.append("_No prior `impact_coefficient` in `flint.yaml` — this is the first calibration._")
    else:
        lines.append(f"- Stored `impact_coefficient`: `{current:.4f}`")
        lines.append(f"- Recommended: `{recommended:.4f}`")
        lines.append(
            f"- Drift: `{drift_pct:.1f}%` "
            f"(threshold `≤ {drift_threshold:.0f}%`) {_ck(drift_ok)}"
        )
        if not drift_ok:
            lines.append("")
            lines.append(
                "> ⚠️ **Drift exceeds threshold.** Re-run with `--force --write-yaml` "
                "to overwrite."
            )
    lines.append("")
    lines.append("## Recommended venue entry (`flint.yaml`)")
    lines.append("")
    lines.append("```yaml")
    lines.append("venues:")
    lines.append(f"  {venue}:")
    lines.append(f"    impact_coefficient: {recommended:.4f}  # from calibration")
    lines.append("```")
    return "\n".join(lines), drift_ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fills", type=Path, required=True,
                        help="Fill CSV (see docs/reference/custom-data-schema.md#fills)")
    parser.add_argument("--market", required=True)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--write-yaml", type=Path, default=None,
                        help="Write calibrated coefficient back to flint.yaml")
    parser.add_argument("--force", action="store_true",
                        help="Allow writing coefficients even when drift > 15%")
    parser.add_argument("--drift-threshold-pct", type=float, default=15.0)
    args = parser.parse_args()

    samples = load_fills(args.fills)
    samples = [s for s in samples
               if s.market == args.market and s.venue == args.venue]
    if not samples:
        raise SystemExit(
            f"No fills matched market={args.market} venue={args.venue}"
        )

    xs, ys = _compute_xy(samples)
    if len(xs) < 10:
        raise SystemExit(
            f"Need at least 10 fills with slippage data; got {len(xs)}"
        )

    power = _fit(xs, ys, "power_law")
    sqrtr = _fit(xs, ys, "sqrt")

    # Pick best by CV R² and derive recommended impact_coefficient.
    best = power if power.cv_r_squared >= sqrtr.cv_r_squared else sqrtr
    recommended = float(best.coefficient_a)

    current = _read_current_coefficient(
        args.write_yaml or Path("flint.yaml"), args.venue
    )
    drift_pct = 0.0
    if current is not None and current != 0.0:
        drift_pct = abs(recommended - current) / abs(current) * 100.0

    period_first = min(s.ts for s in samples)
    period_last = max(s.ts for s in samples)

    markdown, drift_ok = _render(
        venue=args.venue, market=args.market,
        n=len(xs), power=power, sqrtr=sqrtr,
        current=current, recommended=recommended,
        drift_pct=drift_pct, drift_threshold=args.drift_threshold_pct,
        period_first_ts=period_first, period_last_ts=period_last,
    )

    out = args.out
    if out is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out = Path("artifacts/calibration") / f"{args.market}-{args.venue}-{date_str}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    print(f"wrote {out}")

    if args.write_yaml:
        if not drift_ok and not args.force:
            print(
                f"ERROR: drift {drift_pct:.1f}% > threshold "
                f"{args.drift_threshold_pct}% — pass --force to override",
                file=sys.stderr,
            )
            sys.exit(1)
        _write_coefficient(args.write_yaml, args.venue, recommended)
        print(f"Updated {args.write_yaml} — {args.venue}.impact_coefficient = "
              f"{recommended:.4f}")

    sys.exit(0 if drift_ok else 2)


if __name__ == "__main__":
    main()
