# Per-Venue Fill Pipelines — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single generic FillPipeline with venue-specific fill models that simulate each venue's actual execution mechanics — Drift's 3-tier JIT/DLOB/vAMM, Hyperliquid's CLOB + HLP backstop, Jupiter's keeper-delay oracle fill, and CEX CLOB walks with per-venue parameters.

**Architecture:** Each venue gets a `FillModel` subclass. The backtest engine holds `Dict[str, FillModel]` mapping venue names to fill model instances. The `venue` parameter on orders determines which fill model processes them. Real orderbook data from Drift S3, Hyperliquid S3, or Tardis.dev feeds the fill models; a synthetic depth model serves as fallback.

**Tech Stack:** Python, DuckDB, httpx, existing FillModel ABC + ImpactStage infrastructure

**Spec:** `docs/superpowers/specs/2026-04-05-pyth-pricing-venue-fill-pipelines-design.md` (Sub-project 2, sections 2.1-2.9)

---

## Task 1: Orderbook Storage — Add venue column

**Files:**
- Modify: `flint/store.py` (table DDL ~line 45, query methods ~line 764)
- Test: `tests/test_venue_orderbook_store.py`

The `orderbook_snapshots` table currently has PK `(market, ts)`. For per-venue orderbooks, we need PK `(venue, market, ts)`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_venue_orderbook_store.py
"""Tests for per-venue orderbook snapshot storage."""
import os
import tempfile

from flint.models import OrderbookLevel, OrderbookSnapshot
from flint.store import FlintStore


def _make_store():
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    return FlintStore(path), path


def test_upsert_venue_orderbook():
    store, path = _make_store()
    try:
        snapshots = [
            {
                "venue": "drift",
                "market": "SOL-PERP",
                "ts": 1700000000,
                "bid_prices": [99.9, 99.8],
                "bid_sizes": [100.0, 200.0],
                "ask_prices": [100.1, 100.2],
                "ask_sizes": [150.0, 250.0],
            },
            {
                "venue": "binance",
                "market": "SOL-PERP",
                "ts": 1700000000,
                "bid_prices": [99.95, 99.85],
                "bid_sizes": [500.0, 800.0],
                "ask_prices": [100.05, 100.15],
                "ask_sizes": [600.0, 900.0],
            },
        ]
        count = store.upsert_orderbook_snapshots(snapshots)
        assert count == 2
    finally:
        store.close()
        os.unlink(path)


def test_query_nearest_orderbook():
    store, path = _make_store()
    try:
        snapshots = [
            {
                "venue": "drift",
                "market": "SOL-PERP",
                "ts": 1700000000,
                "bid_prices": [99.9],
                "bid_sizes": [100.0],
                "ask_prices": [100.1],
                "ask_sizes": [150.0],
            },
            {
                "venue": "drift",
                "market": "SOL-PERP",
                "ts": 1700000300,
                "bid_prices": [99.85],
                "bid_sizes": [110.0],
                "ask_prices": [100.15],
                "ask_sizes": [160.0],
            },
        ]
        store.upsert_orderbook_snapshots(snapshots)

        # Exact match
        book = store.query_nearest_orderbook("drift", "SOL-PERP", 1700000300)
        assert book is not None
        assert book.ts == 1700000300

        # Nearest before
        book = store.query_nearest_orderbook("drift", "SOL-PERP", 1700000200)
        assert book is not None
        assert book.ts == 1700000000

        # No data
        book = store.query_nearest_orderbook("drift", "SOL-PERP", 1699999000)
        assert book is None

        # Wrong venue
        book = store.query_nearest_orderbook("binance", "SOL-PERP", 1700000300)
        assert book is None
    finally:
        store.close()
        os.unlink(path)


def test_same_ts_different_venues():
    """Two venues at the same timestamp should not collide."""
    store, path = _make_store()
    try:
        snapshots = [
            {"venue": "drift", "market": "SOL-PERP", "ts": 1700000000,
             "bid_prices": [99.9], "bid_sizes": [100.0], "ask_prices": [100.1], "ask_sizes": [150.0]},
            {"venue": "binance", "market": "SOL-PERP", "ts": 1700000000,
             "bid_prices": [99.95], "bid_sizes": [500.0], "ask_prices": [100.05], "ask_sizes": [600.0]},
        ]
        store.upsert_orderbook_snapshots(snapshots)

        drift_book = store.query_nearest_orderbook("drift", "SOL-PERP", 1700000000)
        binance_book = store.query_nearest_orderbook("binance", "SOL-PERP", 1700000000)

        assert drift_book is not None
        assert binance_book is not None
        assert drift_book.bids[0].size == 100.0
        assert binance_book.bids[0].size == 500.0
    finally:
        store.close()
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_venue_orderbook_store.py -v`
Expected: FAIL — `query_nearest_orderbook` doesn't exist, or venue column missing

- [ ] **Step 3: Migrate orderbook_snapshots table**

In `flint/store.py`, update the `orderbook_snapshots` DDL to add a `venue` column and change the PK:

```sql
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    venue      VARCHAR  NOT NULL DEFAULT 'default',
    market     VARCHAR  NOT NULL,
    ts         BIGINT   NOT NULL,
    bid_prices DOUBLE[],
    bid_sizes  DOUBLE[],
    ask_prices DOUBLE[],
    ask_sizes  DOUBLE[],
    PRIMARY KEY (venue, market, ts)
);
```

Add migration logic in `_create_tables()` to handle existing tables without the `venue` column: check if column exists, if not ALTER TABLE to add it and recreate the PK.

- [ ] **Step 4: Update upsert_orderbook_snapshots to accept venue**

Modify the existing method to read `venue` from each snapshot dict (default `'default'`):

```python
def upsert_orderbook_snapshots(self, snapshots: list) -> int:
    if not snapshots:
        return 0
    with self._lock:
        try:
            self._conn.execute("BEGIN TRANSACTION")
            for s in snapshots:
                self._conn.execute(
                    "INSERT OR REPLACE INTO orderbook_snapshots "
                    "(venue, market, ts, bid_prices, bid_sizes, ask_prices, ask_sizes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [s.get("venue", "default"), s["market"], s["ts"],
                     s["bid_prices"], s["bid_sizes"], s["ask_prices"], s["ask_sizes"]],
                )
            self._conn.execute("COMMIT")
            return len(snapshots)
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
```

- [ ] **Step 5: Add query_nearest_orderbook method**

```python
def query_nearest_orderbook(self, venue: str, market: str, ts: int):
    """Get the orderbook snapshot closest to (at or before) a timestamp for a venue."""
    from .models import OrderbookLevel, OrderbookSnapshot
    with self._lock:
        rows = self._conn.execute(
            "SELECT venue, market, ts, bid_prices, bid_sizes, ask_prices, ask_sizes "
            "FROM orderbook_snapshots "
            "WHERE venue = ? AND market = ? AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1",
            [venue, market, ts],
        ).fetchall()
    if not rows:
        return None
    r = rows[0]
    bids = tuple(OrderbookLevel(p, s) for p, s in zip(r[3], r[4])) if r[3] else ()
    asks = tuple(OrderbookLevel(p, s) for p, s in zip(r[5], r[6])) if r[5] else ()
    return OrderbookSnapshot(market=r[1], ts=r[2], bids=bids, asks=asks)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_venue_orderbook_store.py -v`
Expected: 3 passed

- [ ] **Step 7: Run existing orderbook tests**

Run: `pytest tests/ -k "orderbook" -v`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add flint/store.py tests/test_venue_orderbook_store.py
git commit -m "feat: add venue column to orderbook_snapshots and query_nearest_orderbook"
```

---

## Task 2: Synthetic Depth Model

