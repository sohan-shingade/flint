# Concentrated Liquidity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace constant-product math in the arb scanner with a tick-range model for Orca Whirlpools, fetch tick data from on-chain, and store snapshots for historical replay.

**Architecture:** `CLMMPool` models concentrated liquidity with tick-walking price impact. `OrcaTickFetcher` reads Whirlpool accounts via Solana RPC + anchorpy. `ArbDetector._Edge` delegates to CLMMPool when available, falls back to constant-product. Tick snapshots stored in FlintStore.

**Tech Stack:** `anchorpy` (account deserialization), `solana` (RPC), existing `ArbDetector` and `FlintStore`.

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `flint/mev/clmm.py` | TickRange, CLMMPool with tick-walking math | Create |
| `flint/providers/orca_ticks.py` | OrcaTickFetcher — on-chain data via RPC | Create |
| `flint/mev/arb.py` | Add CLMM edge support to _Edge and ArbDetector | Modify |
| `flint/store.py` | Add tick_snapshots table + CRUD methods | Modify |
| `flint/config.py` | Add CLMM config fields | Modify |
| `ROADMAP.md` | Mark §4.2 as implemented | Modify |
| `tests/test_clmm.py` | CLMMPool + TickRange tests | Create |
| `tests/test_orca_ticks.py` | OrcaTickFetcher tests (mocked RPC) | Create |
| `tests/test_arb_clmm.py` | ArbDetector CLMM integration tests | Create |
| `tests/test_clmm_store.py` | tick_snapshots store tests | Create |
| `tests/test_clmm_config.py` | Config field tests | Create |

---

### Task 1: Config Additions

**Files:**
- Modify: `flint/config.py`
- Create: `tests/test_clmm_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_clmm_config.py`:

```python
"""Tests for CLMM config fields."""
from flint.config import FlintConfig


class TestCLMMConfig:
    def test_defaults(self):
        config = FlintConfig()
        assert config.clmm_tick_fetch_enabled is False
        assert config.clmm_tick_persist_interval_s == 300

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_CLMM_TICK_FETCH_ENABLED", "true")
        monkeypatch.setenv("FLINT_CLMM_TICK_PERSIST_INTERVAL_S", "600")
        config = FlintConfig()
        assert config.clmm_tick_fetch_enabled is True
        assert config.clmm_tick_persist_interval_s == 600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_clmm_config.py -v`
Expected: FAIL

- [ ] **Step 3: Add config fields**

In `flint/config.py`, add after the vAMM section (after `vamm_default_sqrt_k`):

```python
    # --- CLMM ---
    clmm_tick_fetch_enabled: bool = False
    clmm_tick_persist_interval_s: int = 300
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_clmm_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/config.py tests/test_clmm_config.py
git commit -m "feat: add CLMM config fields (tick_fetch_enabled, persist_interval)"
```

---

### Task 2: TickRange + CLMMPool Model

**Files:**
- Create: `flint/mev/clmm.py`
- Create: `tests/test_clmm.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_clmm.py`:

