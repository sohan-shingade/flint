"""Tests for flint.regimes — 8 market regime definitions and utility functions."""

import re
from dataclasses import FrozenInstanceError

import pytest

from flint.regimes import (
    REGIME_TYPES,
    REGIMES,
    get_all_regime_ids,
    get_regime,
    get_regimes_by_type,
)


# ---------------------------------------------------------------------------
# Regime data invariants
# ---------------------------------------------------------------------------

class TestRegimeData:
    def test_regime_count(self):
        assert len(REGIMES) == 8

    def test_start_before_end(self):
        for r in REGIMES:
            assert r.start_ts < r.end_ts, f"{r.id}: start_ts >= end_ts"

    def test_no_overlaps(self):
        sorted_regimes = sorted(REGIMES, key=lambda r: r.start_ts)
        for a, b in zip(sorted_regimes, sorted_regimes[1:]):
            assert a.end_ts <= b.start_ts, (
                f"Overlap: {a.id} ends {a.end_date} but {b.id} starts {b.start_date}"
            )

    def test_valid_types(self):
        for r in REGIMES:
            assert r.regime_type in REGIME_TYPES, (
                f"{r.id} has unknown regime_type '{r.regime_type}'"
            )

    def test_all_have_colors(self):
        for r in REGIMES:
            assert isinstance(r.color, str) and r.color.startswith("#") and len(r.color) >= 4

    def test_all_have_descriptions(self):
        for r in REGIMES:
            assert isinstance(r.description, str) and len(r.description) > 0

    def test_chronological_order(self):
        timestamps = [r.start_ts for r in REGIMES]
        assert timestamps == sorted(timestamps), "REGIMES not in chronological order"


# ---------------------------------------------------------------------------
# Regime properties and serialisation
# ---------------------------------------------------------------------------

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TestRegimeProperties:
    def test_start_date_format(self):
        for r in REGIMES:
            assert DATE_RE.match(r.start_date), f"{r.id} start_date '{r.start_date}' bad format"

    def test_end_date_format(self):
        for r in REGIMES:
            assert DATE_RE.match(r.end_date), f"{r.id} end_date '{r.end_date}' bad format"

    def test_to_dict_includes_dates(self):
        r = REGIMES[0]
        d = r.to_dict()
        for key in ("id", "label", "regime_type", "start_ts", "end_ts", "color",
                     "description", "start_date", "end_date"):
            assert key in d, f"to_dict() missing key '{key}'"
        assert d["start_date"] == r.start_date
        assert d["end_date"] == r.end_date

    def test_frozen(self):
        r = REGIMES[0]
        with pytest.raises(FrozenInstanceError):
            r.id = "changed"


# ---------------------------------------------------------------------------
# get_regime()
# ---------------------------------------------------------------------------

class TestGetRegime:
    def test_valid_id(self):
        for r in REGIMES:
            result = get_regime(r.id)
            assert result is r

    def test_invalid_id(self):
        assert get_regime("nonexistent_regime") is None

    def test_empty_id(self):
        assert get_regime("") is None


# ---------------------------------------------------------------------------
# get_all_regime_ids()
# ---------------------------------------------------------------------------

class TestGetAllRegimeIds:
    def test_returns_all_8(self):
        ids = get_all_regime_ids()
        assert len(ids) == 8

    def test_order_matches_regimes(self):
        ids = get_all_regime_ids()
        expected = [r.id for r in REGIMES]
        assert ids == expected


# ---------------------------------------------------------------------------
# get_regimes_by_type()
# ---------------------------------------------------------------------------

class TestGetRegimesByType:
    def test_bull_regimes(self):
        bulls = get_regimes_by_type("bull")
        assert len(bulls) == 2
        assert {r.id for r in bulls} == {"etf_bull_run", "recovery_rally"}

    def test_bear_regimes(self):
        bears = get_regimes_by_type("bear")
        assert len(bears) == 2
        assert {r.id for r in bears} == {"summer_correction", "extended_decline"}

    def test_crash_regimes(self):
        crashes = get_regimes_by_type("crash")
        assert len(crashes) == 2
        assert {r.id for r in crashes} == {"crash_phase_1", "crash_phase_2"}

    def test_sideways_regimes(self):
        sideways = get_regimes_by_type("sideways")
        assert len(sideways) == 1
        assert sideways[0].id == "pre_etf_consolidation"

    def test_high_vol_regimes(self):
        high_vol = get_regimes_by_type("high_vol")
        assert len(high_vol) == 1
        assert high_vol[0].id == "peak_distribution"

    def test_invalid_type(self):
        assert get_regimes_by_type("unknown") == []

    def test_all_types_covered(self):
        all_from_types = []
        for t in REGIME_TYPES:
            all_from_types.extend(get_regimes_by_type(t))
        assert len(all_from_types) == 8
        assert {r.id for r in all_from_types} == {r.id for r in REGIMES}


# ---------------------------------------------------------------------------
# REGIME_TYPES constant
# ---------------------------------------------------------------------------

class TestRegimeTypeConstants:
    def test_five_types(self):
        assert len(REGIME_TYPES) == 5

    def test_each_has_label_and_color(self):
        for type_id, info in REGIME_TYPES.items():
            assert "label" in info, f"REGIME_TYPES['{type_id}'] missing 'label'"
            assert "color" in info, f"REGIME_TYPES['{type_id}'] missing 'color'"