**Files:**
- Create: `flint/execution/synthetic_depth.py`
- Test: `tests/test_synthetic_depth.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_synthetic_depth.py
"""Tests for synthetic orderbook depth generation."""
from flint.execution.synthetic_depth import DepthProfile, generate_synthetic_book


def test_depth_profile_creation():
    dp = DepthProfile(
        bid_depth_1pct=20_000_000, ask_depth_1pct=20_000_000,
        concentration=0.7, spread_bps=0.5,
    )
    assert dp.bid_depth_1pct == 20_000_000
    assert dp.spread_bps == 0.5


def test_generate_synthetic_book():
    dp = DepthProfile(
        bid_depth_1pct=20_000_000, ask_depth_1pct=20_000_000,
        concentration=0.7, spread_bps=1.0,
    )
    book = generate_synthetic_book(mid_price=100.0, profile=dp, levels=20)

    assert len(book.bids) == 20
    assert len(book.asks) == 20
    # Bids should be below mid, asks above
    assert book.bids[0].price < 100.0
    assert book.asks[0].price > 100.0
    # Bids descending, asks ascending
    assert book.bids[0].price > book.bids[-1].price
    assert book.asks[0].price < book.asks[-1].price
    # Total depth should approximate the profile
    total_bid = sum(l.price * l.size for l in book.bids)
    assert total_bid > 0


def test_spread_applied():
    dp = DepthProfile(
        bid_depth_1pct=10_000_000, ask_depth_1pct=10_000_000,
        concentration=0.5, spread_bps=10.0,  # 10 bps = 0.1%
    )
    book = generate_synthetic_book(mid_price=100.0, profile=dp, levels=20)
    # Best bid should be ~0.05% below mid, best ask ~0.05% above
    assert book.bids[0].price < 100.0
    assert book.asks[0].price > 100.0
    spread = book.asks[0].price - book.bids[0].price
    assert spread > 0.05  # at least 5 cents for $100 price


def test_concentration_affects_distribution():
    """Higher concentration = more liquidity at top of book."""
    low_conc = DepthProfile(bid_depth_1pct=10_000_000, ask_depth_1pct=10_000_000,
                            concentration=0.3, spread_bps=1.0)
    high_conc = DepthProfile(bid_depth_1pct=10_000_000, ask_depth_1pct=10_000_000,
                             concentration=0.9, spread_bps=1.0)

    book_low = generate_synthetic_book(100.0, low_conc, 20)
    book_high = generate_synthetic_book(100.0, high_conc, 20)

    # High concentration should have more size at level 0 vs level 19
    ratio_low = book_low.asks[0].size / book_low.asks[-1].size if book_low.asks[-1].size > 0 else 1
    ratio_high = book_high.asks[0].size / book_high.asks[-1].size if book_high.asks[-1].size > 0 else 1
    assert ratio_high > ratio_low


def test_default_venue_profiles():
    from flint.execution.synthetic_depth import VENUE_PROFILES
    assert "binance" in VENUE_PROFILES
    assert "drift" in VENUE_PROFILES
    assert "hyperliquid" in VENUE_PROFILES
    assert "okx" in VENUE_PROFILES
    assert "bybit" in VENUE_PROFILES
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_synthetic_depth.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement synthetic_depth.py**

```python
# flint/execution/synthetic_depth.py
"""Synthetic orderbook depth model.

Generates realistic orderbook snapshots based on per-venue depth profiles.
Used as fallback when real orderbook data (Drift S3, Hyperliquid archive, Tardis) is unavailable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict

from ..models import OrderbookLevel, OrderbookSnapshot


@dataclass(frozen=True)
class DepthProfile:
    """Venue depth characteristics for a reference market (BTC-PERP).
    Other markets scale linearly by relative volume.
    """
    bid_depth_1pct: float    # USD liquidity within 1% of mid (bid side)
    ask_depth_1pct: float    # USD liquidity within 1% of mid (ask side)
    concentration: float     # How concentrated at top-of-book (0-1)
    spread_bps: float        # Typical bid-ask spread in basis points


# Per-venue defaults calibrated from public market data
VENUE_PROFILES: Dict[str, DepthProfile] = {
    "binance": DepthProfile(bid_depth_1pct=20_000_000, ask_depth_1pct=20_000_000,
                            concentration=0.7, spread_bps=0.5),
    "okx": DepthProfile(bid_depth_1pct=10_000_000, ask_depth_1pct=10_000_000,
                         concentration=0.6, spread_bps=1.0),
    "bybit": DepthProfile(bid_depth_1pct=8_000_000, ask_depth_1pct=8_000_000,
                           concentration=0.6, spread_bps=1.0),
    "hyperliquid": DepthProfile(bid_depth_1pct=5_000_000, ask_depth_1pct=5_000_000,
                                 concentration=0.5, spread_bps=1.5),
    "drift": DepthProfile(bid_depth_1pct=2_000_000, ask_depth_1pct=2_000_000,
                           concentration=0.4, spread_bps=3.0),
}


def generate_synthetic_book(
    mid_price: float,
    profile: DepthProfile,
    levels: int = 20,
    market: str = "",
    ts: int = 0,
) -> OrderbookSnapshot:
    """Generate a synthetic orderbook from a depth profile.

    Distributes liquidity across levels using an exponential decay curve
    controlled by the concentration parameter.
    """
    half_spread = (profile.spread_bps / 10_000) * mid_price / 2
    step = (mid_price * 0.01) / levels  # 1% range divided into N levels

    # Exponential decay: higher concentration = more weight at top of book
    decay = 1.0 + profile.concentration * 4  # concentration 0→1 maps to decay 1→5
    weights = [math.exp(-decay * i / levels) for i in range(levels)]
    total_weight = sum(weights)

    bids = []
    asks = []

    for i in range(levels):
        frac = weights[i] / total_weight

        bid_price = mid_price - half_spread - i * step
        bid_usd = profile.bid_depth_1pct * frac
        bid_size = bid_usd / bid_price if bid_price > 0 else 0
        bids.append(OrderbookLevel(price=round(bid_price, 6), size=round(bid_size, 4)))

        ask_price = mid_price + half_spread + i * step
        ask_usd = profile.ask_depth_1pct * frac
        ask_size = ask_usd / ask_price if ask_price > 0 else 0
        asks.append(OrderbookLevel(price=round(ask_price, 6), size=round(ask_size, 4)))

    return OrderbookSnapshot(
        market=market,
        ts=ts,
        bids=tuple(bids),
        asks=tuple(asks),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_synthetic_depth.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add flint/execution/synthetic_depth.py tests/test_synthetic_depth.py
git commit -m "feat: add synthetic depth model with per-venue profiles"
```

---

## Task 3: CexFillModel (Binance, OKX, Bybit)

**Files:**
- Create: `flint/execution/fill_cex.py`
- Test: `tests/test_fill_cex.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_fill_cex.py
"""Tests for CEX fill models (Binance, OKX, Bybit)."""
from flint.execution.fill_cex import BinanceFillModel, OkxFillModel, BybitFillModel
from flint.models import Candle, Order, OrderbookLevel, OrderbookSnapshot, OrderType, Side


def _make_candle(price=100.0):
    return Candle(1700000000, price, price + 1, price - 1, price, 10000.0, "SOL-PERP", 3600, "pyth")


def _make_order(side=Side.LONG, size=10.0):
    return Order(market="SOL-PERP", side=side, order_type=OrderType.MARKET, size=size,
                 price=0.0, order_id="test-1", ts=1700000000, venue="binance")


def _make_book(mid=100.0, depth_per_level=100.0, levels=5):
    bids = tuple(
        OrderbookLevel(price=mid - 0.1 * (i + 1), size=depth_per_level)
        for i in range(levels)
    )
    asks = tuple(
        OrderbookLevel(price=mid + 0.1 * (i + 1), size=depth_per_level)
        for i in range(levels)
    )
    return OrderbookSnapshot(market="SOL-PERP", ts=1700000000, bids=bids, asks=asks)


def test_binance_fill_walks_book():
    model = BinanceFillModel()
    book = _make_book(mid=100.0, depth_per_level=100.0)
    model.set_orderbook(book)
    candle = _make_candle(100.0)
    order = _make_order(side=Side.LONG, size=10.0)
    fill = model.fill_market(order, candle)
    assert fill is not None
    assert fill.price > 100.0  # ask side, should be above mid
    assert fill.size == 10.0


def test_binance_fill_uses_synthetic_when_no_book():
    model = BinanceFillModel()
    candle = _make_candle(100.0)
    order = _make_order(side=Side.LONG, size=10.0)
    fill = model.fill_market(order, candle)
    assert fill is not None
    assert fill.price > 100.0  # synthetic depth still applies slippage


def test_okx_fill_model():
    model = OkxFillModel()
    book = _make_book(mid=100.0, depth_per_level=100.0)
    model.set_orderbook(book)
    candle = _make_candle(100.0)
    order = _make_order(side=Side.LONG, size=10.0)
    fill = model.fill_market(order, candle)
    assert fill is not None
    assert fill.price > 100.0


def test_bybit_ioc_price_band_caps_fill():
    """Bybit converts market orders to IOC with a 1% price band."""
    model = BybitFillModel()
    # Book with very thin liquidity — only 5 units available within normal range
    thin_book = OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000,
        bids=tuple([OrderbookLevel(99.0, 5.0)]),
        asks=tuple([
            OrderbookLevel(100.1, 5.0),
            OrderbookLevel(102.0, 100.0),  # beyond 1% band from 100
        ]),
    )
    model.set_orderbook(thin_book)
    candle = _make_candle(100.0)
    # Order for 50 units — only 5 available within price band
    order = _make_order(side=Side.LONG, size=50.0)
    fill = model.fill_market(order, candle)
    assert fill is not None
    assert fill.size < 50.0  # partial fill due to price band


def test_short_order_walks_bid_side():
    model = BinanceFillModel()
    book = _make_book(mid=100.0, depth_per_level=100.0)
    model.set_orderbook(book)
    candle = _make_candle(100.0)
    order = _make_order(side=Side.SHORT, size=10.0)
    fill = model.fill_market(order, candle)
    assert fill is not None
    assert fill.price < 100.0  # bid side
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fill_cex.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement fill_cex.py**

```python
# flint/execution/fill_cex.py
"""CEX fill models for Binance, OKX, and Bybit.

