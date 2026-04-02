# Concentrated Liquidity for Arb Detection — Design Spec

> Sub-project 4.2 of Phase 4 (ROADMAP.md §4.2)
> Date: 2026-04-01

## Overview

Replace constant-product math in the arb scanner with a tick-range model for Orca Whirlpools. Fetch tick-level liquidity data from on-chain accounts via Solana RPC, store snapshots in FlintStore, and use the tick-walking model for more accurate arb profit estimates. Raydium CLMM falls back to constant-product approximation.

### Scope

**In scope:**
- `CLMMPool` model with tick-range liquidity and tick-walking price impact
- `TickRange` dataclass for per-tick liquidity
- `OrcaTickFetcher` — fetches Whirlpool state + tick arrays via Solana RPC using anchorpy
- `tick_snapshots` table in FlintStore for historical replay
- `ArbDetector` integration — CLMM edges when available, constant-product fallback
- Config additions for CLMM parameters

**Out of scope:**
- Raydium CLMM tick fetching (falls back to constant-product)
- Real-time streaming of tick updates (fetch-and-store only)
- Arb execution / bundle submission (MEV Phase)

---

## 1. CLMMPool Model

**New file:** `flint/mev/clmm.py`

### Data Structures

```python
@dataclass
class TickRange:
    """A liquidity range in a concentrated liquidity pool."""
    tick_lower: int
    tick_upper: int
    liquidity: float         # Liquidity units active in this range
```

### CLMMPool

```python
class CLMMPool:
    """Concentrated liquidity pool with tick-range price impact."""

    def __init__(
        self,
        pool_address: str,
        dex: str,               # "orca" or "raydium"
        token_a_mint: str,
        token_b_mint: str,
        tick_ranges: List[TickRange],
        current_tick: int,
        tick_spacing: int,
        fee_rate: float,
        sqrt_price: float,      # Current sqrt(price)
    ): ...

    def output_amount(self, amount_in: float, a_to_b: bool) -> float:
        """Compute output by walking active tick ranges.

        For each tick range crossed:
        1. Compute how much input is consumed at the current liquidity level
        2. Compute the output produced
        3. If amount remaining, move to next tick range
        4. If no liquidity in range, price jumps (zero output for that segment)

        This captures:
        - Concentrated liquidity zones (less slippage near current price)
        - Empty ranges (discontinuous price jumps)
        - More accurate than constant-product for large or off-center swaps
        """

    def price_at_tick(self, tick: int) -> float:
        """Convert tick index to price. price = 1.0001^tick"""

    def to_pool_state(self) -> "PoolState":
        """Convert to PoolState for backward compatibility.

        Computes effective reserves from the tick distribution
        so existing constant-product code still works.
        """
```

### Tick-Walking Math

The core algorithm for `output_amount`:

```
Given: amount_in, direction (a_to_b or b_to_a), current_tick, sqrt_price

1. Find the active tick range (the one containing current_tick)
2. Compute how much can be swapped within this range:
   - delta = amount consumed before hitting the range boundary
   - output = liquidity-weighted price * delta
3. If amount_in fully consumed: return total output
4. If not: move to next tick range, update sqrt_price, repeat
5. If no liquidity in next range: skip (price jumps, no output)
6. Apply fee: output *= (1 - fee_rate)
```

This is the same algorithm used by Uniswap v3 / Orca Whirlpool on-chain, simplified for off-chain estimation.

---

## 2. On-Chain Data Fetching

**New file:** `flint/providers/orca_ticks.py`

### OrcaTickFetcher

```python
class OrcaTickFetcher:
    """Fetch Orca Whirlpool tick data from Solana RPC.

    Uses anchorpy to deserialize Whirlpool and TickArray accounts.
    """

    def __init__(
        self,
        rpc_url: str = "https://api.mainnet-beta.solana.com",
    ): ...

    async def fetch_pool(self, pool_address: str) -> CLMMPool:
        """Fetch a single whirlpool's state and tick arrays.

        Steps:
        1. getAccountInfo(pool_address) → Whirlpool account
           - Decode: current_tick, sqrt_price, tick_spacing, fee_rate,
                     token_mint_a, token_mint_b
        2. Derive tick array PDAs for ±3 arrays around current_tick
           - PDA = findProgramAddress([b"tick_array", pool, start_tick_index])
        3. getMultipleAccounts(tick_array_pdas) → TickArray accounts
           - Decode: array of 88 ticks, each with liquidity_net, liquidity_gross
        4. Build TickRange list from initialized ticks
        5. Return CLMMPool
        """

    async def fetch_pools(self, pool_addresses: List[str]) -> List[CLMMPool]:
        """Batch fetch multiple pools with concurrent RPC calls."""

    def _derive_tick_array_pda(self, pool_address: str, start_index: int) -> str:
        """Derive the PDA for a tick array account."""

    def _decode_whirlpool(self, data: bytes) -> dict:
        """Decode Whirlpool account data using anchorpy IDL."""

    def _decode_tick_array(self, data: bytes) -> List[TickRange]:
        """Decode TickArray account data into TickRange list."""
```