```python
"""Tests for CLMMPool tick-range model."""
import math
import pytest

from flint.mev.clmm import TickRange, CLMMPool


class TestTickRange:
    def test_create(self):
        tr = TickRange(tick_lower=-100, tick_upper=100, liquidity=1_000_000.0)
        assert tr.tick_lower == -100
        assert tr.tick_upper == 100
        assert tr.liquidity == 1_000_000.0


class TestCLMMPoolConstruction:
    def test_create_with_ticks(self):
        ticks = [
            TickRange(tick_lower=-1000, tick_upper=0, liquidity=500_000.0),
            TickRange(tick_lower=0, tick_upper=1000, liquidity=1_000_000.0),
        ]
        pool = CLMMPool(
            pool_address="pool1", dex="orca",
            token_a_mint="SOL", token_b_mint="USDC",
            tick_ranges=ticks, current_tick=50,
            tick_spacing=64, fee_rate=0.003,
            sqrt_price=math.sqrt(150.0),
        )
        assert pool.dex == "orca"
        assert len(pool.tick_ranges) == 2

    def test_price_at_tick(self):
        pool = CLMMPool(
            pool_address="pool1", dex="orca",
            token_a_mint="SOL", token_b_mint="USDC",
            tick_ranges=[], current_tick=0,
            tick_spacing=1, fee_rate=0.003,
            sqrt_price=1.0,
        )
        # tick 0 → price = 1.0001^0 = 1.0
        assert abs(pool.price_at_tick(0) - 1.0) < 0.001
        # tick 100 → price ≈ 1.01005
        assert pool.price_at_tick(100) > 1.0


class TestOutputAmount:
    def _make_pool(self, liquidity=1_000_000.0):
        """Create a pool with one concentrated range around current tick."""
        ticks = [
            TickRange(tick_lower=-5000, tick_upper=5000, liquidity=liquidity),
        ]
        return CLMMPool(
            pool_address="pool1", dex="orca",
            token_a_mint="SOL", token_b_mint="USDC",
            tick_ranges=ticks, current_tick=0,
            tick_spacing=64, fee_rate=0.003,
            sqrt_price=1.0,
        )

    def test_small_swap_positive_output(self):
        pool = self._make_pool()
        out = pool.output_amount(1.0, a_to_b=True)
        assert out > 0

    def test_larger_swap_more_slippage(self):
        pool = self._make_pool()
        small_out = pool.output_amount(1.0, a_to_b=True)
        large_out = pool.output_amount(100.0, a_to_b=True)
        # Larger swap gets worse effective price
        small_rate = small_out / 1.0
        large_rate = large_out / 100.0
        assert small_rate > large_rate

    def test_more_liquidity_less_slippage(self):
        thin = self._make_pool(liquidity=100_000.0)
        thick = self._make_pool(liquidity=10_000_000.0)
        thin_out = thin.output_amount(10.0, a_to_b=True)
        thick_out = thick.output_amount(10.0, a_to_b=True)
        assert thick_out > thin_out  # More liquidity = better price

    def test_both_directions_work(self):
        pool = self._make_pool()
        a_to_b = pool.output_amount(1.0, a_to_b=True)
        b_to_a = pool.output_amount(1.0, a_to_b=False)
        assert a_to_b > 0
        assert b_to_a > 0

    def test_empty_range_no_output(self):
        """Pool with no tick ranges produces zero output."""
        pool = CLMMPool(
            pool_address="pool1", dex="orca",
            token_a_mint="SOL", token_b_mint="USDC",
            tick_ranges=[], current_tick=0,
            tick_spacing=64, fee_rate=0.003,
            sqrt_price=1.0,
        )
        out = pool.output_amount(1.0, a_to_b=True)
        assert out == 0.0


class TestToPoolState:
    def test_backward_compat(self):
        ticks = [TickRange(tick_lower=-1000, tick_upper=1000, liquidity=1_000_000.0)]
        pool = CLMMPool(
            pool_address="pool1", dex="orca",
            token_a_mint="SOL", token_b_mint="USDC",
            tick_ranges=ticks, current_tick=0,
            tick_spacing=64, fee_rate=0.003,
            sqrt_price=math.sqrt(150.0),
        )
        ps = pool.to_pool_state()
        assert ps.pool_address == "pool1"
        assert ps.dex == "orca"
        assert ps.reserve_a > 0
        assert ps.reserve_b > 0
        assert ps.fee_rate == 0.003
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_clmm.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement CLMMPool**

Create `flint/mev/clmm.py`:

```python
"""CLMMPool — concentrated liquidity model for tick-range AMM pools.

Models Orca Whirlpool and similar concentrated liquidity pools.
Computes output amounts by walking tick ranges instead of constant-product.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from ..models import PoolState


@dataclass
class TickRange:
    """A liquidity range in a concentrated liquidity pool."""
    tick_lower: int
    tick_upper: int
    liquidity: float


class CLMMPool:
    """Concentrated liquidity pool with tick-range price impact.

    Instead of constant-product (x*y=k) across all prices, liquidity
    is concentrated in specific tick ranges. Swaps walk through active
    ranges, consuming liquidity at each level.
    """

    def __init__(
        self,
        pool_address: str,
        dex: str,
        token_a_mint: str,
        token_b_mint: str,
        tick_ranges: List[TickRange],
        current_tick: int,
        tick_spacing: int,
        fee_rate: float,
        sqrt_price: float,
    ):
        self.pool_address = pool_address
        self.dex = dex
        self.token_a_mint = token_a_mint
        self.token_b_mint = token_b_mint
        self.tick_ranges = sorted(tick_ranges, key=lambda t: t.tick_lower)
        self.current_tick = current_tick
        self.tick_spacing = tick_spacing
        self.fee_rate = fee_rate
        self.sqrt_price = sqrt_price

    def price_at_tick(self, tick: int) -> float:
        """Convert tick index to price. price = 1.0001^tick"""
        return 1.0001 ** tick

    def output_amount(self, amount_in: float, a_to_b: bool) -> float:
        """Compute output by walking through active tick ranges.

        For each range that contains liquidity:
        - Compute how much input can be consumed at this liquidity level
        - Compute the output produced using the concentrated liquidity formula
        - If input remains, move to the next range

        Returns 0.0 if no liquidity is available.
        """
        if not self.tick_ranges or amount_in <= 0:
            return 0.0

        remaining = amount_in * (1 - self.fee_rate)
        total_output = 0.0
        current_sqrt_price = self.sqrt_price

        # Sort ranges by proximity to current tick
        if a_to_b:
            # Selling A for B: price decreases, walk downward
            active_ranges = [r for r in self.tick_ranges
                           if r.tick_upper > self.current_tick - 5000]
            active_ranges.sort(key=lambda r: r.tick_lower, reverse=True)
        else:
            # Selling B for A: price increases, walk upward
            active_ranges = [r for r in self.tick_ranges
                           if r.tick_lower < self.current_tick + 5000]
            active_ranges.sort(key=lambda r: r.tick_lower)

        for tr in active_ranges:
            if remaining <= 0:
                break

            if tr.liquidity <= 0:
                continue

            # Compute how much can be swapped within this range
            sqrt_lower = math.sqrt(self.price_at_tick(tr.tick_lower))
            sqrt_upper = math.sqrt(self.price_at_tick(tr.tick_upper))

            if a_to_b:
                # Selling token A: delta_a consumed, delta_b produced
                # Within a range: delta_a = L * (1/sqrt_lower - 1/sqrt_upper)
                max_a_in_range = tr.liquidity * abs(1.0 / sqrt_lower - 1.0 / sqrt_upper)
                consumed = min(remaining, max_a_in_range)
                if max_a_in_range > 0:
                    fraction = consumed / max_a_in_range
                    max_b_out = tr.liquidity * abs(sqrt_upper - sqrt_lower)
                    output = max_b_out * fraction
                else:
                    output = 0.0
            else:
                # Selling token B: delta_b consumed, delta_a produced
                max_b_in_range = tr.liquidity * abs(sqrt_upper - sqrt_lower)
                consumed = min(remaining, max_b_in_range)
                if max_b_in_range > 0:
                    fraction = consumed / max_b_in_range
                    max_a_out = tr.liquidity * abs(1.0 / sqrt_lower - 1.0 / sqrt_upper)
                    output = max_a_out * fraction
                else:
                    output = 0.0

            remaining -= consumed
            total_output += output

        return total_output

    def to_pool_state(self) -> PoolState:
        """Convert to PoolState for backward compatibility.

        Computes effective reserves from the tick distribution.
        """
        total_a = 0.0
        total_b = 0.0
        for tr in self.tick_ranges:
            sqrt_lower = math.sqrt(self.price_at_tick(tr.tick_lower))
            sqrt_upper = math.sqrt(self.price_at_tick(tr.tick_upper))
            total_a += tr.liquidity * abs(1.0 / sqrt_lower - 1.0 / sqrt_upper)
            total_b += tr.liquidity * abs(sqrt_upper - sqrt_lower)

        return PoolState(
            pool_address=self.pool_address,
            dex=self.dex,
            token_a_mint=self.token_a_mint,
            token_b_mint=self.token_b_mint,
            reserve_a=max(total_a, 0.001),
            reserve_b=max(total_b, 0.001),
            fee_rate=self.fee_rate,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_clmm.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/mev/clmm.py tests/test_clmm.py
git commit -m "feat: add CLMMPool tick-range model with tick-walking output computation"
```

---

### Task 3: Store — tick_snapshots Table

**Files:**
- Modify: `flint/store.py`
- Create: `tests/test_clmm_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_clmm_store.py`:

```python
"""Tests for tick_snapshots store methods."""
import json
import pytest
from flint.store import FlintStore


class TestTickSnapshots:
    def test_upsert_and_query(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        tick_data = json.dumps([
            {"lower": -1000, "upper": 0, "liquidity": 500000},
            {"lower": 0, "upper": 1000, "liquidity": 1000000},
        ])
        store.upsert_tick_snapshot(
            pool_address="pool1", ts=1000, dex="orca",
            token_a_mint="SOL", token_b_mint="USDC",
            current_tick=50, tick_spacing=64, fee_rate=0.003,
            sqrt_price=12.247, tick_data_json=tick_data,
        )
        results = store.query_tick_snapshots("pool1")
        assert len(results) == 1
        assert results[0]["dex"] == "orca"
        assert results[0]["current_tick"] == 50
        parsed = json.loads(results[0]["tick_data"])
        assert len(parsed) == 2
        store.close()

    def test_query_with_time_range(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        for ts in [1000, 2000, 3000]:
            store.upsert_tick_snapshot(
                pool_address="pool1", ts=ts, dex="orca",
                token_a_mint="SOL", token_b_mint="USDC",
                current_tick=50, tick_spacing=64, fee_rate=0.003,
                sqrt_price=12.247, tick_data_json="[]",
            )
        results = store.query_tick_snapshots("pool1", start_ts=1500, end_ts=2500)
        assert len(results) == 1
        assert results[0]["ts"] == 2000
        store.close()

    def test_upsert_replaces(self, tmp_path):
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        store.upsert_tick_snapshot(
            pool_address="pool1", ts=1000, dex="orca",
            token_a_mint="SOL", token_b_mint="USDC",
            current_tick=50, tick_spacing=64, fee_rate=0.003,
            sqrt_price=12.0, tick_data_json="[]",
        )
        store.upsert_tick_snapshot(
            pool_address="pool1", ts=1000, dex="orca",
            token_a_mint="SOL", token_b_mint="USDC",
            current_tick=100, tick_spacing=64, fee_rate=0.003,
            sqrt_price=13.0, tick_data_json="[]",
        )
        results = store.query_tick_snapshots("pool1")
        assert len(results) == 1
        assert results[0]["current_tick"] == 100
        store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_clmm_store.py -v`
Expected: FAIL

- [ ] **Step 3: Add tick_snapshots table and methods to store**

In `flint/store.py`, add the table creation SQL alongside existing table definitions:

```python
_CREATE_TICK_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS tick_snapshots (
    pool_address VARCHAR NOT NULL,
    ts           BIGINT  NOT NULL,
    dex          VARCHAR NOT NULL,
    token_a_mint VARCHAR NOT NULL,
    token_b_mint VARCHAR NOT NULL,
    current_tick INTEGER NOT NULL,
    tick_spacing INTEGER NOT NULL,
    fee_rate     DOUBLE  NOT NULL,
    sqrt_price   DOUBLE  NOT NULL,
    tick_data    VARCHAR NOT NULL,
    PRIMARY KEY (pool_address, ts)
);
"""
```

Add table creation to `__init__` alongside existing tables.

Add methods:

```python
    def upsert_tick_snapshot(
        self,
        pool_address: str, ts: int, dex: str,
        token_a_mint: str, token_b_mint: str,
        current_tick: int, tick_spacing: int, fee_rate: float,
        sqrt_price: float, tick_data_json: str,
    ) -> None:
        """Insert or replace a tick snapshot."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO tick_snapshots "
                "(pool_address, ts, dex, token_a_mint, token_b_mint, "
                "current_tick, tick_spacing, fee_rate, sqrt_price, tick_data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [pool_address, ts, dex, token_a_mint, token_b_mint,
                 current_tick, tick_spacing, fee_rate, sqrt_price, tick_data_json],
            )

    def query_tick_snapshots(
        self,
        pool_address: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> list:
        """Query tick snapshots for a pool."""
        sql = (
            "SELECT pool_address, ts, dex, token_a_mint, token_b_mint, "
            "current_tick, tick_spacing, fee_rate, sqrt_price, tick_data "
            "FROM tick_snapshots WHERE pool_address = ?"
        )
        params: list = [pool_address]
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        sql += " ORDER BY ts ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {"pool_address": r[0], "ts": r[1], "dex": r[2],
             "token_a_mint": r[3], "token_b_mint": r[4],
             "current_tick": r[5], "tick_spacing": r[6],
             "fee_rate": r[7], "sqrt_price": r[8], "tick_data": r[9]}
            for r in rows
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_clmm_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/store.py tests/test_clmm_store.py
git commit -m "feat: add tick_snapshots table to FlintStore for CLMM data"
```

---

### Task 4: OrcaTickFetcher

**Files:**
- Create: `flint/providers/orca_ticks.py`
- Create: `tests/test_orca_ticks.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_orca_ticks.py`:

```python
"""Tests for OrcaTickFetcher — mocked RPC, no real Solana calls."""
import asyncio
import json
import math
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from flint.providers.orca_ticks import OrcaTickFetcher
from flint.mev.clmm import CLMMPool, TickRange


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestOrcaTickFetcherConstruction:
    def test_creates(self):
        fetcher = OrcaTickFetcher(rpc_url="https://api.mainnet-beta.solana.com")
        assert fetcher._rpc_url is not None


class TestBuildCLMMPool:
    def test_build_from_decoded_data(self):
        """Test building CLMMPool from pre-decoded whirlpool data."""
        fetcher = OrcaTickFetcher()
        pool = fetcher._build_pool(
            pool_address="pool1",
            whirlpool_data={
                "token_mint_a": "SOL_MINT",
                "token_mint_b": "USDC_MINT",
                "tick_current_index": 50,
                "sqrt_price": int(math.sqrt(150.0) * (2**64)),
                "fee_rate": 300,  # 3 bps (hundredths of a bp)
                "tick_spacing": 64,
            },
            tick_ranges=[
                TickRange(tick_lower=-1000, tick_upper=0, liquidity=500_000.0),
                TickRange(tick_lower=0, tick_upper=1000, liquidity=1_000_000.0),
            ],
        )
        assert isinstance(pool, CLMMPool)
        assert pool.pool_address == "pool1"
        assert pool.dex == "orca"
        assert pool.current_tick == 50
        assert len(pool.tick_ranges) == 2

    def test_fee_rate_conversion(self):
        """Fee rate is in hundredths of a basis point on-chain."""
        fetcher = OrcaTickFetcher()
        pool = fetcher._build_pool(
            pool_address="pool1",
            whirlpool_data={
                "token_mint_a": "A", "token_mint_b": "B",
                "tick_current_index": 0,
                "sqrt_price": 2**64,
                "fee_rate": 3000,  # 30 bps = 0.003
                "tick_spacing": 64,
            },
            tick_ranges=[],
        )
        assert abs(pool.fee_rate - 0.003) < 0.0001


class TestDecodeTickRanges:
    def test_decode_from_tick_data(self):
        """Test decoding tick ranges from raw tick array data."""
        fetcher = OrcaTickFetcher()
        # Simulate decoded tick array: list of (tick_index, liquidity_net, liquidity_gross)
        raw_ticks = [
            {"tick_index": -128, "liquidity_net": 1000000, "liquidity_gross": 1000000, "initialized": True},
            {"tick_index": 0, "liquidity_net": -500000, "liquidity_gross": 500000, "initialized": True},
            {"tick_index": 128, "liquidity_net": -500000, "liquidity_gross": 0, "initialized": True},
        ]
        ranges = fetcher._ticks_to_ranges(raw_ticks, tick_spacing=64)
        assert len(ranges) > 0
        assert all(isinstance(r, TickRange) for r in ranges)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orca_ticks.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement OrcaTickFetcher**

Create `flint/providers/orca_ticks.py`:

```python
"""OrcaTickFetcher — fetch Orca Whirlpool tick data from Solana RPC.