All three use centralized CLOB matching. Shared base class handles orderbook walks.
Bybit adds IOC price band capping.
"""
from __future__ import annotations

from typing import Optional

from ..models import Candle, Fill, Order, OrderbookSnapshot, Side
from .fill_models import FillModel
from .synthetic_depth import DepthProfile, VENUE_PROFILES, generate_synthetic_book


class CexFillModel(FillModel):
    """Base CEX fill model — walks L2 orderbook or synthetic depth fallback."""

    def __init__(self, venue: str = "binance", taker_fee_bps: float = 5.0):
        self._venue = venue
        self._taker_fee_bps = taker_fee_bps
        self._current_book: Optional[OrderbookSnapshot] = None
        self._profile = VENUE_PROFILES.get(venue, VENUE_PROFILES["binance"])

    def set_orderbook(self, book: Optional[OrderbookSnapshot]) -> None:
        self._current_book = book

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        book = self._current_book
        if book is None or not book.bids or not book.asks:
            book = generate_synthetic_book(
                mid_price=candle.close, profile=self._profile,
                market=candle.market, ts=candle.ts,
            )
        levels = book.asks if order.side == Side.LONG else book.bids
        return self._walk_book(order, candle, levels)

    def _walk_book(self, order: Order, candle: Candle, levels) -> Optional[Fill]:
        remaining = order.size
        total_cost = 0.0
        filled = 0.0
        for level in levels:
            take = min(remaining, level.size)
            total_cost += take * level.price
            filled += take
            remaining -= take
            if remaining <= 0:
                break
        if filled <= 0:
            return None
        avg_price = total_cost / filled
        return Fill(
            order_id=order.order_id, market=order.market, side=order.side,
            price=avg_price, size=filled, fee=0.0, ts=candle.ts,
            venue=self._venue,
        )

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        if order.side == Side.LONG and candle.low <= order.price:
            return Fill(order_id=order.order_id, market=order.market, side=order.side,
                        price=order.price, size=order.size, fee=0.0, ts=candle.ts,
                        venue=self._venue)
        if order.side == Side.SHORT and candle.high >= order.price:
            return Fill(order_id=order.order_id, market=order.market, side=order.side,
                        price=order.price, size=order.size, fee=0.0, ts=candle.ts,
                        venue=self._venue)
        return None


class BinanceFillModel(CexFillModel):
    def __init__(self):
        super().__init__(venue="binance", taker_fee_bps=5.0)


class OkxFillModel(CexFillModel):
    def __init__(self):
        super().__init__(venue="okx", taker_fee_bps=5.0)


class BybitFillModel(CexFillModel):
    """Bybit converts market orders to IOC with a 1% price band."""

    def __init__(self):
        super().__init__(venue="bybit", taker_fee_bps=5.5)
        self._price_band_pct = 0.01  # 1% from mark

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        book = self._current_book
        if book is None or not book.bids or not book.asks:
            book = generate_synthetic_book(
                mid_price=candle.close, profile=self._profile,
                market=candle.market, ts=candle.ts,
            )
        levels = book.asks if order.side == Side.LONG else book.bids

        # Apply IOC price band
        if order.side == Side.LONG:
            max_price = candle.close * (1 + self._price_band_pct)
            capped_levels = [l for l in levels if l.price <= max_price]
        else:
            min_price = candle.close * (1 - self._price_band_pct)
            capped_levels = [l for l in levels if l.price >= min_price]

        if not capped_levels:
            return None
        return self._walk_book(order, candle, capped_levels)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fill_cex.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add flint/execution/fill_cex.py tests/test_fill_cex.py
git commit -m "feat: add CEX fill models (Binance, OKX, Bybit with IOC band)"
```

---

## Task 4: HyperliquidFillModel

**Files:**
- Create: `flint/execution/fill_hyperliquid.py`
- Test: `tests/test_fill_hyperliquid.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_fill_hyperliquid.py
"""Tests for Hyperliquid CLOB fill model with HLP backstop."""
from flint.execution.fill_hyperliquid import HyperliquidFillModel
from flint.models import Candle, Order, OrderbookLevel, OrderbookSnapshot, OrderType, Side


def _make_candle(price=100.0):
    return Candle(1700000000, price, price + 1, price - 1, price, 10000.0, "SOL-PERP", 3600, "pyth")


def _make_order(side=Side.LONG, size=10.0):
    return Order(market="SOL-PERP", side=side, order_type=OrderType.MARKET, size=size,
                 price=0.0, order_id="test-1", ts=1700000000, venue="hyperliquid")


def test_walks_orderbook():
    model = HyperliquidFillModel()
    book = OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000,
        bids=tuple([OrderbookLevel(99.9, 50.0), OrderbookLevel(99.8, 100.0)]),
        asks=tuple([OrderbookLevel(100.1, 50.0), OrderbookLevel(100.2, 100.0)]),
    )
    model.set_orderbook(book)
    fill = model.fill_market(_make_order(size=10.0), _make_candle())
    assert fill is not None
    assert fill.price == 100.1  # all filled at best ask


def test_hlp_backstop_fills_remainder():
    """When orderbook is exhausted, HLP backstop fills the rest at worse price."""
    model = HyperliquidFillModel()
    thin_book = OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000,
        bids=tuple([OrderbookLevel(99.9, 5.0)]),
        asks=tuple([OrderbookLevel(100.1, 5.0)]),
    )
    model.set_orderbook(thin_book)
    order = _make_order(side=Side.LONG, size=20.0)
    fill = model.fill_market(order, _make_candle())
    assert fill is not None
    assert fill.size == 20.0  # full fill (book + HLP)
    assert fill.price > 100.1  # HLP fills at worse price


def test_synthetic_fallback():
    model = HyperliquidFillModel()
    fill = model.fill_market(_make_order(), _make_candle())
    assert fill is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fill_hyperliquid.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement fill_hyperliquid.py**

```python
# flint/execution/fill_hyperliquid.py
"""Hyperliquid fill model — pure CLOB walk with HLP vault backstop.

