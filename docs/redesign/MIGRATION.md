# Migrating from legacy Flint (v1.x) — data + strategies (§19.6)

The greenfield rewrite deletes old *code*, never old *data*. A legacy Flint
DuckDB holds recorded market data — candles, funding, locally-recorded L2 depth,
and open interest — that is **now-or-never**: nobody can re-record yesterday's
order book. This note covers the one-shot data importer and the strategy-API
porting path.

## Data: the one-shot importer

`flint/data/migrate.py` → `migrate_legacy_duckdb(legacy_path, sink)`.

- **The legacy file is opened strictly read-only** (`duckdb.connect(..., read_only=True)`).
  The importer never writes a byte back to it. It stays a safe, verifiable
  original — keep it until you have diffed the report against the new store and
  trust the copy.
- **It writes only through a sink** (`MigrationSink` — any cache tier, i.e.
  `DurableCacheSource` for the durable Parquet lake, or `InMemoryCacheSource` in
  tests). The importer never touches storage directly; it honors the ports rule.
- **It is idempotent.** The sink upserts keyed `(venue, market, ts)`, so
  re-running the import over an already-imported store is a no-op.
- **It returns a `MigrationReport`** — per-kind row counts and a run-metadata
  inventory — so you can verify source vs destination. `report.format()` renders
  a human-readable verification report.

### Usage

```python
from flint.data import migrate_legacy_duckdb
from flint.data.store import DurableCacheSource

sink = DurableCacheSource("~/.flint/lake")
report = migrate_legacy_duckdb("~/.flint/legacy/flint.duckdb", sink)
print(report.format())
```

### Schema translation (legacy → new)

The importer maps each legacy table to the canonical Arrow schema the engine
reads today. Fabricates nothing (D26): dropped fields have no new-schema home;
absent fields are null or an explicit `"legacy"` provenance sentinel — never an
invented number.

| Legacy table | New `Kind` | Mapping notes |
|---|---|---|
| `candles` | `CANDLES` | 1:1 — ts/OHLCV/market/`resolution_s`/venue carried verbatim. |
| `venue_funding_rates` | `FUNDING` | `rate_hourly` carried verbatim. `interval_s=0` (native cadence unknown), `price_basis="legacy"`, `rate_type="legacy"` — provenance sentinels, not guesses. Legacy `mark_price`/`index_price` **dropped** (no home in `FUNDING_SCHEMA`). |
| `orderbook_snapshots` | `DEPTH` | Four parallel arrays (`bid_prices`/`bid_sizes`/`ask_prices`/`ask_sizes`) re-zipped into best-first `[[px, sz], …]` level pairs, truncated to the shorter side so a malformed row never invents a level. |
| `open_interest` | `OI` | `oi = long_oi` (aggregate OI is one side of the book — every long has a short). `short_oi` **dropped**. `mark_price`/`index_price`/`funding_hourly` were never recorded on the legacy OI row → **null** (not `0.0`: a zero price would be a fabrication). |

Venues are preserved exactly as stored; the importer does not filter or rename
them. Legacy rows recorded under other venues survive the copy as recorded facts
— preserving data is not the same as adding a venue to the supported set.

### Run metadata is inventoried, not moved (yet)

Run metadata (`journal_equity`, `strategies`) has no market-data home in the new
store, and the Run Library that will own it is **Phase 6** (`research/runlib.py`).
The importer therefore *counts* these tables (they appear in `report.format()`
under "Run metadata inventoried") but does not move them. Run-metadata import
lands with the Run Library.

## Strategies: porting the old API → `Strategy` / `Signal`

Pre-1.0 breaking changes are allowed with a porting note (§19.6). The legacy
strategy API is replaced by the `strategy/` surface (§8.1):

- Old callback-style strategies become a `Strategy` subclass emitting a
  list-of-`Signal` (or calling `ctx.submit_order`); indicators read from the
  read-only `ctx` value object, never from global state.
- Look-ahead is now caught by the linter (`research/lookahead.py`); a strategy
  that reached forward in legacy code will be flagged on port.
- User strategy code runs OS-isolated in the sandbox (D25); imports outside the
  allowlist are surfaced as line-precise user errors, not silent failures.

The strategy surface (base class, `Signal`, ctx, templates) lands in Phase 5; a
worked old→new port example belongs with that phase and is a carry-forward here.
