# Pin Proof-Notebook Data Fixtures

**Who this is for:** maintainers closing out [D-1.5 (DEFERRED.md)][1] — the
`EXPECTED_CANDLE_HASH = "PIN-ME"` placeholder in each notebook under
`notebooks/`.

Proof notebooks (`notebooks/funding_arb.py`, `basis_trade.py`,
`momentum_breakout.py`) hash their input candles at run time and compare
to a pinned constant. Once real fixture data is committed, that constant
stops saying `"PIN-ME"` and starts asserting the bytes you want anyone
cloning the repo to reproduce against.

[1]: ../../DEFERRED.md

---

## The choice: commit fixture vs external artifact store

Flint ships free market data from public APIs, so "just re-download" is
cheap. That argues against committing multi-MB parquet blobs into git.
Two supported workflows:

### A. Lightweight — hash only, re-download each run (default)

1. Notebook calls `flint.store.FlintStore().query_candles(...)` on
   whatever the user has locally.
2. `_hash_candles` in the notebook computes SHA-256 of the candle list.
3. If `EXPECTED_CANDLE_HASH` is `"PIN-ME"`, the notebook skips the
   equality check and just prints the observed hash.
4. When a contributor wants to pin a specific snapshot, they:
   a. Run the notebook once.
   b. Copy the printed sha256 into `EXPECTED_CANDLE_HASH`.
   c. Commit + open PR.

This is what ships today. Downside: "reproducible" means "reproducible
after everyone downloads the same 30-day window from Drift's current
API state," which can drift on late-arriving corrections.

### B. Heavy — commit a canonical parquet (strict reproducibility)

For releases where bit-identical reruns matter (audit, external
evaluation, firm pitch):

1. Export the canonical window to parquet:
   ```python
   from flint.store import FlintStore
   import pyarrow as pa, pyarrow.parquet as pq

   store = FlintStore()
   candles = store.query_candles("SOL-PERP", 3600,
                                  start_ts=..., end_ts=...)
   table = pa.table({
       "ts_epoch_s": [c.ts for c in candles],
       "open": [c.open for c in candles],
       "high": [c.high for c in candles],
       "low": [c.low for c in candles],
       "close": [c.close for c in candles],
       "volume": [c.volume for c in candles],
       "market": [c.market for c in candles],
       "resolution_s": [c.resolution_s for c in candles],
   })
   pq.write_table(table, "artifacts/proof-data/SOL-PERP-2026-04.parquet")
   ```
2. Commit under `artifacts/proof-data/` (whole directory is NOT
   gitignored — unlike `artifacts/pit/` and `artifacts/parity/` which
   are regenerated per-run).
3. Notebook loads from parquet via `CustomParquetProvider` instead of
   `FlintStore.query_candles`.
4. Paste the new hash into `EXPECTED_CANDLE_HASH`.

Downside: git bloat. ~30 days of hourly SOL-PERP candles = roughly
40 KB parquet — tolerable. Multi-year or higher-resolution fixtures
bloat fast.

### Recommendation

- **Default:** workflow A. Snapshots drift, contributors rehash.
- **Release cuts:** workflow B for SOL-PERP + ETH-PERP 30-day windows
  only. Snapshot once per quarter.

---

## Commands

Get current hash of the local store for a given range:

```python
import hashlib
from flint.store import FlintStore

store = FlintStore()
candles = store.query_candles("SOL-PERP", 3600,
                              start_ts=1700000000,
                              end_ts=1702592000)

h = hashlib.sha256()
for c in candles:
    h.update(f"{c.ts},{c.open},{c.high},{c.low},{c.close},{c.volume},{c.market}\n".encode())
print(h.hexdigest())
```

Paste that into the notebook's `EXPECTED_CANDLE_HASH` constant.

---

## CI behavior

Today: the notebooks honor `EXPECTED_CANDLE_HASH = "PIN-ME"` by
printing the observed hash + skipping the assert. That's deliberate —
a contributor opening a PR without pinned fixtures still gets a useful
report, just without the reproducibility gate.

Once fixtures are pinned, the notebooks will exit non-zero on drift.
Use that in CI jobs like `parity.yml` (Phase 5.3) to catch data-source
regressions — candles mysteriously changing between the pin and the
re-run is the exact scenario the pin was designed to catch.

---

## See also

- [DEFERRED.md][1] — D-1.5 origin + closure criteria
- `docs/specs/phase-1-trust-correctness.md#15-proof-notebooks` — spec
- `notebooks/README.md` — notebook conventions
- `docs/reference/custom-data-schema.md` — Parquet fixture format