The HLP (Hyperliquidity Provider) vault acts as liquidity of last resort
when the orderbook is exhausted.
"""
from __future__ import annotations

from typing import Optional

from ..models import Candle, Fill, Order, OrderbookSnapshot, Side
from .fill_models import FillModel
from .synthetic_depth import VENUE_PROFILES, generate_synthetic_book


class HyperliquidFillModel(FillModel):
    """Pure CLOB with HLP backstop."""

    def __init__(self, impact_coefficient: float = 0.005):
        self._impact_k = impact_coefficient
        self._current_book: Optional[OrderbookSnapshot] = None
        self._profile = VENUE_PROFILES["hyperliquid"]

    def set_orderbook(self, book: Optional[OrderbookSnapshot]) -> None:
        self._current_book = book

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        book = self._current_book
        if book is None or not book.bids or not book.asks:
            book = generate_synthetic_book(
                mid_price=candle.close, profile=self._profile,
                market=candle.market, ts=candle.ts,
            )

        levels = book.asks if order.side == Side.LONG else book.bids
        remaining = order.size
        total_cost = 0.0
        filled = 0.0

        # Walk orderbook
        for level in levels:
            take = min(remaining, level.size)
            total_cost += take * level.price
            filled += take
            remaining -= take
            if remaining <= 0:
                break

        # HLP backstop for remainder
        if remaining > 0:
            hlp_price = self._hlp_fill_price(remaining, candle, order.side)
            total_cost += remaining * hlp_price
            filled += remaining

        if filled <= 0:
            return None

        avg_price = total_cost / filled
        return Fill(
            order_id=order.order_id, market=order.market, side=order.side,
            price=avg_price, size=filled, fee=0.0, ts=candle.ts,
            venue="hyperliquid",
        )

    def _hlp_fill_price(self, remaining: float, candle: Candle, side: Side) -> float:
        """HLP vault fills at oracle price + impact."""
        remaining_pct = remaining / max(candle.volume, 1.0)
        impact = self._impact_k * remaining_pct
        if side == Side.LONG:
            return candle.close * (1 + impact)
        else:
            return candle.close * (1 - impact)

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        if order.side == Side.LONG and candle.low <= order.price:
            return Fill(order_id=order.order_id, market=order.market, side=order.side,
                        price=order.price, size=order.size, fee=0.0, ts=candle.ts,
                        venue="hyperliquid")
        if order.side == Side.SHORT and candle.high >= order.price:
            return Fill(order_id=order.order_id, market=order.market, side=order.side,
                        price=order.price, size=order.size, fee=0.0, ts=candle.ts,
                        venue="hyperliquid")
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fill_hyperliquid.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add flint/execution/fill_hyperliquid.py tests/test_fill_hyperliquid.py
git commit -m "feat: add Hyperliquid fill model with HLP backstop"
```

---

## Task 5: DriftFillModel (3-tier)

**Files:**
- Create: `flint/execution/fill_drift.py`
- Test: `tests/test_fill_drift.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_fill_drift.py
"""Tests for Drift 3-tier fill model: JIT auction → DLOB walk → vAMM backstop."""
from flint.execution.fill_drift import DriftFillModel
from flint.models import Candle, Order, OrderbookLevel, OrderbookSnapshot, OrderType, Side


def _make_candle(price=100.0, volume=10000.0):
    return Candle(1700000000, price, price + 1, price - 1, price, volume, "SOL-PERP", 3600, "pyth")


def _make_order(side=Side.LONG, size=10.0):
    return Order(market="SOL-PERP", side=side, order_type=OrderType.MARKET, size=size,
                 price=0.0, order_id="test-1", ts=1700000000, venue="drift")


def test_jit_fills_portion():
    """JIT auction should fill a portion of the order at a favorable price."""
    model = DriftFillModel(jit_fill_probability=1.0, seed=42)
    book = OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000,
        bids=tuple([OrderbookLevel(99.9, 100.0)]),
        asks=tuple([OrderbookLevel(100.1, 100.0)]),
    )
    model.set_orderbook(book)
    fill = model.fill_market(_make_order(size=10.0), _make_candle())
    assert fill is not None
    assert fill.size == 10.0


def test_vamm_backstop_used_when_book_thin():
    """vAMM should fill remainder when orderbook is exhausted."""
    model = DriftFillModel(jit_fill_probability=0.0, seed=42)
    empty_book = OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000, bids=(), asks=(),
    )
    model.set_orderbook(empty_book)
    fill = model.fill_market(_make_order(size=10.0), _make_candle())
    assert fill is not None
    assert fill.size == 10.0
    # vAMM should give worse price than oracle
    assert fill.price > 100.0  # for long order


def test_full_order_splits_across_tiers():
    """Order should split across JIT + DLOB + vAMM."""
    model = DriftFillModel(jit_fill_probability=0.5, seed=42)
    thin_book = OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000,
        bids=tuple([OrderbookLevel(99.9, 2.0)]),
        asks=tuple([OrderbookLevel(100.1, 2.0)]),
    )
    model.set_orderbook(thin_book)
    fill = model.fill_market(_make_order(size=10.0), _make_candle())
    assert fill is not None
    assert fill.size == 10.0


def test_short_order():
    model = DriftFillModel(seed=42)
    book = OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000,
        bids=tuple([OrderbookLevel(99.9, 100.0)]),
        asks=tuple([OrderbookLevel(100.1, 100.0)]),
    )
    model.set_orderbook(book)
    fill = model.fill_market(_make_order(side=Side.SHORT, size=5.0), _make_candle())
    assert fill is not None
    assert fill.price < 100.0


def test_deterministic_with_seed():
    model1 = DriftFillModel(seed=42)
    model2 = DriftFillModel(seed=42)
    book = OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000,
        bids=tuple([OrderbookLevel(99.9, 50.0)]),
        asks=tuple([OrderbookLevel(100.1, 50.0)]),
    )
    candle = _make_candle()
    order = _make_order(size=10.0)
    model1.set_orderbook(book)
    model2.set_orderbook(book)
    fill1 = model1.fill_market(order, candle)
    fill2 = model2.fill_market(order, candle)
    assert fill1.price == fill2.price
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fill_drift.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement fill_drift.py**

```python
# flint/execution/fill_drift.py
"""Drift Protocol 3-tier fill model.

