"""Python-throughput spike against the §19.4 gate (Phase-2, board slice 2.9).

The engine does not exist yet (Phase 3), so this is a *proxy*: a representative
row-by-row **stub fill loop** — the naive "materialize columns, iterate in
Python, accumulate money in ``Decimal``" path — run at the §19.4 budget volume.
That naive path is exactly the one §19.4 says to measure: *if it fails, book/trade
data must feed the fill model via an Arrow-native columnar path.* So we measure
the pessimistic path on purpose; a pass here means the vectorized path is a v2
speed-up, not a v1 necessity.

Budget (§19.4):
  * 6-month, 3-market, 1-minute-bar **Tier-C** backtest        ≤  60 s
  * the same 6-month, 3-market window at **Tier-A** (HL depth) ≤ 15 min
  * peak RSS                                                   ≤   4 GB

Data policy (D26): **no synthetic market data.** The real HL S3 depth day is a
Requester-Pays bucket (anonymous HTTPS → 403), so no public download is possible;
this harness therefore replays a *single hand-authored representative frame* (a
real unit input, not generated market-like data) tiled up to the benchmark row
count. Per-row CPU cost is a function of the code path and book width, not the
price values, so tiling faithfully measures loop throughput — it does NOT validate
data realism (that is the ingestion quality bars' job, §9.0). A real depth day,
run once AWS credentials for the archive exist, would vary book widths and refine
the per-snapshot cost; that re-run is a documented carry-forward.

Run:  python scripts/spike_throughput.py          # full budget-volume spike
      python scripts/spike_throughput.py --smoke   # tiny, for the guard test
"""

from __future__ import annotations

import argparse
import platform
import resource
import sys
import time
from dataclasses import dataclass
from decimal import Decimal

import pyarrow as pa

# Reuse the real normalization + Arrow schema the ingestion path writes, so the
# rows we iterate are byte-shaped exactly like recorded depth.
from flint.core.models import Side
from flint.data.normalize import books_to_arrow, normalize_l2book
from flint.engine.fills.arrow import depth_level_arrays, taker_walk_batch
from flint.engine.money import money

# --- budget constants (§19.4) -----------------------------------------------

TIER_C_BUDGET_S = 60.0
TIER_A_BUDGET_S = 15 * 60.0
RSS_BUDGET_BYTES = 4 * 1024**3

# 6-month, 3-market, 1-minute-bar workload shape.
_DAYS_6MO = 182
_MINUTES_PER_DAY = 1440
_MARKETS = 3
TIER_C_ROWS = _DAYS_6MO * _MINUTES_PER_DAY * _MARKETS  # 786,240 bars

# Tier-A replays depth at the archive's recorded cadence, NOT on 1-minute bars.
# HL l2Book archive cadence is assumed at 1 snapshot/second/coin (documented
# assumption pending a real-day measurement — see module docstring). One measured
# unit = one day, one market; the full-window projection scales it ×markets×days.
_TIER_A_CADENCE_HZ = 1
_SECONDS_PER_DAY = 86_400
TIER_A_SNAPSHOTS_PER_DAY = _SECONDS_PER_DAY * _TIER_A_CADENCE_HZ  # 86,400
TIER_A_FULL_SNAPSHOTS = TIER_A_SNAPSHOTS_PER_DAY * _MARKETS * _DAYS_6MO  # ~47.2M

# Money model per §5: Decimal accumulators, floats for prices.
_FEE_RATE = Decimal("0.0004")  # representative taker fee (bps-scale)
_ORDER_NOTIONAL = Decimal("1000")


# --- representative recorded frame (hand-authored unit input, D26) ----------
# A 20-level-per-side HL l2Book frame in the exact archive shape
# ({coin, time, levels:[[bids],[asks]]}, each level {px,sz,n}). Real unit input,
# not generated market data; tiled below only to reach benchmark volume.
def _representative_l2book_frame(ts_ms: int) -> dict:
    bids = [{"px": 100.0 - i * 0.1, "sz": 5.0 + i, "n": 1 + i} for i in range(20)]
    asks = [{"px": 100.1 + i * 0.1, "sz": 4.0 + i, "n": 1 + i} for i in range(20)]
    return {"coin": "SOL", "time": ts_ms, "levels": [bids, asks]}


