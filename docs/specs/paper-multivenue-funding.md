# Paper trading — multi-venue funding correctness

**Status**: shipped end-to-end via the A → C ladder.
- **Option A** (per-venue funding query in the engine tick loop) — v1.4.2
- **Option C** (full broker re-architecture as `PaperContext`, positions
  keyed by `(venue, market)` so same-market opposite-leg arb works) —
  v1.5.0 (D-2.1.d)
- **Option B** skipped — the structural fix in v1.5.0 made it redundant.

**Trigger**: audit during the price-ticker discussion. Confirmed that
single-venue paper trading applies funding correctly per-venue, but
multi-venue paper sessions silently underreport funding on every leg
that isn't on the broker's primary venue.

---

## What works today (single-venue path)

`flint/paper/engine.py:622–644` — once per tick, the funding loop:

1. Pulls new rows from `venue_funding_rates` filtered by
   `(session.broker.venue, session.market)` via
   `store.query_venue_funding(...)`.
2. For each row, calls `broker.apply_funding(market, rate_hourly, mark_price)`,
   which signs the payment by side, debits cash, and increments
   `total_funding`.
3. Persists the payment to `paper_funding_payments` for resume +
   journal.

A Drift paper session pulls only Drift funding. An HL paper session
pulls only HL funding. **Single-venue case is correct.**

## Where it breaks

Two compounding gaps:

### Gap 1: funding loop only queries the primary venue

```python
funding = self.store.query_venue_funding(
    session.broker.venue, session.market,   # ← only the primary
    last_funding_ts + 1, now_ts,
)
```

If a session holds a long on Drift and a short on Hyperliquid (the
exact shape of `funding_dislocation_arb`), only the Drift leg's
funding payment gets booked. The HL leg accrues no funding on the
paper books — but in real life it would, and that funding is the
reason the strategy makes money.

### Gap 2: PaperBroker positions dict keyed by market only

`flint/execution/paper_broker.py:322` — `pos = self.positions.get(market)`,
and `:325` opens new positions as `self.positions[market] = {...}`.
**No (venue, market) compound key.** A `Side.LONG` fill on
`SOL-PERP` from venue `drift` and a `Side.LONG` fill on `SOL-PERP`
from venue `hyperliquid` collide on the same dict key — the second
gets DCA-merged into the first.

Even if Gap 1 were fixed, Gap 2 means there's no way to apply
different funding to the two legs because the broker doesn't track
two legs in the first place.

## Why this matters

Multi-venue paper is a wedge feature, not an edge case:

- `flint/strategy/funding_dislocation_arb.py` — the cross-venue
  funding-arb strategy that's literally the point of having
  Drift + HL together.
- `funding_arb_v2`, `funding_arb` — also cross-venue.
- Any future basis trade that hedges a Drift perp with a CCXT spot.

Backtest + Rust engine already handle this correctly — the
`BacktestContext` migration (D-2.1.b) keys positions by
`(venue, market)` via `PositionManager`, and the funding loop in
`flint/execution/backtest_context.py` iterates
`self._pm.items()` by `(venue, market)` and applies per-venue rates
through `self._cm.debit(payment, venue)`. Paper just hasn't caught
up.

## Failure mode in numbers

Concrete example. Funding-dislocation-arb session on
`SOL-PERP`, equal-size legs, 8h funding cycle:

| Venue | Rate (per 8h) | Notional | Payment |
|---|---|---|---|
| Drift | -0.001 (long pays nothing, receives) | $5_000 | +$5 (received) |
| HL | +0.0015 (short receives) | $5_000 | +$7.50 (received) |

Expected total funding income for this cycle: **+$12.50**.
Today's paper code: pulls Drift rates only, applies to both legs
combined ($10k notional × -0.001) → **+$10**. The strategy looks
20% less profitable than reality. Over a month of 8h cycles, that
mis-attribution adds up to several hundred bps of fake drag.

## Available context: the backtest path already solved this

`BacktestContext.apply_funding` (post-D-2.1.b):

```python
for (venue, m), pos in self._pm.items():
    if m != market:
        continue
    payment = ... (rate, notional, side)
    self._cm.debit(payment, venue)   # routes per-venue
    pos.funding_paid += payment
    self._cm.add_funding(payment)
```

Per-venue dispatch + per-venue allocator debit + per-position
attribution. Paper needs the same shape.

## Design options

### Option A: minimal patch — multi-query loop

In `paper/engine.py:622`, iterate the broker's distinct venues
instead of using `session.broker.venue`:

```python
venues_in_book = {pos.get("venue", session.broker.venue)
                  for pos in session.broker.positions.values()}
for v in venues_in_book:
    funding = self.store.query_venue_funding(v, session.market, ...)
    for fr in funding:
        broker.apply_funding(session.market, fr["rate_hourly"], mp, venue=v)
```

Plus `apply_funding` gets a `venue=` kwarg and only debits positions
matching that venue.

**Pros**: ~30 LOC, no broker rewrite, fixes Gap 1.

**Cons**: doesn't fix Gap 2. If a strategy ever opens both Drift-long
and HL-short on the same market, the second fill still collides on
the dict key. The patch helps multi-market multi-venue (e.g. Drift
SOL + HL BTC) but not same-market opposite-leg arb.

### Option B: re-key broker positions by (venue, market)

Match what `BacktestContext._pm` already does. `PaperBroker.positions`
becomes `Dict[Tuple[str, str], dict]`; every read site (8 call sites,
checked in `flint/paper/engine.py`, `flint/api/routes/paper.py`,
session-store persistence) updates to compound keys.