Tier 1: JIT Dutch Auction (~60% of volume) — competitive MM fills at auction price
Tier 2: DLOB Walk (~30%) — resting limit orders, standard orderbook walk
Tier 3: vAMM Backstop (~10%) — constant-product AMM for remaining liquidity
"""
from __future__ import annotations

import random
from typing import Optional

from ..models import Candle, Fill, Order, OrderbookSnapshot, Side
from .fill_models import FillModel
from .synthetic_depth import VENUE_PROFILES, generate_synthetic_book
from .vamm import DEFAULT_SQRT_K, VammCurve


class DriftFillModel(FillModel):
    """Simulates Drift's JIT auction → DLOB → vAMM execution pipeline."""

    def __init__(
        self,
        jit_fill_probability: float = 0.6,
        auction_slots: int = 20,
        auction_price_improvement_bps: float = 2.0,
        seed: Optional[int] = None,
    ):
        self._jit_prob = jit_fill_probability
        self._auction_slots = auction_slots
        self._auction_improvement_bps = auction_price_improvement_bps
        self._rng = random.Random(seed)
        self._current_book: Optional[OrderbookSnapshot] = None
        self._profile = VENUE_PROFILES["drift"]

    def set_orderbook(self, book: Optional[OrderbookSnapshot]) -> None:
        self._current_book = book

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        remaining = order.size
        total_cost = 0.0

        # Tier 1: JIT Dutch Auction
        if self._rng.random() < self._jit_prob:
            jit_size = remaining * self._rng.uniform(0.3, 0.8)
            jit_price = self._jit_auction_price(candle, order.side)
            total_cost += jit_size * jit_price
            remaining -= jit_size

        # Tier 2: DLOB Walk
        if remaining > 0:
            book = self._current_book
            if book is None or not book.bids or not book.asks:
                book = generate_synthetic_book(
                    mid_price=candle.close, profile=self._profile,
                    market=candle.market, ts=candle.ts,
                )
            levels = book.asks if order.side == Side.LONG else book.bids
            for level in levels:
                take = min(remaining, level.size)
                total_cost += take * level.price
                remaining -= take
                if remaining <= 0:
                    break

        # Tier 3: vAMM Backstop
        if remaining > 0:
            vamm_price = self._vamm_fill_price(remaining, candle, order.side)
            total_cost += remaining * vamm_price
            remaining = 0

        filled = order.size - remaining
        if filled <= 0:
            return None

        avg_price = total_cost / filled
        return Fill(
            order_id=order.order_id, market=order.market, side=order.side,
            price=avg_price, size=filled, fee=0.0, ts=candle.ts,
            venue="drift",
        )

    def _jit_auction_price(self, candle: Candle, side: Side) -> float:
        """JIT auction fills at a price favorable to the taker (better than oracle)."""
        improvement = self._auction_improvement_bps / 10_000
        auction_progress = self._rng.uniform(0.1, 0.5)
        effective_improvement = improvement * (1 - auction_progress)
        if side == Side.LONG:
            return candle.close * (1 + effective_improvement)
        else:
            return candle.close * (1 - effective_improvement)

    def _vamm_fill_price(self, size: float, candle: Candle, side: Side) -> float:
        """vAMM constant-product backstop fill."""
        sqrt_k = DEFAULT_SQRT_K.get(candle.market, 5_000_000)
        curve = VammCurve.from_oracle_price(sqrt_k, candle.close)
        direction = "long" if side == Side.LONG else "short"
        return curve.fill_price(size, direction)

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        if order.side == Side.LONG and candle.low <= order.price:
            return Fill(order_id=order.order_id, market=order.market, side=order.side,
                        price=order.price, size=order.size, fee=0.0, ts=candle.ts,
                        venue="drift")
        if order.side == Side.SHORT and candle.high >= order.price:
            return Fill(order_id=order.order_id, market=order.market, side=order.side,
                        price=order.price, size=order.size, fee=0.0, ts=candle.ts,
                        venue="drift")
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fill_drift.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add flint/execution/fill_drift.py tests/test_fill_drift.py
git commit -m "feat: add Drift 3-tier fill model (JIT auction + DLOB + vAMM)"
```

---

## Task 6: JupiterFillModel (keeper-delay-aware)

**Files:**
- Create: `flint/execution/fill_jupiter.py`
- Test: `tests/test_fill_jupiter.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_fill_jupiter.py
"""Tests for Jupiter Perps fill model — oracle price + keeper delay + quadratic impact."""
from flint.execution.fill_jupiter import JupiterFillModel
from flint.models import Candle, Order, OrderType, Side


def _make_candle(ts=1700000000, open_=100.0, close=100.0):
    return Candle(ts, open_, close + 1, open_ - 1, close, 10000.0, "SOL-PERP", 3600, "pyth")


def _make_order(side=Side.LONG, size=10.0):
    return Order(market="SOL-PERP", side=side, order_type=OrderType.MARKET, size=size,
                 price=0.0, order_id="test-1", ts=1700000000, venue="jupiter")


def test_fill_at_oracle_price_plus_impact():
    model = JupiterFillModel(seed=42)
    candle = _make_candle(close=100.0)
    order = _make_order(size=10.0)
    fill = model.fill_market(order, candle)
    assert fill is not None
    # Small order: impact negligible, fill near oracle price
    assert abs(fill.price - 100.0) < 1.0


def test_large_order_has_significant_impact():
    model = JupiterFillModel(seed=42)
    candle = _make_candle(close=100.0)
    small_order = _make_order(size=10.0)
    large_order = Order(market="SOL-PERP", side=Side.LONG, order_type=OrderType.MARKET,
                        size=10000.0, price=0.0, order_id="test-2", ts=1700000000, venue="jupiter")
    small_fill = model.fill_market(small_order, candle)
    large_fill = model.fill_market(large_order, candle)
    # Large order should have worse fill than small
    assert large_fill.price > small_fill.price


def test_keeper_delay_interpolation():
    """Fill price should interpolate based on keeper delay within the bar."""
    model = JupiterFillModel(base_latency_s=1800, latency_jitter_s=0, seed=42)
    # Bar with significant price movement
    candle = _make_candle(open_=100.0, close=110.0)
    fill = model.fill_market(_make_order(), candle)
    # With 1800s delay in a 3600s bar, fill should be ~halfway between open and close
    assert fill is not None
    assert fill.price > 100.0
    assert fill.price < 110.0


def test_candle_buffer_for_cross_bar_delay():
    """When delay crosses into next bar, use next bar's price."""
    model = JupiterFillModel(base_latency_s=4000, latency_jitter_s=0, seed=42)
    current = _make_candle(ts=1700000000, open_=100.0, close=105.0)
    next_bar = _make_candle(ts=1700003600, open_=106.0, close=110.0)
    model.set_candle_buffer([current, next_bar])
    fill = model.fill_market(_make_order(), current)
    assert fill is not None
    # Delay is 4000s, bar is 3600s, so fills in next bar
    assert fill.price >= 106.0  # should be at or after next bar open


def test_no_orderbook_needed():
    """Jupiter is pool-based, no orderbook."""
    model = JupiterFillModel(seed=42)
    model.set_orderbook(None)  # should be fine
    fill = model.fill_market(_make_order(), _make_candle())
    assert fill is not None


def test_short_order():
    model = JupiterFillModel(seed=42)
    fill = model.fill_market(_make_order(side=Side.SHORT), _make_candle(close=100.0))
    assert fill is not None
    assert fill.price <= 100.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fill_jupiter.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement fill_jupiter.py**

```python
# flint/execution/fill_jupiter.py
"""Jupiter Perps fill model — oracle-priced pool with keeper delay.

Fill price = interpolated oracle price (based on keeper delay) + quadratic impact fee.
No orderbook — Jupiter is pool-based.
"""
from __future__ import annotations

import random
from typing import List, Optional

from ..models import Candle, Fill, Order, OrderbookSnapshot, Side
from .fill_models import FillModel


# Default impact fee scalars per custody (from on-chain data)
_IMPACT_SCALARS = {
    "SOL-PERP": 1_000_000_000,
    "ETH-PERP": 1_000_000_000,
    "BTC-PERP": 1_000_000_000,
}


class JupiterFillModel(FillModel):
    """Oracle-priced pool fill with keeper delay interpolation."""

    def __init__(
        self,
        base_latency_s: float = 12.0,
        latency_jitter_s: float = 8.0,
        seed: Optional[int] = None,
    ):
        self._base_latency = base_latency_s
        self._jitter = latency_jitter_s
        self._rng = random.Random(seed)
        self._candle_buffer: List[Candle] = []

    def set_orderbook(self, book: Optional[OrderbookSnapshot]) -> None:
        pass  # Jupiter has no orderbook

    def set_candle_buffer(self, candles: List[Candle]) -> None:
        """Provide lookahead candles for keeper delay interpolation."""
        self._candle_buffer = candles

    def fill_market(self, order: Order, candle: Candle) -> Optional[Fill]:
        # Compute keeper delay
        delay_s = self._base_latency + self._rng.uniform(-self._jitter, self._jitter)
        delay_s = max(0, delay_s)

        # Interpolate fill price based on delay
        fill_price = self._interpolate_price(candle, delay_s)

        # Apply quadratic impact fee
        notional = order.size * fill_price
        scalar = _IMPACT_SCALARS.get(order.market, 1_000_000_000)
        impact_fee_usd = (notional / scalar) * notional
        impact_per_unit = impact_fee_usd / order.size if order.size > 0 else 0

        if order.side == Side.LONG:
            fill_price += impact_per_unit
        else:
            fill_price -= impact_per_unit

        return Fill(
            order_id=order.order_id, market=order.market, side=order.side,
            price=fill_price, size=order.size, fee=0.0, ts=candle.ts,
            venue="jupiter",
        )

    def _interpolate_price(self, candle: Candle, delay_s: float) -> float:
        """Interpolate price based on keeper delay within/across bars."""
        bar_duration = candle.resolution_s
        if bar_duration <= 0:
            return candle.close

        if delay_s <= bar_duration:
            # Within current bar: interpolate open → close
            frac = delay_s / bar_duration
            return candle.open + frac * (candle.close - candle.open)
        else:
            # Crosses into next bar
            remaining_s = delay_s - bar_duration
            next_candle = self._find_next_candle(candle)
            if next_candle is None:
                return candle.close  # end of data
            frac = min(remaining_s / next_candle.resolution_s, 1.0)
            return next_candle.open + frac * (next_candle.close - next_candle.open)

    def _find_next_candle(self, current: Candle) -> Optional[Candle]:
        """Find the next candle in the buffer after the current one."""
        next_ts = current.ts + current.resolution_s
        for c in self._candle_buffer:
            if c.ts == next_ts and c.market == current.market:
                return c
        return None

    def fill_limit(self, order: Order, candle: Candle) -> Optional[Fill]:
        if order.side == Side.LONG and candle.low <= order.price:
            return Fill(order_id=order.order_id, market=order.market, side=order.side,
                        price=order.price, size=order.size, fee=0.0, ts=candle.ts,
                        venue="jupiter")
        if order.side == Side.SHORT and candle.high >= order.price:
            return Fill(order_id=order.order_id, market=order.market, side=order.side,
                        price=order.price, size=order.size, fee=0.0, ts=candle.ts,
                        venue="jupiter")
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fill_jupiter.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add flint/execution/fill_jupiter.py tests/test_fill_jupiter.py
git commit -m "feat: add Jupiter fill model with keeper-delay interpolation"
```

