"""Data quality checks — gap detection, outlier detection, completeness."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from ..models import Candle


@dataclass
class DataQualityReport:
    """Results of a data quality check."""
    total_candles: int = 0
    expected_candles: int = 0
    completeness_pct: float = 100.0
    gaps: List[Tuple[int, int]] = field(default_factory=list)  # (start_ts, end_ts)
    outliers: List[Tuple[int, float]] = field(default_factory=list)  # (ts, price)
    duplicates: int = 0


def check_candle_quality(
    candles: List[Candle],
    resolution_s: int,
    outlier_std_threshold: float = 5.0,
) -> DataQualityReport:
    """Run quality checks on a list of candles.

    Detects:
    - Gaps: missing candle periods
    - Outliers: prices that deviate > N std devs from rolling mean
    - Duplicates: same timestamp appearing twice
    - Completeness: % of expected candles present
    """
    if not candles:
        return DataQualityReport()

    report = DataQualityReport(total_candles=len(candles))

    # Sort by timestamp
    sorted_candles = sorted(candles, key=lambda c: c.ts)

    # Expected candle count
    ts_range = sorted_candles[-1].ts - sorted_candles[0].ts
    if resolution_s > 0:
        report.expected_candles = (ts_range // resolution_s) + 1
        if report.expected_candles > 0:
            report.completeness_pct = (len(candles) / report.expected_candles) * 100

    # Gap detection
    for i in range(1, len(sorted_candles)):
        diff = sorted_candles[i].ts - sorted_candles[i - 1].ts
        if diff > resolution_s * 1.5:  # allow 50% tolerance
            report.gaps.append((sorted_candles[i - 1].ts, sorted_candles[i].ts))

    # Duplicate detection
    seen_ts = set()
    for c in sorted_candles:
        if c.ts in seen_ts:
            report.duplicates += 1
        seen_ts.add(c.ts)

    # Outlier detection (Z-score on returns)
    if len(sorted_candles) > 20:
        closes = [c.close for c in sorted_candles]
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1]
                   for i in range(1, len(closes)) if closes[i - 1] != 0]
        if returns:
            mean_ret = sum(returns) / len(returns)
            variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
            std_ret = variance ** 0.5

            if std_ret > 0:
                for i, ret in enumerate(returns):
                    z_score = abs(ret - mean_ret) / std_ret
                    if z_score > outlier_std_threshold:
                        report.outliers.append((
                            sorted_candles[i + 1].ts,
                            sorted_candles[i + 1].close,
                        ))

    return report