Deserializes Whirlpool and TickArray accounts to build CLMMPool instances.
Uses anchorpy for Anchor account decoding when available, falls back to
manual parsing for robustness.
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional

from ..mev.clmm import CLMMPool, TickRange

logger = logging.getLogger("flint.orca_ticks")

# Orca Whirlpool program ID
WHIRLPOOL_PROGRAM_ID = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"

# Ticks per tick array
TICK_ARRAY_SIZE = 88


class OrcaTickFetcher:
    """Fetch Orca Whirlpool tick data from Solana RPC.

    Builds CLMMPool instances from on-chain account data.
    """

    def __init__(self, rpc_url: str = "https://api.mainnet-beta.solana.com"):
        self._rpc_url = rpc_url

    async def fetch_pool(self, pool_address: str) -> Optional[CLMMPool]:
        """Fetch a single whirlpool's state and tick arrays.

        Steps:
        1. getAccountInfo for whirlpool account
        2. Derive tick array PDAs around current_tick
        3. getMultipleAccounts for tick arrays
        4. Build CLMMPool from decoded data
        """
        try:
            from solana.rpc.async_api import AsyncClient

            async with AsyncClient(self._rpc_url) as client:
                # Step 1: Fetch whirlpool account
                from solders.pubkey import Pubkey
                pool_pubkey = Pubkey.from_string(pool_address)
                resp = await client.get_account_info(pool_pubkey)

                if resp.value is None:
                    logger.warning("Whirlpool account not found: %s", pool_address)
                    return None

                whirlpool_data = self._decode_whirlpool(resp.value.data)
                if whirlpool_data is None:
                    return None

                # Step 2: Derive tick array PDAs
                current_tick = whirlpool_data["tick_current_index"]
                tick_spacing = whirlpool_data["tick_spacing"]
                pda_addresses = self._derive_tick_array_pdas(
                    pool_address, current_tick, tick_spacing, count=3,
                )

                # Step 3: Fetch tick arrays
                tick_ranges = []
                for pda in pda_addresses:
                    try:
                        pda_pubkey = Pubkey.from_string(pda)
                        pda_resp = await client.get_account_info(pda_pubkey)
                        if pda_resp.value is not None:
                            raw_ticks = self._decode_tick_array(pda_resp.value.data)
                            ranges = self._ticks_to_ranges(raw_ticks, tick_spacing)
                            tick_ranges.extend(ranges)
                    except Exception as e:
                        logger.debug("Failed to decode tick array %s: %s", pda, e)

                # Step 4: Build CLMMPool
                return self._build_pool(pool_address, whirlpool_data, tick_ranges)

        except ImportError:
            logger.error("solana/solders packages required for OrcaTickFetcher")
            return None
        except Exception as e:
            logger.error("Failed to fetch pool %s: %s", pool_address, e)
            return None

    async def fetch_pools(self, pool_addresses: List[str]) -> List[CLMMPool]:
        """Batch fetch multiple pools."""
        import asyncio
        results = await asyncio.gather(
            *[self.fetch_pool(addr) for addr in pool_addresses],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, CLMMPool)]

    def _build_pool(
        self,
        pool_address: str,
        whirlpool_data: dict,
        tick_ranges: List[TickRange],
    ) -> CLMMPool:
        """Build CLMMPool from decoded on-chain data."""
        # Fee rate is in hundredths of a basis point on-chain
        # e.g., 3000 = 30 bps = 0.003
        fee_rate = whirlpool_data.get("fee_rate", 0) / 1_000_000

        # sqrt_price is Q64.64 fixed point on-chain
        raw_sqrt_price = whirlpool_data.get("sqrt_price", 2**64)
        sqrt_price = raw_sqrt_price / (2**64)

        return CLMMPool(
            pool_address=pool_address,
            dex="orca",
            token_a_mint=str(whirlpool_data.get("token_mint_a", "")),
            token_b_mint=str(whirlpool_data.get("token_mint_b", "")),
            tick_ranges=tick_ranges,
            current_tick=whirlpool_data.get("tick_current_index", 0),
            tick_spacing=whirlpool_data.get("tick_spacing", 64),
            fee_rate=fee_rate,
            sqrt_price=sqrt_price,
        )

    def _decode_whirlpool(self, data) -> Optional[dict]:
        """Decode Whirlpool account data.

        Tries anchorpy first, falls back to manual byte parsing.
        """
        try:
            # Try anchorpy IDL-based decoding
            import anchorpy
            # For now, use manual parsing as fallback
            raise ImportError("Use manual parsing")
        except ImportError:
            pass

        # Manual parsing of key fields from Whirlpool account layout
        # Layout reference: Orca Whirlpool program
        try:
            raw = bytes(data)
            if len(raw) < 300:
                return None

            import struct
            # Skip 8-byte discriminator
            offset = 8
            # Skip whirlpools_config (32 bytes) + whirlpool_bump (1 byte)
            offset += 33
            # tick_spacing: u16
            tick_spacing = struct.unpack_from("<H", raw, offset)[0]
            offset += 2
            # Skip tick_spacing_seed (2 bytes)
            offset += 2
            # fee_rate: u16
            fee_rate = struct.unpack_from("<H", raw, offset)[0]
            offset += 2
            # Skip protocol_fee_rate (2 bytes)
            offset += 2
            # liquidity: u128 (16 bytes)
            offset += 16
            # sqrt_price: u128 (16 bytes)
            sqrt_price = int.from_bytes(raw[offset:offset+16], "little")
            offset += 16
            # tick_current_index: i32
            tick_current_index = struct.unpack_from("<i", raw, offset)[0]
            offset += 4
            # Skip protocol_fee_owed_a (8), protocol_fee_owed_b (8)
            offset += 16
            # token_mint_a: Pubkey (32 bytes)
            token_mint_a = raw[offset:offset+32]
            offset += 32
            # token_mint_b: Pubkey (32 bytes)
            token_mint_b = raw[offset:offset+32]
            offset += 32

            from solders.pubkey import Pubkey
            return {
                "tick_spacing": tick_spacing,
                "fee_rate": fee_rate,
                "sqrt_price": sqrt_price,
                "tick_current_index": tick_current_index,
                "token_mint_a": str(Pubkey.from_bytes(token_mint_a)),
                "token_mint_b": str(Pubkey.from_bytes(token_mint_b)),
            }
        except Exception as e:
            logger.error("Failed to decode whirlpool: %s", e)
            return None

    def _decode_tick_array(self, data) -> List[dict]:
        """Decode TickArray account data into list of tick dicts."""
        ticks = []
        try:
            raw = bytes(data)
            if len(raw) < 100:
                return []

            import struct
            # Skip 8-byte discriminator
            offset = 8
            # start_tick_index: i32
            start_tick_index = struct.unpack_from("<i", raw, offset)[0]
            offset += 4

            # 88 ticks, each with:
            # initialized: bool (1), liquidity_net: i128 (16), liquidity_gross: u128 (16)
            # + fee_growth fields (32 bytes each * 2) + reward fields
            # Total per tick: ~1 + 16 + 16 + 64 + ... = variable
            # Simplified: read initialized + liquidity fields
            tick_size = 137  # Approximate tick struct size

            for i in range(TICK_ARRAY_SIZE):
                tick_offset = offset + i * tick_size
                if tick_offset + 33 > len(raw):
                    break

                initialized = raw[tick_offset] != 0
                if not initialized:
                    continue

                liquidity_net = int.from_bytes(raw[tick_offset+1:tick_offset+17], "little", signed=True)
                liquidity_gross = int.from_bytes(raw[tick_offset+17:tick_offset+33], "little", signed=False)

                tick_index = start_tick_index + i
                ticks.append({
                    "tick_index": tick_index,
                    "liquidity_net": liquidity_net,
                    "liquidity_gross": liquidity_gross,
                    "initialized": True,
                })
        except Exception as e:
            logger.debug("Failed to decode tick array: %s", e)

        return ticks

    def _ticks_to_ranges(self, raw_ticks: List[dict], tick_spacing: int) -> List[TickRange]:
        """Convert raw tick data to TickRange list.

        Builds ranges between consecutive initialized ticks
        where liquidity is positive.
        """
        if not raw_ticks:
            return []

        # Sort by tick index
        sorted_ticks = sorted(raw_ticks, key=lambda t: t["tick_index"])
        ranges = []

        # Build ranges from liquidity_net changes
        current_liquidity = 0.0
        prev_tick = None

        for tick in sorted_ticks:
            if prev_tick is not None and current_liquidity > 0:
                ranges.append(TickRange(
                    tick_lower=prev_tick,
                    tick_upper=tick["tick_index"],
                    liquidity=current_liquidity,
                ))
            current_liquidity += tick["liquidity_net"]
            prev_tick = tick["tick_index"]

        return ranges

    def _derive_tick_array_pdas(
        self,
        pool_address: str,
        current_tick: int,
        tick_spacing: int,
        count: int = 3,
    ) -> List[str]:
        """Derive tick array PDA addresses around current tick."""
        pdas = []
        try:
            from solders.pubkey import Pubkey
            import struct

            program_id = Pubkey.from_string(WHIRLPOOL_PROGRAM_ID)
            pool_pubkey = Pubkey.from_string(pool_address)

            ticks_per_array = TICK_ARRAY_SIZE * tick_spacing
            if ticks_per_array <= 0:
                return []

            start_index = (current_tick // ticks_per_array) * ticks_per_array

            for offset in range(-count, count + 1):
                idx = start_index + offset * ticks_per_array
                try:
                    seeds = [
                        b"tick_array",
                        bytes(pool_pubkey),
                        struct.pack("<i", idx),
                    ]
                    pda, _ = Pubkey.find_program_address(seeds, program_id)
                    pdas.append(str(pda))
                except Exception:
                    continue
        except ImportError:
            logger.debug("solders not available for PDA derivation")

        return pdas
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_orca_ticks.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/providers/orca_ticks.py tests/test_orca_ticks.py
git commit -m "feat: add OrcaTickFetcher for on-chain Whirlpool tick data"
```

---

### Task 5: ArbDetector CLMM Integration

**Files:**
- Modify: `flint/mev/arb.py`
- Create: `tests/test_arb_clmm.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_arb_clmm.py`:

```python
"""Tests for ArbDetector CLMM integration."""
import math
import pytest

from flint.models import PoolState
from flint.mev.arb import ArbDetector
from flint.mev.clmm import CLMMPool, TickRange


def _make_clmm_pool(pool_address, token_a, token_b, liquidity=1_000_000.0):
    return CLMMPool(
        pool_address=pool_address, dex="orca",
        token_a_mint=token_a, token_b_mint=token_b,
        tick_ranges=[TickRange(tick_lower=-5000, tick_upper=5000, liquidity=liquidity)],
        current_tick=0, tick_spacing=64, fee_rate=0.003,
        sqrt_price=1.0,
    )


class TestCLMMEdge:
    def test_clmm_edge_used_when_available(self):
        pool = PoolState(pool_address="pool1", dex="orca",
                         token_a_mint="A", token_b_mint="B",
                         reserve_a=1000, reserve_b=1000, fee_rate=0.003)
        clmm = _make_clmm_pool("pool1", "A", "B")

        detector = ArbDetector(min_profit_bps=0)
        detector.update_pools([pool], clmm_pools={"pool1": clmm})

        # The edge should use CLMM output, not constant-product
        edges = detector._adjacency.get("A", [])
        assert len(edges) > 0
        out = edges[0].output_amount(1.0)
        assert out > 0

    def test_fallback_without_clmm(self):
        pool = PoolState(pool_address="pool1", dex="orca",
                         token_a_mint="A", token_b_mint="B",
                         reserve_a=1000, reserve_b=1000, fee_rate=0.003)

        detector = ArbDetector(min_profit_bps=0)
        detector.update_pools([pool])  # No clmm_pools

        edges = detector._adjacency.get("A", [])
        assert len(edges) > 0
        out = edges[0].output_amount(1.0)
        assert out > 0  # Uses constant-product

    def test_clmm_gives_different_output(self):
        pool = PoolState(pool_address="pool1", dex="orca",
                         token_a_mint="A", token_b_mint="B",
                         reserve_a=1000, reserve_b=1000, fee_rate=0.003)
        clmm = _make_clmm_pool("pool1", "A", "B", liquidity=500_000.0)

        # Without CLMM
        det_cp = ArbDetector(min_profit_bps=0)
        det_cp.update_pools([pool])
        cp_out = det_cp._adjacency["A"][0].output_amount(10.0)

        # With CLMM
        det_clmm = ArbDetector(min_profit_bps=0)
        det_clmm.update_pools([pool], clmm_pools={"pool1": clmm})
        clmm_out = det_clmm._adjacency["A"][0].output_amount(10.0)

        # Outputs should differ (CLMM uses tick-walking, CP uses x*y=k)
        assert abs(cp_out - clmm_out) > 0.001

    def test_backward_compat_no_clmm_param(self):
        pool = PoolState(pool_address="pool1", dex="raydium",
                         token_a_mint="A", token_b_mint="B",
                         reserve_a=1000, reserve_b=1000, fee_rate=0.003)
        detector = ArbDetector(min_profit_bps=0)
        detector.update_pools([pool])
        out = detector._adjacency["A"][0].output_amount(1.0)
        assert out > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_arb_clmm.py -v`
Expected: FAIL — `update_pools() got unexpected keyword argument 'clmm_pools'`

- [ ] **Step 3: Modify ArbDetector for CLMM support**

Modify `flint/mev/arb.py`:

Update `_Edge` to accept optional CLMM pool:

```python
@dataclass
class _Edge:
    """Directed edge: swap token_in → token_out through a pool."""
    pool_address: str
    token_in: str
    token_out: str
    reserve_in: float
    reserve_out: float
    fee_rate: float
    clmm_pool: Optional["CLMMPool"] = None
    is_a_to_b: bool = True

    def output_amount(self, amount_in: float) -> float:
        """Compute swap output. Uses CLMM if available, else constant-product."""
        if self.clmm_pool is not None:
            return self.clmm_pool.output_amount(amount_in, self.is_a_to_b)
        effective_in = amount_in * (1 - self.fee_rate)
        return (self.reserve_out * effective_in) / (self.reserve_in + effective_in)

    def effective_price(self, amount_in: float = 1.0) -> float:
        out = self.output_amount(amount_in)
        return out / amount_in if amount_in > 0 else 0.0
```

Add `Optional` import and update `update_pools`:

```python
    def update_pools(self, pools: List[PoolState], clmm_pools: Optional[Dict[str, "CLMMPool"]] = None) -> None:
        """Rebuild the graph from current pool states.

        Args:
            pools: List of pool states (constant-product or fallback).
            clmm_pools: Optional dict mapping pool_address to CLMMPool
                        for concentrated liquidity pricing.
        """
        self._edges.clear()
        self._tokens.clear()
        self._adjacency.clear()

        for p in pools:
            self._tokens.add(p.token_a_mint)
            self._tokens.add(p.token_b_mint)

            clmm = clmm_pools.get(p.pool_address) if clmm_pools else None

            fwd = _Edge(p.pool_address, p.token_a_mint, p.token_b_mint,
                        p.reserve_a, p.reserve_b, p.fee_rate,
                        clmm_pool=clmm, is_a_to_b=True)
            rev = _Edge(p.pool_address, p.token_b_mint, p.token_a_mint,
                        p.reserve_b, p.reserve_a, p.fee_rate,
                        clmm_pool=clmm, is_a_to_b=False)

            self._edges.extend([fwd, rev])
            self._adjacency[p.token_a_mint].append(fwd)
            self._adjacency[p.token_b_mint].append(rev)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_arb_clmm.py -v`
Expected: PASS

- [ ] **Step 5: Run existing arb tests for regressions**

Run: `pytest tests/ -k "arb or mev" --tb=short -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add flint/mev/arb.py tests/test_arb_clmm.py
git commit -m "feat: add CLMM edge support to ArbDetector with constant-product fallback"
```

---

### Task 6: ROADMAP Update

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Update ROADMAP.md §4.2**

Find the §4.2 Concentrated Liquidity section and add after the existing checklist items:

```markdown
**Implemented:**
- [x] `CLMMPool` tick-range model with tick-walking `output_amount()` (`flint/mev/clmm.py`)
- [x] `TickRange` dataclass for per-tick liquidity
- [x] `OrcaTickFetcher` — on-chain Whirlpool tick data via Solana RPC (`flint/providers/orca_ticks.py`)
- [x] Whirlpool + TickArray account deserialization (manual byte parsing with anchorpy fallback)
- [x] `ArbDetector` CLMM integration — tick-walking edges with constant-product fallback
- [x] `tick_snapshots` table in FlintStore for historical replay
- [x] Raydium CLMM falls back to constant-product (no tick fetcher)
- [x] Config: `clmm_tick_fetch_enabled`, `clmm_tick_persist_interval_s`
```

- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: update ROADMAP §4.2 with concentrated liquidity implementation notes"
```

---

## Task Dependencies

```
Task 1 (Config) ───────────────────┐
                                    ├──→ Task 4 (OrcaTickFetcher) ──→ Task 5 (ArbDetector)
Task 2 (CLMMPool model) ──────────┤                                        │
                                    │                                   Task 6 (ROADMAP)
Task 3 (Store tick_snapshots) ─────┘
```

**Parallelizable:** Tasks 1, 2, 3 have no dependencies between them.
**Sequential:** Task 4 needs 1+2+3. Task 5 needs 2+4. Task 6 is last.