---

## Task 7: Engine Refactor — Per-venue fill model dispatch

**Files:**
- Modify: `flint/backtest/engine.py`
- Modify: `flint/execution/venue_config.py` (add fill_model_type)
- Create: `flint/execution/fill_registry.py`
- Test: `tests/test_venue_fill_dispatch.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_venue_fill_dispatch.py
"""Tests for per-venue fill model dispatch in backtest engine."""
from flint.execution.fill_registry import create_fill_model, create_venue_fill_models
from flint.execution.fill_drift import DriftFillModel
from flint.execution.fill_hyperliquid import HyperliquidFillModel
from flint.execution.fill_jupiter import JupiterFillModel
from flint.execution.fill_cex import BinanceFillModel, OkxFillModel, BybitFillModel


def test_create_fill_model_by_venue():
    model = create_fill_model("drift")
    assert isinstance(model, DriftFillModel)

    model = create_fill_model("hyperliquid")
    assert isinstance(model, HyperliquidFillModel)

    model = create_fill_model("jupiter")
    assert isinstance(model, JupiterFillModel)

    model = create_fill_model("binance")
    assert isinstance(model, BinanceFillModel)

    model = create_fill_model("okx")
    assert isinstance(model, OkxFillModel)

    model = create_fill_model("bybit")
    assert isinstance(model, BybitFillModel)


def test_create_fill_model_unknown_venue():
    """Unknown venues should get a default FillPipeline."""
    from flint.execution.fill_models import FillPipeline
    model = create_fill_model("unknown_venue")
    assert isinstance(model, FillPipeline)


def test_create_venue_fill_models():
    """Create fill models for multiple venues at once."""
    models = create_venue_fill_models(["drift", "hyperliquid", "jupiter"])
    assert "drift" in models
    assert "hyperliquid" in models
    assert "jupiter" in models
    assert isinstance(models["drift"], DriftFillModel)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_venue_fill_dispatch.py -v`
Expected: FAIL — `ModuleNotFoundError: ... fill_registry`

- [ ] **Step 3: Create fill_registry.py**

```python
# flint/execution/fill_registry.py
"""Registry for creating venue-specific fill models."""
from __future__ import annotations

from typing import Dict

from .fill_models import FillModel, FillPipeline


# Lazy imports to avoid circular dependencies
_VENUE_FILL_CLASSES = {
    "drift": "flint.execution.fill_drift:DriftFillModel",
    "hyperliquid": "flint.execution.fill_hyperliquid:HyperliquidFillModel",
    "jupiter": "flint.execution.fill_jupiter:JupiterFillModel",
    "binance": "flint.execution.fill_cex:BinanceFillModel",
    "okx": "flint.execution.fill_cex:OkxFillModel",
    "bybit": "flint.execution.fill_cex:BybitFillModel",
}


def create_fill_model(venue: str, **kwargs) -> FillModel:
    """Create a fill model for a specific venue."""
    entry = _VENUE_FILL_CLASSES.get(venue)
    if entry is None:
        return FillPipeline(**kwargs)

    module_path, class_name = entry.rsplit(":", 1)
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(**kwargs)


def create_venue_fill_models(venues: list, **kwargs) -> Dict[str, FillModel]:
    """Create fill models for multiple venues."""
    return {v: create_fill_model(v, **kwargs) for v in venues}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_venue_fill_dispatch.py -v`
Expected: 3 passed

- [ ] **Step 5: Modify BacktestEngine for per-venue dispatch**

Read `flint/backtest/engine.py`. Currently `self._fill_model` is a single `FillModel`. Change to support per-venue dispatch:

1. Add `venue_fill_models: Optional[Dict[str, FillModel]] = None` parameter to `__init__`
2. In the orderbook feeding loop (lines 245-247), feed orderbooks to the correct venue's fill model
3. In `BacktestContext.process_market_orders`, use the venue from the order to select the right fill model

The engine should maintain backward compatibility: if `venue_fill_models` is not provided, use the single `fill_model` for all orders (existing behavior).

Key changes:

In `BacktestEngine.__init__`:
```python
self._venue_fill_models = venue_fill_models or {}
# If venue models provided, use them; otherwise fall back to single fill_model
```

In the main loop, when feeding orderbooks:
```python
# Feed orderbook to venue-specific fill models
for venue_name, venue_model in self._venue_fill_models.items():
    if hasattr(venue_model, 'set_orderbook'):
        book = ctx.get_orderbook(candle.market)  # TODO: per-venue books
        venue_model.set_orderbook(book)
```

In `BacktestContext`, when processing orders:
```python
# Select fill model based on order venue
venue = order.venue or "default"
fill_model = self._venue_fill_models.get(venue, self._fill_model)
fill = fill_model.fill_market(order, candle)
```

Pass `venue_fill_models` to `BacktestContext` alongside the existing `fill_model`.

- [ ] **Step 6: Feed Jupiter candle buffer**

For `JupiterFillModel`, the engine needs to pass the candle lookahead buffer. In the main loop, before calling strategy:

```python
for venue_name, venue_model in self._venue_fill_models.items():
    if hasattr(venue_model, 'set_candle_buffer'):
        # Pass remaining candles from current position
        venue_model.set_candle_buffer(candles[bar_idx:bar_idx + 5])
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_venue_fill_dispatch.py tests/test_fill_drift.py tests/test_fill_hyperliquid.py tests/test_fill_jupiter.py tests/test_fill_cex.py -v`
Expected: All pass

- [ ] **Step 8: Run full backtest tests for regressions**

Run: `pytest tests/ -k "backtest" -v`
Expected: All pass (backward compatible — no venue_fill_models = existing behavior)

- [ ] **Step 9: Commit**

```bash
git add flint/execution/fill_registry.py flint/backtest/engine.py flint/execution/backtest_context.py tests/test_venue_fill_dispatch.py
git commit -m "feat: add per-venue fill model dispatch in backtest engine"
```

---

## Task 8: Tardis.dev Provider