**Pros**: structural fix. Same-market arb actually works. Closes the
last remaining "backtest is right but paper is off" delta on
cross-venue strategies — which is the whole reason the wedge needs
a paper layer.

**Cons**:
- Touches more code than the price-ticker spec did.
- Schema change in `paper_positions` table — needs a migration step
  for sessions persisted before the fix.
- Property aliases on the legacy market-keyed access pattern (some
  tests do `broker.positions["SOL-PERP"]`) need a back-compat shim
  during the transition, like the seven-manager extraction did.

### Option C: paper engine stops using `PaperBroker`, switches to `BacktestContext`

The structural answer. After D-2.1.b shipped, `BacktestContext` is
already a clean orchestrator with per-(venue, market) positions, the
right funding loop, and the orchestrator gauntlet. `LiveContext` was
originally the paper wrapper because backtest was a god class —
that's no longer true. Could collapse paper-engine onto
`BacktestContext` with a "live candle source" instead of a candle
list.

**Pros**: deletes `PaperBroker` and `LiveContext` for paper sessions.
One execution model end-to-end. Free correctness everywhere because
the backtest path is the canonical path.

**Cons**:
- D-2.1.d in the deferred backlog already lists this. Estimated 1w.
- Touches every paper test file. Real risk of regressions.
- Paper engine has live-specific machinery (replay-then-live
  transition, RiskGuard polling, equity history persistence to
  `paper_equity_history`) that doesn't have a clean home on
  BacktestContext today.

## Recommendation

**Ship Option A first, then Option B as a follow-on, then Option C as
the deferred D-2.1.d work.**

1. **Now (~30 LOC + 4 tests)**: Option A. The multi-market case is
   common — strategies routinely hold positions across venues without
   opening offsetting same-market legs. Fixes the funding-dislocation-arb
   miss for the strategies that don't open SOL on both venues at once
   (basis trade, multi-asset cross-venue). Cost is small enough that
   shipping it ahead of the structural fix is fine.

2. **Next (~150 LOC + 10 tests)**: Option B. Re-key the broker. Adds
   a migration for `paper_positions` (DB column already has `venue`
   — just need a `(venue, market)` PK instead of `market` PK). Tests
   land in `tests/test_paper_multi_venue.py`. Validates against a
   funding-dislocation-arb paper run.

3. **Deferred (the existing D-2.1.d slot)**: Option C. Once Option B
   ships, the pressure to do C drops a lot — paper is correct on the
   wedge. C becomes a code-quality refactor rather than a correctness
   fix.

## Concrete diff (Option A scope)

Three edits:

```python
# flint/execution/paper_broker.py
def apply_funding(self, market: str, rate: float, mark_price: float,
                  venue: Optional[str] = None) -> float:
    pos = self.positions.get(market)
    if pos is None:
        return 0.0
    if venue is not None and pos.get("venue") != venue:
        return 0.0  # rate is for a different venue; skip
    # ...rest unchanged
```

```python
# flint/paper/engine.py:622–644
if session.broker.positions:
    last_funding_ts = getattr(session, '_last_funding_ts', 0)
    now_ts = int(time.time())
    venues_in_book = {pos.get("venue", session.broker.venue)
                      for pos in session.broker.positions.values()}
    latest_ts = last_funding_ts
    for v in venues_in_book:
        try:
            funding = self.store.query_venue_funding(
                v, session.market, last_funding_ts + 1, now_ts,
            )
            for fr in funding:
                mp = fr["mark_price"] or candle.close
                payment = session.broker.apply_funding(
                    session.market, fr["rate_hourly"], mp, venue=v,
                )
                if payment != 0 and ss:
                    pos = session.broker.positions.get(session.market)
                    ss.save_funding_payment(
                        session.session_id, fr["ts"], session.market,
                        fr["rate_hourly"], payment,
                        pos["size"] if pos else 0, mp, venue=v,
                    )
                if fr["ts"] > latest_ts:
                    latest_ts = fr["ts"]
        except Exception as e:
            logger.debug("Funding application error %s/%s: %s",
                         session.session_id, v, e)
    session._last_funding_ts = latest_ts
```

```python
# flint/paper/session_store.py — add `venue` column + arg to save_funding_payment
```

Tests:

- `tests/test_paper_multi_venue.py::test_funding_applies_per_venue`:
  open positions on two venues, push funding rates for both into the
  store, assert `apply_funding` debits each leg with its own rate
  and skips cross-venue ones.
- `tests/test_paper_multi_venue.py::test_funding_loop_queries_each_venue`:
  pin that the engine's tick loop iterates `venues_in_book` and
  calls `query_venue_funding` once per distinct venue.
- One regression test (single-venue path stays unchanged).
- One persistence test (resumed multi-venue session restores
  `total_funding` correctly).

## What this does and doesn't fix

**Fixes (Option A)**:
- Multi-market multi-venue paper — Drift SOL + HL BTC now gets both
  venues' funding applied to the right legs.
- Per-venue funding payment attribution in `paper_funding_payments`
  table → journal page shows correct per-leg funding.
- Single-venue path unchanged.

**Doesn't fix (deferred to B)**:
- Same-market opposite-leg arb (Drift long SOL + HL short SOL on the
  same broker) — the position-key collision still exists.
- Per-venue allocator cash debit (Option A's `apply_funding` debits
  the global cash; allocator-aware debit comes with B).

**Doesn't fix (deferred to C / D-2.1.d)**:
- Paper engine's structural drift from BacktestContext. Two execution
  paths to maintain instead of one.

## Decision deferred to next loop

Not committed. Awaiting confirmation that the A → B → C ladder is
the right shape before writing the actual `apply_funding` venue arg
+ tick-loop multi-query + tests.
