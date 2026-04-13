"""Market regime definitions for multi-regime backtesting.

Defines curated date ranges covering different crypto market regimes
(bull, bear, crash, sideways, high-vol) from Dec 2023 to Apr 2026.

Keep in sync with: ui/src/constants/regimes.ts
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Regime:
    """A named market regime with a date range."""
    id: str
    label: str
    regime_type: str  # bull, bear, crash, sideways, high_vol
    start_ts: int     # Unix timestamp
    end_ts: int       # Unix timestamp
    color: str        # Hex color for UI
    description: str

    @property
    def start_date(self) -> str:
        return datetime.fromtimestamp(self.start_ts, tz=timezone.utc).strftime("%Y-%m-%d")

    @property
    def end_date(self) -> str:
        return datetime.fromtimestamp(self.end_ts, tz=timezone.utc).strftime("%Y-%m-%d")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start_date"] = self.start_date
        d["end_date"] = self.end_date
        return d


def _ts(date_str: str) -> int:
    """Convert 'YYYY-MM-DD' to Unix timestamp (UTC midnight)."""
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


REGIMES: List[Regime] = [
    Regime(
        id="pre_etf_consolidation",
        label="Pre-ETF Consolidation",
        regime_type="sideways",
        start_ts=_ts("2023-12-01"),
        end_ts=_ts("2024-01-09"),
        color="#78788a",
        description="Post-FTX recovery, low volatility, awaiting ETF catalyst",
    ),
    Regime(
        id="etf_bull_run",
        label="ETF Bull Run",
        regime_type="bull",
        start_ts=_ts("2024-01-10"),
        end_ts=_ts("2024-05-31"),
        color="#57c84d",
        description="BTC spot ETF approval drives +70% rally across majors",
    ),
    Regime(
        id="summer_correction",
        label="Summer Correction",
        regime_type="bear",
        start_ts=_ts("2024-06-01"),
        end_ts=_ts("2024-08-31"),
        color="#e84d4d",
        description="Profit-taking phase, BTC -12%, moderate volatility",
    ),
    Regime(
        id="recovery_rally",
        label="Recovery Rally",
        regime_type="bull",
        start_ts=_ts("2024-09-01"),
        end_ts=_ts("2024-12-31"),
        color="#57c84d",
        description="Re-acceleration to new ATHs, BTC +58%, SOL +40%",
    ),
    Regime(
        id="peak_distribution",
        label="Peak & Distribution",
        regime_type="high_vol",
        start_ts=_ts("2025-01-01"),
        end_ts=_ts("2025-03-31"),
        color="#e8a849",
        description="Topping pattern with high volatility and funding spikes",
    ),
    Regime(
        id="extended_decline",
        label="Extended Decline",
        regime_type="bear",
        start_ts=_ts("2025-04-01"),
        end_ts=_ts("2025-09-30"),
        color="#e84d4d",
        description="Slow grind lower, SOL $240 to $186, declining momentum",
    ),
    Regime(
        id="crash_phase_1",
        label="Crash Phase 1",
        regime_type="crash",
        start_ts=_ts("2025-10-01"),
        end_ts=_ts("2025-12-31"),
        color="#c84d4d",
        description="Accelerating sell-off, SOL $186 to $125, capitulation events",
    ),
    Regime(
        id="crash_phase_2",
        label="Crash Phase 2",
        regime_type="crash",
        start_ts=_ts("2026-01-01"),
        end_ts=_ts("2026-04-09"),
        color="#c84d4d",
        description="Continued decline, SOL $125 to $83, dead cat bounces",
    ),
]

REGIME_MAP: Dict[str, Regime] = {r.id: r for r in REGIMES}

REGIME_TYPES = {
    "bull": {"label": "Bull", "color": "#57c84d"},
    "bear": {"label": "Bear", "color": "#e84d4d"},
    "crash": {"label": "Crash", "color": "#c84d4d"},
    "sideways": {"label": "Sideways", "color": "#78788a"},
    "high_vol": {"label": "High Vol", "color": "#e8a849"},
}


def get_regime(regime_id: str) -> Optional[Regime]:
    """Look up a regime by ID."""
    return REGIME_MAP.get(regime_id)


def get_all_regime_ids() -> List[str]:
    """Return all regime IDs in chronological order."""
    return [r.id for r in REGIMES]


def get_regimes_by_type(regime_type: str) -> List[Regime]:
    """Return all regimes of a given type."""
    return [r for r in REGIMES if r.regime_type == regime_type]