**Files:**
- Create: `flint/providers/tardis.py`
- Test: `tests/test_tardis_provider.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tardis_provider.py
"""Tests for Tardis.dev orderbook data provider."""
from unittest.mock import MagicMock, patch
from flint.providers.tardis import TardisOrderbookProvider


MOCK_CSV_DATA = """timestamp,local_timestamp,ask_price_0,ask_amount_0,ask_price_1,ask_amount_1,bid_price_0,bid_amount_0,bid_price_1,bid_amount_1
1700000000000000,1700000000100000,100.1,50.0,100.2,100.0,99.9,60.0,99.8,120.0
1700000300000000,1700000300100000,100.15,55.0,100.25,95.0,99.85,65.0,99.75,115.0
"""


def test_parse_csv_row():
    provider = TardisOrderbookProvider(api_key="fake")
    snapshots = provider._parse_csv(MOCK_CSV_DATA, "binance", "SOL-PERP", num_levels=2)
    assert len(snapshots) == 2
    assert snapshots[0]["venue"] == "binance"
    assert snapshots[0]["market"] == "SOL-PERP"
    assert snapshots[0]["ask_prices"][0] == 100.1
    assert snapshots[0]["bid_prices"][0] == 99.9


def test_is_available():
    assert TardisOrderbookProvider(api_key="").is_available() is False
    assert TardisOrderbookProvider(api_key="td_test").is_available() is True


def test_timestamp_aligned_to_5min():
    provider = TardisOrderbookProvider(api_key="fake")
    # 1700000123 should align to 1700000000 (nearest 5min boundary before)
    assert provider._align_ts(1700000123) == 1700000000
    assert provider._align_ts(1700000300) == 1700000300
    assert provider._align_ts(1700000599) == 1700000300
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tardis_provider.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement tardis.py**

```python
# flint/providers/tardis.py
"""Tardis.dev orderbook data provider.

Downloads historical L2 orderbook snapshots for CEX venues (Binance, OKX, Bybit).
Requires FLINT_TARDIS_API_KEY.
"""
from __future__ import annotations

import csv
import gzip
import io
import logging
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_DATASETS_URL = "https://datasets.tardis.dev/v1"

# Tardis exchange names
_EXCHANGE_MAP = {
    "binance": "binance-futures",
    "okx": "okx-swap",
    "bybit": "bybit",
}

# Flint market to Tardis symbol
_SYMBOL_MAP = {
    "SOL-PERP": {"binance-futures": "SOLUSDT", "okx-swap": "SOL-USDT-SWAP", "bybit": "SOLUSDT"},
    "BTC-PERP": {"binance-futures": "BTCUSDT", "okx-swap": "BTC-USDT-SWAP", "bybit": "BTCUSDT"},
    "ETH-PERP": {"binance-futures": "ETHUSDT", "okx-swap": "ETH-USDT-SWAP", "bybit": "ETHUSDT"},
}


