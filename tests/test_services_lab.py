"""Funding/basis lab service tests (§10, §12) — hand-authored observations (D26).

These exercise the markets×venues heatmap front door: real per-cell carry from
``research.funding_lab``, the cross-venue dislocation headline, the rate_type
filter, and the load-bearing **degrade-not-reject** contract — a lab query over a
venue with no funding never raises the funding gate, it yields a ``null`` cell.
"""

from __future__ import annotations

import pyarrow as pa

from flint.data import DataManager
from flint.data.ranges import Kind
from flint.data.sources import InMemoryCacheSource
from flint.ports import TenantContext
from flint.research.funding_lab import annualize_hourly
from flint.services import funding_lab

HOUR_MS = 3_600_000


def _funding_table(
    venue: str, market: str, rows: list[tuple[int, float, str]]
) -> pa.Table:
    """rows = (ts, rate_hourly, rate_type) → a FUNDING Arrow table for one leg."""
    return pa.table(
        {
            "market": [market] * len(rows),
            "ts": [ts for ts, _, _ in rows],
            "rate_hourly": [r for _, r, _ in rows],
            "interval_s": [3600] * len(rows),
            "price_basis": ["oracle"] * len(rows),
            "rate_type": [rt for _, _, rt in rows],
            "venue": [venue] * len(rows),
        }
    )


def _dm(entries: dict[tuple[str, str, Kind], pa.Table]) -> DataManager:
    src = InMemoryCacheSource()
    for (venue, market, kind), table in entries.items():
        src.store(venue, market, kind, table)
    return DataManager(sources=[src])


TENANT = TenantContext.local()


def test_cells_carry_real_mean_and_linear_annualization():
    # Two venues, one market, three predicted hourly rates each (D26).
    hl = _funding_table(
        "hyperliquid",
        "SOL-PERP",
        [
            (0, 0.0001, "predicted"),
            (HOUR_MS, 0.0002, "predicted"),
            (2 * HOUR_MS, 0.0003, "predicted"),
        ],
    )
    bn = _funding_table(
        "binance",
        "SOL-PERP",
        [
            (0, 0.00005, "predicted"),
            (HOUR_MS, 0.00006, "predicted"),
            (2 * HOUR_MS, 0.00007, "predicted"),
        ],
    )
    dm = _dm(
        {
            ("hyperliquid", "SOL-PERP", Kind.FUNDING): hl,
            ("binance", "SOL-PERP", Kind.FUNDING): bn,
        }
    )

    out = funding_lab(
        TENANT,
        data=dm,
        markets=["SOL-PERP"],
        venues=["hyperliquid", "binance"],
        start_ms=0,
        end_ms=3 * HOUR_MS,
    )

    hl_cell = out["cells"]["hyperliquid/SOL-PERP"]
    assert hl_cell is not None
    assert hl_cell["n"] == 3
    assert abs(hl_cell["mean_hourly"] - 0.0002) < 1e-12
    # linear annualization, never compounded (§10)
    assert abs(hl_cell["annualized"] - annualize_hourly(0.0002)) < 1e-12

    bn_cell = out["cells"]["binance/SOL-PERP"]
    assert bn_cell is not None
    assert abs(bn_cell["mean_hourly"] - 0.00006) < 1e-12


def test_widest_cross_venue_dislocation_is_the_headline():
    # Three venues: HL rich, the other two clustered — HL is the unambiguous outlier
    # (with only two venues the deviations are symmetric, so a third breaks the tie).
    hl = _funding_table("hyperliquid", "SOL-PERP", [(0, 0.0010, "predicted")])
    bn = _funding_table("binance", "SOL-PERP", [(0, 0.0002, "predicted")])
    ok = _funding_table("okx", "SOL-PERP", [(0, 0.0002, "predicted")])
    dm = _dm(
        {
            ("hyperliquid", "SOL-PERP", Kind.FUNDING): hl,
            ("binance", "SOL-PERP", Kind.FUNDING): bn,
            ("okx", "SOL-PERP", Kind.FUNDING): ok,
        }
    )
    out = funding_lab(
        TENANT,
        data=dm,
        markets=["SOL-PERP"],
        venues=["hyperliquid", "binance", "okx"],
        start_ms=0,
        end_ms=HOUR_MS,
    )
    d = out["dislocation"]["SOL-PERP"]
    assert d is not None
    # benchmark ≈ 0.000467; hyperliquid +0.000533 is the widest, and positive.
    assert d["venue"] == "hyperliquid"
    assert d["dislocation_hourly"] > 0


def test_venue_with_no_funding_degrades_to_null_cell_never_rejects():
    # binance requested but has NO funding data — a lab leg degrades, never gates.
    hl = _funding_table("hyperliquid", "SOL-PERP", [(0, 0.0001, "predicted")])
    dm = _dm({("hyperliquid", "SOL-PERP", Kind.FUNDING): hl})

    out = funding_lab(
        TENANT,
        data=dm,
        markets=["SOL-PERP"],
        venues=["hyperliquid", "binance"],
        start_ms=0,
        end_ms=HOUR_MS,
    )
    # No FundingCoverageError raised (degrade-not-reject, 4260b7f ruling).
    assert out["cells"]["hyperliquid/SOL-PERP"] is not None
    assert out["cells"]["binance/SOL-PERP"] is None


def test_rate_type_filter_excludes_final_rows_by_default():
    # A predicted row and a final row; default rate_type="predicted" keeps only the former.
    mixed = _funding_table(
        "hyperliquid", "SOL-PERP", [(0, 0.0001, "predicted"), (HOUR_MS, 0.9, "final")]
    )
    dm = _dm({("hyperliquid", "SOL-PERP", Kind.FUNDING): mixed})

    out = funding_lab(
        TENANT,
        data=dm,
        markets=["SOL-PERP"],
        venues=["hyperliquid"],
        start_ms=0,
        end_ms=2 * HOUR_MS,
    )
    cell = out["cells"]["hyperliquid/SOL-PERP"]
    assert cell["n"] == 1  # the "final" 0.9 row excluded
    assert abs(cell["mean_hourly"] - 0.0001) < 1e-12


def test_empty_universe_returns_structured_empty_not_error():
    dm = _dm({})
    out = funding_lab(
        TENANT,
        data=dm,
        markets=["SOL-PERP"],
        venues=["hyperliquid"],
        start_ms=0,
        end_ms=HOUR_MS,
    )
    assert out["cells"]["hyperliquid/SOL-PERP"] is None
    assert out["dislocation"]["SOL-PERP"] is None
    assert out["markets"] == ["SOL-PERP"]