### Whirlpool Account Layout (key fields)

From Orca's Anchor IDL:
- `tick_current_index: i32` — current tick
- `sqrt_price: u128` — current sqrt price (Q64.64)
- `fee_rate: u16` — fee in hundredths of a basis point
- `tick_spacing: u16` — tick spacing (1, 8, 64, 128)
- `token_mint_a: Pubkey`
- `token_mint_b: Pubkey`

### TickArray Account Layout

Each tick array holds 88 ticks. Key fields per tick:
- `initialized: bool`
- `liquidity_net: i128` — net liquidity change when crossing this tick
- `liquidity_gross: u128` — total liquidity referencing this tick

We build `TickRange` entries from consecutive initialized ticks where `liquidity_gross > 0`.

---

## 3. ArbDetector Integration

**Modify:** `flint/mev/arb.py`

### _Edge Changes

Add optional `clmm_pool` to `_Edge`:

```python
@dataclass
class _Edge:
    pool_address: str
    token_in: str
    token_out: str
    reserve_in: float
    reserve_out: float
    fee_rate: float
    clmm_pool: Optional[CLMMPool] = None
    is_a_to_b: bool = True

    def output_amount(self, amount_in: float) -> float:
        if self.clmm_pool:
            return self.clmm_pool.output_amount(amount_in, self.is_a_to_b)
        # Existing constant-product fallback
        effective_in = amount_in * (1 - self.fee_rate)
        return (self.reserve_out * effective_in) / (self.reserve_in + effective_in)
```

### ArbDetector Changes

`update_pools()` accepts optional CLMM data:

```python
def update_pools(self, pools: List[PoolState], clmm_pools: Optional[Dict[str, CLMMPool]] = None):
    # When building edges, check if pool_address has CLMM data
    clmm = clmm_pools.get(p.pool_address) if clmm_pools else None
    fwd = _Edge(..., clmm_pool=clmm, is_a_to_b=True)
    rev = _Edge(..., clmm_pool=clmm, is_a_to_b=False)
```

**Backward compatible:** When `clmm_pools` is None (default), all edges use constant-product — existing behavior unchanged.

---

## 4. Store — tick_snapshots Table

**Modify:** `flint/store.py`

```sql
CREATE TABLE IF NOT EXISTS tick_snapshots (
    pool_address VARCHAR NOT NULL,
    ts BIGINT NOT NULL,
    dex VARCHAR NOT NULL,
    token_a_mint VARCHAR NOT NULL,
    token_b_mint VARCHAR NOT NULL,
    current_tick INTEGER NOT NULL,
    tick_spacing INTEGER NOT NULL,
    fee_rate DOUBLE NOT NULL,
    sqrt_price DOUBLE NOT NULL,
    tick_data VARCHAR NOT NULL,     -- JSON: [{"lower": int, "upper": int, "liquidity": float}, ...]
    PRIMARY KEY (pool_address, ts)
);
```

Methods:
- `upsert_tick_snapshot(pool_address, ts, dex, token_a_mint, token_b_mint, current_tick, tick_spacing, fee_rate, sqrt_price, tick_data_json)`
- `query_tick_snapshots(pool_address, start_ts=None, end_ts=None) -> list`

---

## 5. Config Additions

**Modify:** `flint/config.py`

```python
# --- CLMM ---
clmm_tick_fetch_enabled: bool = False
clmm_tick_persist_interval_s: int = 300
```

---

## 6. Dependencies

- `anchorpy` — already installed via driftpy for Anchor account deserialization
- `solana` — already installed for RPC access
- No new dependencies

---

## 7. ROADMAP Update

After implementation, update ROADMAP.md §4.2 with "Implemented" checkboxes.

---

## 8. Testing Strategy

All tests use mocked RPC responses — no real Solana calls.

- **CLMMPool**: Test output_amount with synthetic tick distributions (3-5 tick ranges). Test single concentrated range gives less slippage than constant-product for small orders. Test empty range causes zero output for that segment. Test large swap crossing multiple ranges. Test to_pool_state backward compat.
- **TickRange**: Test dataclass creation.
- **OrcaTickFetcher**: Mock `getAccountInfo` and `getMultipleAccounts` responses. Test whirlpool account decoding. Test tick array decoding. Test PDA derivation. Test pool construction from decoded data.
- **ArbDetector CLMM**: Test CLMM edge produces different output than constant-product. Test fallback to constant-product when no CLMM data. Test arb routes use CLMM when available. Test backward compat (no clmm_pools param).
- **Store**: Test tick_snapshots CRUD with tmp_path DuckDB.
- **Config**: Test defaults and env overrides.