class TardisOrderbookProvider:
    """Downloads historical orderbook snapshots from Tardis.dev."""

    def __init__(self, api_key: str, max_gb: float = 1.0,
                 client: Optional[httpx.Client] = None):
        self._api_key = api_key
        self._max_gb = max_gb
        self._client = client or httpx.Client(timeout=120)
        self._owns_client = client is None

    def is_available(self) -> bool:
        return bool(self._api_key)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @staticmethod
    def _align_ts(ts: int) -> int:
        """Align timestamp to 5-minute boundary."""
        return ts - (ts % 300)

    def _parse_csv(self, csv_text: str, venue: str, market: str,
                   num_levels: int = 25) -> List[dict]:
        """Parse Tardis CSV into orderbook snapshot dicts."""
        reader = csv.DictReader(io.StringIO(csv_text))
        snapshots = []
        seen_ts = set()

        for row in reader:
            ts_us = int(row["timestamp"])
            ts_s = self._align_ts(ts_us // 1_000_000)

            if ts_s in seen_ts:
                continue  # skip duplicates within same 5min window
            seen_ts.add(ts_s)

            ask_prices = []
            ask_sizes = []
            bid_prices = []
            bid_sizes = []

            for i in range(num_levels):
                ap_key = f"ask_price_{i}"
                aa_key = f"ask_amount_{i}"
                bp_key = f"bid_price_{i}"
                ba_key = f"bid_amount_{i}"
                if ap_key in row and row[ap_key]:
                    ask_prices.append(float(row[ap_key]))
                    ask_sizes.append(float(row[aa_key]))
                if bp_key in row and row[bp_key]:
                    bid_prices.append(float(row[bp_key]))
                    bid_sizes.append(float(row[ba_key]))

            snapshots.append({
                "venue": venue,
                "market": market,
                "ts": ts_s,
                "ask_prices": ask_prices,
                "ask_sizes": ask_sizes,
                "bid_prices": bid_prices,
                "bid_sizes": bid_sizes,
            })

        return snapshots

    def fetch(self, venue: str, market: str, date: str) -> List[dict]:
        """Fetch orderbook snapshots for a venue/market/date.

        Args:
            venue: Flint venue name (binance, okx, bybit)
            market: Flint market name (SOL-PERP)
            date: Date string YYYY/MM/DD

        Returns: List of snapshot dicts for store.upsert_orderbook_snapshots()
        """
        if not self.is_available():
            return []

        exchange = _EXCHANGE_MAP.get(venue)
        if not exchange:
            logger.warning(f"Tardis: unsupported venue {venue}")
            return []

        symbols = _SYMBOL_MAP.get(market, {})
        symbol = symbols.get(exchange)
        if not symbol:
            logger.warning(f"Tardis: no symbol mapping for {market} on {exchange}")
            return []

        url = f"{_DATASETS_URL}/{exchange}/book_snapshot_25/{date}/{symbol}.csv.gz"
        try:
            resp = self._client.get(url, headers={"Authorization": f"Bearer {self._api_key}"})
            resp.raise_for_status()
            csv_text = gzip.decompress(resp.content).decode("utf-8")
            return self._parse_csv(csv_text, venue, market)
        except Exception as e:
            logger.warning(f"Tardis fetch failed for {venue}/{market}/{date}: {e}")
            return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tardis_provider.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add flint/providers/tardis.py tests/test_tardis_provider.py
git commit -m "feat: add Tardis.dev orderbook data provider"
```

---

## Task 9: Hyperliquid Orderbook Provider

**Files:**
- Create: `flint/providers/hyperliquid_orderbook.py`
- Test: `tests/test_hyperliquid_orderbook.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_hyperliquid_orderbook.py
"""Tests for Hyperliquid S3 archive orderbook provider."""
from unittest.mock import MagicMock, patch
from flint.providers.hyperliquid_orderbook import HyperliquidOrderbookProvider


def test_is_available():
    provider = HyperliquidOrderbookProvider()
    assert provider.is_available() is True  # S3 is public, no key needed


def test_build_s3_url():
    provider = HyperliquidOrderbookProvider()
    url = provider._build_url("2024", "06", "01", "12", "SOL")
    assert "hyperliquid-archive" in url
    assert "2024/06/01/12" in url
    assert "SOL" in url


def test_market_to_coin():
    provider = HyperliquidOrderbookProvider()
    assert provider._market_to_coin("SOL-PERP") == "SOL"
    assert provider._market_to_coin("BTC-PERP") == "BTC"
    assert provider._market_to_coin("ETH-PERP") == "ETH"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hyperliquid_orderbook.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement hyperliquid_orderbook.py**

```python
# flint/providers/hyperliquid_orderbook.py
"""Hyperliquid S3 archive orderbook provider.

Downloads historical L2 snapshots from s3://hyperliquid-archive/market_data/
Free, no API key needed. Data uploaded ~monthly, may have gaps.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

_S3_BASE = "https://hyperliquid-archive.s3.amazonaws.com/market_data"


class HyperliquidOrderbookProvider:
    """Downloads L2 orderbook snapshots from Hyperliquid's public S3 archive."""

    def __init__(self, client: Optional[httpx.Client] = None):
        self._client = client or httpx.Client(timeout=120)
        self._owns_client = client is None

    def is_available(self) -> bool:
        return True  # public S3, no key

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @staticmethod
    def _market_to_coin(market: str) -> str:
        return market.split("-")[0]

    def _build_url(self, year: str, month: str, day: str, hour: str, coin: str) -> str:
        return f"{_S3_BASE}/{year}/{month}/{day}/{hour}/l2Book/{coin}.lz4"

    @staticmethod
    def _align_ts(ts: int) -> int:
        return ts - (ts % 300)

    def fetch(self, market: str, date: str, hours: Optional[List[int]] = None) -> List[dict]:
        """Fetch orderbook snapshots for a market/date.

        Args:
            market: Flint market name (SOL-PERP)
            date: Date string YYYY-MM-DD
            hours: Optional list of hours to fetch (0-23). Default: all.

        Returns: List of snapshot dicts for store.upsert_orderbook_snapshots()
        """
        coin = self._market_to_coin(market)
        parts = date.split("-")
        if len(parts) != 3:
            return []
        year, month, day = parts

        fetch_hours = hours or list(range(24))
        all_snapshots = []

        for hour in fetch_hours:
            url = self._build_url(year, month, day, f"{hour:02d}", coin)
            try:
                resp = self._client.get(url)
                if resp.status_code == 404:
                    continue  # gap in archive
                resp.raise_for_status()

                # Decompress LZ4 and parse
                try:
                    import lz4.frame
                    data = lz4.frame.decompress(resp.content)
                except ImportError:
                    logger.warning("lz4 not installed — cannot decompress Hyperliquid archive")
                    return []

                for line in data.decode("utf-8").strip().split("\n"):
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        ts = self._align_ts(record.get("time", 0) // 1000)
                        levels = record.get("levels", [[], []])
                        bid_levels = levels[0] if len(levels) > 0 else []
                        ask_levels = levels[1] if len(levels) > 1 else []

                        all_snapshots.append({
                            "venue": "hyperliquid",
                            "market": market,
                            "ts": ts,
                            "bid_prices": [float(l.get("px", 0)) for l in bid_levels[:20]],
                            "bid_sizes": [float(l.get("sz", 0)) for l in bid_levels[:20]],
                            "ask_prices": [float(l.get("px", 0)) for l in ask_levels[:20]],
                            "ask_sizes": [float(l.get("sz", 0)) for l in ask_levels[:20]],
                        })
                    except (json.JSONDecodeError, KeyError):
                        continue
            except Exception as e:
                logger.warning(f"Hyperliquid archive fetch failed for {url}: {e}")
                continue

        return all_snapshots
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hyperliquid_orderbook.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add flint/providers/hyperliquid_orderbook.py tests/test_hyperliquid_orderbook.py
git commit -m "feat: add Hyperliquid S3 archive orderbook provider"
```

---

## Task 10: Integration Tests — Venue Fill Pipeline

**Files:**
- Create: `tests/test_venue_fill_integration.py`

- [ ] **Step 1: Write integration tests**

```python
# tests/test_venue_fill_integration.py
"""Integration tests for per-venue fill pipelines in backtests."""
from flint.models import Candle, Order, OrderbookLevel, OrderbookSnapshot, Side
from flint.execution.fill_registry import create_fill_model


def _make_candles(n=5, price=100.0):
    return [
        Candle(1700000000 + i * 3600, price + i, price + i + 1,
               price + i - 1, price + i + 0.5, 10000.0, "SOL-PERP", 3600, "pyth")
        for i in range(n)
    ]


def _make_book(mid=100.0):
    return OrderbookSnapshot(
        market="SOL-PERP", ts=1700000000,
        bids=tuple([OrderbookLevel(mid - 0.1 * (i + 1), 100.0) for i in range(10)]),
        asks=tuple([OrderbookLevel(mid + 0.1 * (i + 1), 100.0) for i in range(10)]),
    )


def test_all_venues_produce_fills():
    """Every venue fill model should produce a fill for a standard order."""
    venues = ["drift", "hyperliquid", "jupiter", "binance", "okx", "bybit"]
    candle = _make_candles(1)[0]
    book = _make_book(100.0)

    for venue in venues:
        model = create_fill_model(venue, seed=42) if venue in ("drift", "jupiter") else create_fill_model(venue)
        if hasattr(model, 'set_orderbook'):
            model.set_orderbook(book)
        if hasattr(model, 'set_candle_buffer'):
            model.set_candle_buffer(_make_candles(5))

        order = Order(market="SOL-PERP", side=Side.LONG, order_type="market",
                      size=10.0, price=0.0, order_id=f"test-{venue}", ts=1700000000,
                      venue=venue)
        fill = model.fill_market(order, candle)
        assert fill is not None, f"{venue} failed to produce a fill"
        assert fill.size > 0, f"{venue} fill has zero size"
        assert fill.price > 0, f"{venue} fill has zero price"


def test_venue_fill_prices_differ():
    """Different venues should produce different fill prices due to their models."""
    candle = _make_candles(1)[0]
    book = _make_book(100.0)
    prices = {}

    for venue in ["drift", "hyperliquid", "binance"]:
        model = create_fill_model(venue, seed=42) if venue == "drift" else create_fill_model(venue)
        model.set_orderbook(book)
        order = Order(market="SOL-PERP", side=Side.LONG, order_type="market",
                      size=10.0, price=0.0, order_id=f"test-{venue}", ts=1700000000,
                      venue=venue)
        fill = model.fill_market(order, candle)
        prices[venue] = fill.price

    # At least some prices should differ (different fill models)
    unique_prices = set(round(p, 4) for p in prices.values())
    assert len(unique_prices) >= 2, f"Expected different prices, got {prices}"


def test_switching_venues_mid_strategy():
    """Simulate closing a Drift position and opening a Jupiter position."""
    drift_model = create_fill_model("drift", seed=42)
    jupiter_model = create_fill_model("jupiter", seed=42)
    candles = _make_candles(5)
    book = _make_book(100.0)

    drift_model.set_orderbook(book)
    jupiter_model.set_candle_buffer(candles)

    # Open on Drift
    open_order = Order(market="SOL-PERP", side=Side.LONG, order_type="market",
                       size=10.0, price=0.0, order_id="open-drift", ts=1700000000, venue="drift")
    open_fill = drift_model.fill_market(open_order, candles[0])
    assert open_fill is not None

    # Close on Drift, open on Jupiter (same bar)
    close_order = Order(market="SOL-PERP", side=Side.SHORT, order_type="market",
                        size=10.0, price=0.0, order_id="close-drift", ts=1700003600, venue="drift")
    jupiter_order = Order(market="SOL-PERP", side=Side.LONG, order_type="market",
                          size=10.0, price=0.0, order_id="open-jupiter", ts=1700003600, venue="jupiter")

    close_fill = drift_model.fill_market(close_order, candles[1])
    jupiter_fill = jupiter_model.fill_market(jupiter_order, candles[1])

    assert close_fill is not None
    assert jupiter_fill is not None
    # Jupiter and Drift fills should have different prices
    assert close_fill.price != jupiter_fill.price


def test_orderbook_gap_uses_synthetic():
    """When orderbook data is missing, fill model should use synthetic depth."""
    model = create_fill_model("binance")
    # Don't set orderbook — should use synthetic
    candle = _make_candles(1)[0]
    order = Order(market="SOL-PERP", side=Side.LONG, order_type="market",
                  size=10.0, price=0.0, order_id="test", ts=1700000000, venue="binance")
    fill = model.fill_market(order, candle)
    assert fill is not None
    assert fill.price > 100.0  # synthetic depth applies slippage
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_venue_fill_integration.py -v`
Expected: 4 passed

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_venue_fill_integration.py
git commit -m "test: add per-venue fill pipeline integration tests"
```

---

## Summary

| Task | Component | Est. Lines |
|------|-----------|-----------|
| 1 | Orderbook storage (venue column + query_nearest) | ~60 |
| 2 | Synthetic depth model | ~80 |
| 3 | CexFillModel (Binance/OKX/Bybit) | ~120 |
| 4 | HyperliquidFillModel | ~90 |
| 5 | DriftFillModel (3-tier) | ~130 |
| 6 | JupiterFillModel (keeper-delay) | ~110 |
| 7 | Engine refactor + fill registry | ~80 |
| 8 | Tardis.dev provider | ~120 |
| 9 | Hyperliquid orderbook provider | ~100 |
| 10 | Integration tests | ~100 |
| **Total** | | **~990** |

**Dependencies**: Tasks 1-2 are foundation (parallel). Tasks 3-6 depend on Task 2 (synthetic depth). Task 7 depends on Tasks 3-6. Tasks 8-9 are independent data providers. Task 10 depends on all above.