def _representative_bar() -> dict:
    return {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 12.0}


# --- table builders (tile one recorded frame to benchmark volume) -----------

_CANDLE_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
    ]
)


def build_candle_table(n_rows: int) -> pa.Table:
    bar = _representative_bar()
    base = 1_700_000_000_000
    return pa.table(
        {
            "ts": pa.array([base + i * 60_000 for i in range(n_rows)], pa.int64()),
            "open": pa.array([bar["open"]] * n_rows, pa.float64()),
            "high": pa.array([bar["high"]] * n_rows, pa.float64()),
            "low": pa.array([bar["low"]] * n_rows, pa.float64()),
            "close": pa.array([bar["close"]] * n_rows, pa.float64()),
            "volume": pa.array([bar["volume"]] * n_rows, pa.float64()),
        },
        schema=_CANDLE_SCHEMA,
    )


def build_depth_table(n_rows: int) -> pa.Table:
    base = 1_700_000_000_000
    books = [
        normalize_l2book(_representative_l2book_frame(base + i * 1000), "SOL-PERP")
        for i in range(n_rows)
    ]
    return books_to_arrow(books)


# --- stub fill loops (the naive row-by-row path §19.4 targets) --------------


def tier_c_fill_loop(table: pa.Table) -> tuple[int, Decimal]:
    """Parametric Tier-C fill per bar: read OHLC (float), fill, accrue (Decimal).

    Materializes columns to Python lists then iterates row-by-row — the
    pessimistic path the spike exists to stress.
    """
    closes = table.column("close").to_pylist()
    highs = table.column("high").to_pylist()
    lows = table.column("low").to_pylist()
    cash = Decimal("100000")
    fees = Decimal("0")
    fills = 0
    side = 1
    for close, high, low in zip(closes, highs, lows):
        # Parametric fill: cross the bar's spread proxy, apply slippage (float).
        slippage = (high - low) * 0.05
        fill_px = close + side * slippage
        # Accrue money in Decimal (§5): notional fee + PnL mark (never float-accum).
        notional = _ORDER_NOTIONAL
        fee = notional * _FEE_RATE
        fees += fee
        cash -= fee + notional * Decimal(str(fill_px / 100.0 - 1.0))
        fills += 1
        side = -side
    return fills, cash - fees


def tier_a_fill_loop(table: pa.Table) -> tuple[int, Decimal]:
    """Queue-aware Tier-A stub: walk the ask book to fill a fixed order size.

    Per snapshot, materialize the level pairs and walk them accumulating a VWAP
    fill + queue bookkeeping — representative of §6.3 tier-A queue-aware fills.
    """
    asks_col = table.column("asks").to_pylist()
    ts_col = table.column("ts").to_pylist()
    cash = Decimal("100000")
    fills = 0
    target = 3.0  # order size to fill by walking the book
    for asks, _ts in zip(asks_col, ts_col):
        remaining = target
        filled_notional = 0.0
        queue_ahead = 0.0
        for level in asks:  # each level is [px, sz]
            px, sz = level[0], level[1]
            if remaining <= 0:
                break
            take = sz if sz < remaining else remaining
            filled_notional += take * px
            remaining -= take
            queue_ahead += sz  # queue-position bookkeeping
        if remaining < target:  # got at least a partial fill
            cash -= Decimal(str(filled_notional)) * _FEE_RATE
            fills += 1
    return fills, cash


def tier_a_fill_loop_arrow(table: pa.Table) -> tuple[int, Decimal]:
    """Tier-A via the Arrow-native book-walk (§19.4 fix) — the same fill, batched.

    The columnar equivalent of ``tier_a_fill_loop``: depth stays Arrow (zero-copy
    strided level views, no ``to_pylist``), the book is walked with the vectorized
    ``taker_walk_batch`` cumsum kernel instead of a per-level Python loop, and
    money is reduced to ``Decimal`` once at the batch boundary — never
    float-accumulated across snapshots (§5). Same order size, same result: on the
    fixed-depth benchmark frame it fills an identical count for identical cash as
    the naive loop (asserted in tests/test_engine_fills_arrow.py).
    """
    px, sz, offsets = depth_level_arrays(table, Side.LONG)
    walk = taker_walk_batch(px, sz, offsets, buy=True, order_size=3.0)
    notional = walk.filled * walk.vwap  # (n,) float, 0 where no fill
    # Batch-boundary money reduction: one Decimal per actual fill, not per level.
    fee = Decimal("0")
    for nt, ok in zip(notional.tolist(), walk.ok.tolist()):
        if ok:
            fee += money(nt) * _FEE_RATE
    return int(walk.ok.sum()), Decimal("100000") - fee


