# `tests/parity/` — Nautilus vs frozen legacy goldens (§19.4)

The byte-exact parity suite. Through N9 it diffed the **legacy bar engine** against
the **Nautilus bar lane** live, engine-vs-engine. In **N10 the legacy engine was
deleted**, so parity is now Nautilus-vs-**frozen-golden**: each scenario's legacy
event log was snapshotted once, at commit `300e9b2`, into `goldens/<name>.json`, and
every test runs *only* the Nautilus lane and asserts it reproduces the golden
byte-for-byte (§19.4b), zero tolerance.

## The goldens are frozen artifacts

`goldens/*.json` were recorded by `record_goldens.py` from the legacy engine and can
**never be regenerated from current history** — the source engine is gone.
Regenerating one requires checking out pre-N10 history (`git checkout 300e9b2 --`
the engine), re-running the recorder, and copying the file back. A golden breaking
is therefore a real signal: the Nautilus lane's output changed (a `nautilus_trader`
churn, a Flint fill/funding/liquidation change), and that change must be understood,
not papered over by re-recording.

`golden_store.py` is the single serialization contract both the recorder and the
tests use: `canonical()` maps event rows to their JSON-native form (`Decimal` → its
exact string, `StrEnum` → its value, tuple → list) via one `json` round-trip, so a
live Nautilus log and a JSON-loaded golden compare with plain `==`, byte-for-byte.

## Why layer (a) is gone

The old harness ran two §19.4 layers: input-parity (a *localizer*: did the same
numbers reach the shared pure funding/liquidation functions?) then the full byte
diff (the contract). Input-parity only made sense across two live engines — it told
you whether a divergence was a math bug or an ordering bug. With one engine held to
a frozen log there is no second reach to compare, so only the full byte diff remains
(`golden_store.assert_matches_golden`).

## The goldens

`test_goldens.py::GOLDENS` — §6.4 funding worked example, predicted/final
divergence, multi-settlement flip, §6.5 liquidation worked example,
funding-saves-position ordering, cross-pool cascade, backstop forfeiture,
intrabar-ambiguous, T+1 fill timing, size_usd materialization, oracle-band clip,
zero-volume reject. Plus two beyond the hand-authored set:

- **cross-market** (`test_cross_market_*`) — two markets sharing bar timestamps.
  `EngineFeed` canonicalizes candles to `(ts, market)` at the seam, so both feed
  orders (SOL-first / ETH-first) reproduce the one `cross_market_shared_ts` golden.
- **real fragment** (`test_real_fragment_golden`) — one recorded HL day from the
  committed truncated real Tardis SOL 2026-06-01 fixtures: candles aggregated from
  real trade prints + real predicted funding (settlement absent; the fragment ships
  no finals — D26 honesty note in `real_fragment.py`).

The same recorder also froze the per-phase goldens the `tests/test_nautilus_*.py`
gates assert against (funding / liquidation / bar-lane / skeleton) and the
`tests/test_engine_seam.py` seam + §6.7 warm-start scenarios.

## Running

```bash
PYTHONPATH=. .venv/bin/pytest tests/parity/ -v      # requires the `nautilus` extra
```

Tests `importorskip("nautilus_trader")`, so they **skip cleanly without the extra**
and **run when it is present**. Marked `parity` (registered in `pyproject.toml`).
