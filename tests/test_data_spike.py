"""Guard for the §19.4 throughput spike harness (board slice 2.9).

Runs the harness at *smoke* volume only — the full budget-volume run lives in
`scripts/spike_throughput.py` and is not part of the fast suite. This test keeps
the harness from bit-rotting before Phase 3 can re-run it against the real engine,
and pins the load-bearing pieces: the representative frame is a real 20-level
book (D26 — hand-authored unit input, not generated), the Arrow schemas the
loops iterate match the ingestion path, and the pass/fail + RSS machinery works.
"""

from __future__ import annotations

from decimal import Decimal

from scripts.spike_throughput import (
    RSS_BUDGET_BYTES,
    TIER_A_FULL_SNAPSHOTS,
    TIER_C_ROWS,
    build_candle_table,
    build_depth_table,
    format_report,
    peak_rss_bytes,
    run_spike,
    tier_a_fill_loop,
    tier_c_fill_loop,
)


def test_representative_book_has_20_levels_per_side():
    # Real hand-authored unit input in the archive shape, not fabricated market
    # data: a fixed 20-level book normalized through the production path.
    table = build_depth_table(1)
    assert table.num_rows == 1
    assert len(table.column("bids").to_pylist()[0]) == 20
    assert len(table.column("asks").to_pylist()[0]) == 20
    # Each level is a [px, sz] pair matching DEPTH_SCHEMA.
    assert len(table.column("bids").to_pylist()[0][0]) == 2


def test_tier_c_loop_fills_every_bar_and_accrues_decimal():
    table = build_candle_table(100)
    fills, equity = tier_c_fill_loop(table)
    assert fills == 100
    assert isinstance(equity, Decimal)  # money stays Decimal (§5)


def test_tier_a_loop_fills_from_the_book():
    table = build_depth_table(50)
    fills, cash = tier_a_fill_loop(table)
    assert fills == 50  # the sized order fills off the 20-level book each snapshot
    assert isinstance(cash, Decimal)


def test_smoke_spike_runs_and_reports():
    results = run_spike(smoke=True)
    labels = [r.label for r in results]
    assert any("Tier-C" in x for x in labels)
    assert any("Tier-A" in x for x in labels)
    text = format_report(results, peak_rss_bytes())
    assert "PASS" in text or "FAIL" in text
    assert "RSS" in text


def test_budget_constants_match_the_spec():
    # 6-month, 3-market, 1-minute Tier-C row count and the full Tier-A projection.
    assert TIER_C_ROWS == 182 * 1440 * 3
    assert TIER_A_FULL_SNAPSHOTS == 86_400 * 3 * 182
    assert RSS_BUDGET_BYTES == 4 * 1024**3
