# Arrow-native fill path — design note (triggered by the §19.4 spike)

**Status:** design note, not implementation. The §19.4 throughput spike
(`docs/redesign/spike-report.md`) FAILED the Tier-A depth-replay budget by ~4×
on the naive row-by-row Python + `Decimal` path, which §19.4 says triggers this
design *now, before the engine exists*. Ownership of the implementation is the
**Phase-3 engine builder** (task #3); this note is the carry-forward brief.

## The signal

| | Budget | Naive path | Gap |
|---|---|---|---|
| Tier-C (OHLCV, 6mo/3mkt/1m) | ≤ 60 s | 3.2 s | passes ~19× |
| Tier-A (HL depth, 6mo/3mkt) | ≤ 15 min | ~62 min | **fails ~4×** |
| RSS | ≤ 4 GiB | 818 MiB | passes |

The break-even for the naive path is **0.24 book snapshots/sec/coin** — below
real HL cadence for liquid coins. Tier-C is fine on the naive path; **only the
Tier-A depth-replay hot loop needs to change.** This is a targeted redesign, not
an engine rewrite.

## Root cause

The per-snapshot cost is dominated by Python-object overhead, not arithmetic:

- Materializing Arrow depth columns to Python lists-of-lists (`to_pylist`)
  allocates one Python `float`/`list` object per level per snapshot — millions of
  short-lived objects across a 6-month replay.
- Row-by-row Python iteration pays interpreter dispatch per level per snapshot.
- `Decimal(str(x))` conversions in the money accrual are individually costly and
  land in the innermost loop.

None of these scale to ~47 M snapshots in 15 minutes. Vectorized columnar
kernels do.

## The path (what Phase-3 should build)

**Principle (§19.4):** *book/trade data feeds the fill model via an Arrow-native
columnar path; dataclasses stay for orders/fills/events only — they are
low-volume.* Keep the honest per-fill semantics; change only the data plane under
them.

1. **Keep depth in Arrow end-to-end.** Do not materialize the book to Python
   per snapshot. Represent bids/asks as Arrow list columns (already the store
   shape, `DEPTH_SCHEMA`) and operate on them with `pyarrow.compute` /
   NumPy-zero-copy kernels.
2. **Vectorize the book walk.** The Tier-A fill (walk levels until an order size
   is met, VWAP the crossed levels, compute queue-ahead) is a cumulative-sum +
   searchsorted over each snapshot's level array — expressible as columnar
   kernels (`cumulative_sum`, `partition_by`/`list_flatten` + offsets) rather than
   a Python inner loop. Fills for a batch of snapshots compute in one pass.
3. **Batch by bar/window, not by row.** Process a chunk of snapshots (e.g., one
   bar's worth, or an Arrow `RecordBatch`) at a time so the interpreter dispatch
   cost amortizes over thousands of rows.
4. **Money accumulation stays exact but batched.** Accrue fees/PnL over a batch
   with scaled-integer / `Decimal` reduction at batch boundaries (§5 numeric
   policy preserved: sorted `(ts, event_seq)` accumulation order, |Δ|≤1e-9 — the
   Rust parity contract, §19.4). Do not `Decimal(str(...))` per level; convert
   once per batch.
5. **Orders/fills/events remain dataclasses.** They are low-volume (one per
   actual fill, not per snapshot), so object overhead there is immaterial and the
   event-log/replay design (§2.10) is untouched.
6. **Down-sample is a visible knob, never silent (§19.4 Tier-A policy).** If a
   run opts into e.g. 1 s depth down-sampling, it is a named run parameter shown
   on the tearsheet — not a hidden throughput crutch.

## Validation when built

- Re-run `scripts/spike_throughput.py` (extended with the Arrow-native loop, or a
  Phase-3 equivalent) against the §19.4 budget; Tier-A must land ≤ 15 min with
  RSS ≤ 4 GiB.
- Prefer a **real** HL depth day for the re-run (archive credentials +
  requester-pays, or a vendored dense day) so the per-snapshot cost reflects real
  book-width variance — see the spike report's carry-forward.
- The Arrow path must produce **bit-identical** fills to a reference scalar
  implementation on a fixture (the parity contract is the guardrail against a
  vectorization that silently changes fill semantics).
