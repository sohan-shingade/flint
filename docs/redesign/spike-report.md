# Python-throughput spike report — §19.4 gate (board slice 2.9)

Harness: `scripts/spike_throughput.py`. Run: `python scripts/spike_throughput.py`.

## Verdict

| Workload | Budget (§19.4) | Measured | Projected | Result |
|---|---|---|---|---|
| Tier-C, 6-month, 3-market, 1-minute | ≤ 60 s | 786,240 bars in **3.2 s** (243,911 bars/s) | — | **PASS** (≈19× headroom) |
| Tier-A (HL depth), same 6-month window | ≤ 15 min | 86,400 snaps in **6.8 s** (12,697 snaps/s) | 47.2 M snaps → **~62 min** | **FAIL** (~4.1× over) |
| Peak RSS | ≤ 4 GiB | **818 MiB** | — | **PASS** |

**OVERALL: FAIL** — the naive row-by-row Python fill path clears Tier-C and RSS
comfortably but misses the Tier-A depth-replay budget by roughly 4×. Per §19.4,
this triggers the Arrow-native fill-path design **now** (before the engine
exists): see `docs/redesign/ARROW-FILL-PATH.md`. Team-lead flagged.

Numbers are single-run, machine-local (this dev host), indicative not certified —
a spike answers "which order of magnitude," and Tier-A is a clear order-of-
magnitude miss.

## What was measured, and why it is the right pessimistic proxy

The engine is Phase 3 and does not exist yet, so the harness runs a **stub fill
loop** that mirrors the per-bar / per-snapshot shape of §6.1/§6.3 on the
deliberately *naive* path: materialize Arrow columns to Python lists, iterate
row-by-row, accumulate money in `Decimal` (§5). §19.4 defines the gate precisely
against this path — *"if the spike fails, book/trade data feeds the fill model via
an Arrow-native columnar path (dataclasses stay for orders/fills/events only)."*
So measuring the pessimistic path is the point: a Tier-C pass means vectorization
is a v2 speed-up there, and a Tier-A fail means vectorization is a v1 necessity
for depth replay.

- **Tier-C loop:** per bar, read OHLC (float), parametric fill with slippage
  (float), accrue notional fee + PnL mark in `Decimal`.
- **Tier-A loop:** per depth snapshot, walk the ask book to fill a fixed order
  size accumulating a VWAP fill + queue-position bookkeeping — representative of
  a Tier-A queue-aware fill. Note the stub is *light* (it walks ~1 level for the
  sized order and does no order-state/event work); the real engine's per-snapshot
  cost is heavier, so the real Tier-A gap is at least this wide.

## Robustness: the Tier-A verdict does not hinge on the cadence assumption

Tier-A replays depth at the archive's recorded cadence, not on 1-minute bars, so
the full-window snapshot count depends on how often HL archives a book. The
harness assumes **1 snapshot/second/coin** (documented, pending a real-day
measurement). Inverting the budget gives the **break-even cadence**:

```
15 min × 12,697 snaps/s = 11.4 M affordable snapshots
÷ (182 days × 3 markets) = 20,929 snapshots / market-day
÷ 86,400 s/day           = 0.24 snapshots/second/coin  (1 every ~4.1 s)
```

The naive path only meets Tier-A if HL archives **fewer than one book snapshot
every ~4 seconds per coin** — far below real HL l2Book density for liquid coins.
Under any realistic cadence, the naive path fails. The verdict is not sensitive
to the exact 1/s guess.

## Data provenance (D26) — no synthetic market data

The real HL S3 depth day could not be downloaded: the archive
(`hyperliquid-archive.s3.amazonaws.com`) is a **Requester-Pays** bucket —
anonymous HTTPS returns `403 AccessDenied` ("Anonymous users cannot invoke
requests against Requester Pays buckets. Please authenticate."). No public
download exists, and billing the operator's AWS account for requester-pays egress
was out of scope for a benchmark.

Fallback (as instructed): the harness replays a **single hand-authored,
representative 20-level-per-side l2Book frame** — a real unit input in the exact
archive shape, normalized through the production `normalize_l2book` /
`books_to_arrow` — tiled up to the benchmark row count. Per-row CPU cost is a
function of the code path and book width, not the price values, so tiling
faithfully measures loop throughput. It does **not** validate data realism (the
ingestion quality bars own that, §9.0). This is a throughput stopwatch, not a
backtest; no fabricated market data feeds any result.

## Carry-forwards

1. **Re-run on a real depth day** once archive credentials exist (AWS creds +
   `--request-payer requester`, or a vendored/recorded dense day). A real day
   varies book widths and would refine the per-snapshot cost and the true cadence
   — but is very unlikely to reverse a 4× miss.
2. **Arrow-native fill path** is the design consequence — owned by the Phase-3
   engine builder; see `docs/redesign/ARROW-FILL-PATH.md`.
3. The harness lives at `scripts/spike_throughput.py` and is guarded by a fast
   smoke test (`tests/test_data_spike.py`) so it does not bit-rot before Phase 3
   can re-run it against the real engine.