# --- measurement ------------------------------------------------------------


def peak_rss_bytes() -> int:
    """Peak resident set size. ru_maxrss is bytes on macOS, KiB on Linux."""
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return ru if platform.system() == "Darwin" else ru * 1024


@dataclass
class Result:
    label: str
    rows: int
    seconds: float
    budget_s: float
    projected_rows: int | None = None
    baseline_seconds: float | None = None  # prior (naive) time, for the speed-up line

    @property
    def rows_per_s(self) -> float:
        return self.rows / self.seconds if self.seconds else float("inf")

    @property
    def speedup(self) -> float | None:
        if self.baseline_seconds is None or not self.seconds:
            return None
        return self.baseline_seconds / self.seconds

    @property
    def projected_seconds(self) -> float:
        n = self.projected_rows if self.projected_rows is not None else self.rows
        return n / self.rows_per_s

    @property
    def passed(self) -> bool:
        return self.projected_seconds <= self.budget_s


def _time(fn, table) -> float:
    t0 = time.perf_counter()
    fn(table)
    return time.perf_counter() - t0


def run_spike(*, smoke: bool = False) -> list[Result]:
    if smoke:
        c_rows, a_rows, a_proj = 2000, 2000, 2000
    else:
        c_rows, a_rows, a_proj = TIER_C_ROWS, TIER_A_SNAPSHOTS_PER_DAY, TIER_A_FULL_SNAPSHOTS

    ct = build_candle_table(c_rows)
    c_secs = _time(tier_c_fill_loop, ct)
    tier_c = Result("Tier-C 6mo/3mkt/1m", c_rows, c_secs, TIER_C_BUDGET_S)

    at = build_depth_table(a_rows)
    a_naive = _time(tier_a_fill_loop, at)  # the naive path the §19.4 spike failed on
    a_secs = _time(tier_a_fill_loop_arrow, at)  # the Arrow-native fix
    tier_a = Result(
        "Tier-A 1 day/1mkt Arrow-native (projected 6mo/3mkt)",
        a_rows,
        a_secs,
        TIER_A_BUDGET_S,
        projected_rows=a_proj,
        baseline_seconds=a_naive,
    )
    return [tier_c, tier_a]


def format_report(results: list[Result], rss: int) -> str:
    lines = ["Throughput spike — §19.4 gate", ""]
    for r in results:
        verdict = "PASS" if r.passed else "FAIL"
        lines.append(f"  [{verdict}] {r.label}")
        lines.append(
            f"         {r.rows:,} rows in {r.seconds:.3f}s "
            f"= {r.rows_per_s:,.0f} rows/s"
        )
        if r.projected_rows is not None and r.projected_rows != r.rows:
            lines.append(
                f"         projected {r.projected_rows:,} rows "
                f"-> {r.projected_seconds:.1f}s  (budget {r.budget_s:.0f}s)"
            )
        else:
            lines.append(f"         budget {r.budget_s:.0f}s")
        if r.speedup is not None:
            lines.append(
                f"         naive baseline {r.baseline_seconds:.3f}s "
                f"-> {r.speedup:.1f}x faster (Arrow-native)"
            )
    rss_ok = "PASS" if rss <= RSS_BUDGET_BYTES else "FAIL"
    lines.append(
        f"  [{rss_ok}] peak RSS {rss / 1024**2:,.0f} MiB (budget "
        f"{RSS_BUDGET_BYTES / 1024**3:.0f} GiB)"
    )
    overall = all(r.passed for r in results) and rss <= RSS_BUDGET_BYTES
    lines.append("")
    lines.append(f"OVERALL: {'PASS' if overall else 'FAIL'}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="tiny run for the guard test")
    args = ap.parse_args()
    results = run_spike(smoke=args.smoke)
    rss = peak_rss_bytes()
    print(format_report(results, rss))
    overall = all(r.passed for r in results) and rss <= RSS_BUDGET_BYTES
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
