# `tests/parity/` — legacy-vs-Nautilus parity harness (§19.4, N6)

The CI-required superset harness that byte-diffs the **legacy bar engine** against
the **Nautilus bar lane** on the §19.3 golden set. The per-phase tests
(`tests/test_nautilus_*.py`) gate each engine phase; this suite is the single
harness the **N9 default flip gates on** — *the engine default (`auto`) flips
legacy → Nautilus only after this suite has been green for a full release.*

## What it proves

Every golden runs one hand-authored feed (D26) + a real trading strategy through
both engines and asserts they produce the **same event log**. `harness.py` applies
the two §19.4 layers, in order:

- **Layer (a) — input parity** (`input_parity_rows`). The exact inputs both
  engines feed the *shared pure functions* — funding settlement args (rate,
  settled rate, cap flag, interval, price basis, oracle price, size, amount) and
  the liquidation-check inputs (mark, equity, maintenance, size, side, margin
  mode, liq/bankruptcy price). Order- and `seq`-independent (sorted by
  `(ts, kind, market)`): it answers *did the same inputs reach the same math?*,
  not *in what interleaving?* — so a divergence here is a **math/reach** bug.

- **Layer (b) — full byte diff** (`full_rows`). The whole event stream by
  `(ts, seq)`: event-kind sequence, fill decisions/prices/sizes, order
  transitions, FUNDING/LIQUIDATION payloads incl. Decimal-string amounts, per-bar
  EQUITY Decimal-strings. Exact equality, no tolerances — the shared pure modules
  ARE the single implementation (§6.0), so equality is by construction. The only
  stripped field is the `engine` name on the `run_started`/`run_finished` events.

Layer (a) runs first so a break localizes: **(a) pass + (b) fail ⇒ ordering /
fill-path divergence**, not the pure math.

## The goldens

`test_goldens.py::GOLDENS` — §6.4 funding worked example, predicted/final
divergence, multi-settlement flip, §6.5 liquidation worked example,
funding-saves-position ordering, cross-pool cascade, backstop forfeiture,
intrabar-ambiguous, T+1 fill timing, size_usd materialization, oracle-band clip,
zero-volume reject. Plus two beyond the hand-authored set:

- **cross-market** (`test_cross_market_*`) — two markets sharing bar timestamps.
  Currently an **`xfail` finding** (see below).
- **real fragment** (`test_real_fragment_golden`) — one recorded HL day from the
  committed truncated real Tardis SOL 2026-06-01 fixtures: candles aggregated from
  real trade prints + real predicted funding (settlement absent; the fragment
  ships no finals — D26 honesty note in `real_fragment.py`).

## Known finding (xfail)

`test_cross_market_shared_ts_byte_parity` is a **strict xfail**: when two markets
share bar timestamps, the legacy lane interleaves per-market EQUITY/FUNDING events
in candle-feed order while the Nautilus lane emits them in canonical
(market-name-sorted) order. `test_cross_market_divergence_is_ordering_only`
pins it down — **layer (a) passes, layer (b) fails** — so the divergence is event
ordering only, never the settlement math, and it disappears when the feed order
matches the canonical sort. Reported to main; tracked for the N9 flip. No
`flint/` source is changed here (N6 touches only `tests/parity/`).

## Running

```bash
PYTHONPATH=. .venv/bin/pytest tests/parity/ -v      # requires the `nautilus` extra
```

Tests `importorskip("nautilus_trader")`, so they **skip cleanly without the
extra** and **run (are not skippable) when it is present**. Marked `parity`
(registered in `pyproject.toml`).
