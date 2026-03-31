# WebSocket Feeds — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build real-time WebSocket data feeds (Drift trades/funding, Pyth oracle prices) with a reusable base class, event-driven strategy ticking, and automatic REST fallback on disconnect.

**Architecture:** `WebSocketFeed` ABC handles lifecycle, reconnection, and health checks. `DriftWebSocketFeed` subscribes to trade events and feeds them through `CandleAggregator` to build OHLCV bars. `PythWebSocketFeed` streams oracle prices into a cache. `LiveExecutionContext.run()` gains an event-driven mode where candle close events trigger strategy ticks via an `asyncio.Queue`, replacing the timer loop.

**Tech Stack:** Python 3.10+, `websockets` library (new dep), asyncio, DuckDB (FlintStore)

---

### Task 1: Add `venue` field to Candle dataclass

**Files:**
- Modify: `flint/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_models.py`:

```python
class TestCandleVenue:
    def test_default_venue(self):
        from flint.models import Candle
        c = Candle(ts=1000, open=100.0, high=101.0, low=99.0,
                   close=100.5, volume=500.0, market="SOL-PERP", resolution_s=60)
        assert c.venue == "default"

    def test_explicit_venue(self):
        from flint.models import Candle
        c = Candle(ts=1000, open=100.0, high=101.0, low=99.0,
                   close=100.5, volume=500.0, market="SOL-PERP", resolution_s=60,
                   venue="drift")
        assert c.venue == "drift"

    def test_backward_compatible_with_existing_code(self):
        from flint.models import Candle
        # Existing code constructs Candle without venue — should work
        c = Candle(ts=1000, open=100.0, high=101.0, low=99.0,
                   close=100.5, volume=500.0, market="SOL-PERP", resolution_s=60)
        assert c.market == "SOL-PERP"
        assert c.venue == "default"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py::TestCandleVenue -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'venue'` or `AttributeError`

- [ ] **Step 3: Add venue field to Candle**

In `flint/models.py`, modify the `Candle` dataclass (around line 56) to add `venue` as the last field:

```python
@dataclass(frozen=True)
class Candle:
    ts: int  # unix seconds (bucket start)
    open: float
    high: float
    low: float
    close: float
    volume: float  # base asset amount
    market: str  # e.g. "SOL-PERP"
    resolution_s: int  # candle width in seconds
    venue: str = "default"  # "drift", "hyperliquid", "default" for backtest
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_models.py -v && pytest tests/ -x -q --timeout=120`
Expected: All pass (venue has a default, so no existing code breaks)

- [ ] **Step 5: Commit**

```bash
git add flint/models.py tests/test_models.py
git commit -m "feat: add venue field to Candle dataclass for multi-venue support"
```

---

### Task 2: Add WebSocket config fields

**Files:**
- Modify: `flint/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_config.py`:

```python
class TestWebSocketConfig:
    def test_defaults(self):
        cfg = FlintConfig()
        assert cfg.live_tick_mode == "on_candle_close"
        assert cfg.live_candle_resolution_s == 60
        assert cfg.live_tick_markets == []

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_LIVE_TICK_MODE", "timer")
        monkeypatch.setenv("FLINT_LIVE_CANDLE_RESOLUTION_S", "300")
        cfg = FlintConfig()
        assert cfg.live_tick_mode == "timer"
        assert cfg.live_candle_resolution_s == 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::TestWebSocketConfig -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Add config fields**

In `flint/config.py`, add after the existing live trading fields (after `live_wallet_mode`, line 116):

```python
    # --- Live trading (WebSocket feeds) ---
    live_tick_mode: str = "on_candle_close"
    live_candle_resolution_s: int = 60
    live_tick_markets: List[str] = Field(default=[])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/config.py tests/test_config.py
git commit -m "feat: add WebSocket feed config fields (tick_mode, candle_resolution, tick_markets)"
```

---

### Task 3: Add `websockets` dependency

**Files:**
- Modify: `setup.py` or `pyproject.toml` (whichever manages deps)

- [ ] **Step 1: Check which file manages dependencies**

Run: `ls setup.py pyproject.toml setup.cfg 2>/dev/null`

- [ ] **Step 2: Add `websockets` to dependencies**

Add `websockets>=12.0` to the install_requires or dependencies list, in the same section as other runtime dependencies.

- [ ] **Step 3: Install the dependency**

Run: `pip install websockets>=12.0`

- [ ] **Step 4: Verify import works**

Run: `python -c "import websockets; print(websockets.__version__)"`

- [ ] **Step 5: Commit**

```bash
git add setup.py  # or pyproject.toml
git commit -m "deps: add websockets library for live data feeds"
```

---

### Task 4: CandleAggregator

**Files:**
- Create: `flint/providers/candle_aggregator.py`
- Test: `tests/test_candle_aggregator.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_candle_aggregator.py`:

```python
"""Tests for CandleAggregator — trade-to-candle conversion."""
import pytest
from flint.models import Candle
from flint.providers.candle_aggregator import CandleAggregator


class TestBarConstruction:
    def test_first_trade_opens_bar(self):
        closed = []
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
        )
        agg.process_trade(price=150.0, size=10.0, ts=1000)
        bar = agg.current_bar()
        assert bar is not None
        assert bar.open == 150.0
        assert bar.high == 150.0
        assert bar.low == 150.0
        assert bar.close == 150.0
        assert bar.volume == 10.0
        assert len(closed) == 0

    def test_updates_ohlcv(self):
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: None,
        )
        agg.process_trade(price=150.0, size=10.0, ts=1000)
        agg.process_trade(price=152.0, size=5.0, ts=1010)   # new high
        agg.process_trade(price=148.0, size=3.0, ts=1020)   # new low
        agg.process_trade(price=151.0, size=7.0, ts=1030)   # new close
        bar = agg.current_bar()
        assert bar.open == 150.0
        assert bar.high == 152.0
        assert bar.low == 148.0
        assert bar.close == 151.0
        assert bar.volume == 25.0

    def test_bar_closes_on_boundary(self):
        closed = []
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
        )
        # Bar 1: ts 960-1019 (bar_start = 960)
        agg.process_trade(price=150.0, size=10.0, ts=960)
        agg.process_trade(price=152.0, size=5.0, ts=980)
        # This trade at ts=1020 crosses the 960+60=1020 boundary → close bar
        agg.process_trade(price=153.0, size=8.0, ts=1020)
        assert len(closed) == 1
        assert closed[0].ts == 960
        assert closed[0].open == 150.0
        assert closed[0].close == 152.0
        assert closed[0].volume == 15.0
        assert closed[0].venue == "drift"
        assert closed[0].market == "SOL-PERP"
        assert closed[0].resolution_s == 60
        # New bar started with the crossing trade
        bar = agg.current_bar()
        assert bar.open == 153.0
        assert bar.volume == 8.0

    def test_multiple_bar_closes(self):
        closed = []
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
        )
        agg.process_trade(price=100.0, size=1.0, ts=0)
        agg.process_trade(price=101.0, size=1.0, ts=30)
        # Jump 2 bars ahead — should close the first bar
        agg.process_trade(price=105.0, size=1.0, ts=120)
        assert len(closed) >= 1
        assert closed[0].ts == 0

    def test_no_trades_no_bar(self):
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: None,
        )
        assert agg.current_bar() is None

    def test_store_persistence(self, tmp_path):
        from flint.store import FlintStore
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        closed = []
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
            store=store,
        )
        agg.process_trade(price=150.0, size=10.0, ts=0)
        agg.process_trade(price=151.0, size=5.0, ts=60)  # closes bar at ts=0
        assert len(closed) == 1
        candles = store.query_candles("SOL-PERP", 60)
        assert len(candles) == 1
        assert candles[0].close == 150.0
        store.close()

    def test_close_bar_explicitly(self):
        closed = []
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
        )
        agg.process_trade(price=150.0, size=10.0, ts=0)
        candle = agg.close_bar()
        assert candle is not None
        assert candle.close == 150.0
        assert len(closed) == 1
        assert agg.current_bar() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_candle_aggregator.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create candle_aggregator.py**

Create `flint/providers/candle_aggregator.py`:

```python
"""CandleAggregator — converts raw trade events into OHLCV candles.

One instance per market. Receives trade-by-trade data and emits
closed candles at the configured resolution.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from ..models import Candle

logger = logging.getLogger("flint.candle_aggregator")


class CandleAggregator:
    """Aggregates raw trades into OHLCV candle bars.

    Args:
        market: Market symbol (e.g. "SOL-PERP").
        venue: Venue name (e.g. "drift").
        resolution_s: Candle width in seconds.
        on_candle_close: Callback fired when a bar closes.
        store: Optional FlintStore for persisting closed candles.
    """

    def __init__(
        self,
        market: str,
        venue: str,
        resolution_s: int,
        on_candle_close: Callable[[Candle], None],
        store=None,
    ):
        self._market = market
        self._venue = venue
        self._resolution_s = resolution_s
        self._on_candle_close = on_candle_close
        self._store = store

        # In-progress bar state
        self._bar_start: int = 0
        self._open: float = 0.0
        self._high: float = 0.0
        self._low: float = 0.0
        self._close: float = 0.0
        self._volume: float = 0.0
        self._has_data = False

    def process_trade(self, price: float, size: float, ts: int) -> None:
        """Process a single trade. Closes bar if ts crosses boundary."""
        if not self._has_data:
            # First trade — start a new bar
            self._start_bar(price, size, ts)
            return

        bar_end = self._bar_start + self._resolution_s
        if ts >= bar_end:
            # Trade crosses bar boundary — close current bar, start new one
            self._close_and_emit()
            self._start_bar(price, size, ts)
        else:
            # Update in-progress bar
            self._high = max(self._high, price)
            self._low = min(self._low, price)
            self._close = price
            self._volume += size

    def current_bar(self) -> Optional[Candle]:
        """Return the in-progress (unclosed) candle, or None if no data."""
        if not self._has_data:
            return None
        return Candle(
            ts=self._bar_start,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=self._volume,
            market=self._market,
            resolution_s=self._resolution_s,
            venue=self._venue,
        )

    def close_bar(self) -> Optional[Candle]:
        """Force-close the current bar. Returns the closed candle or None."""
        if not self._has_data:
            return None
        return self._close_and_emit()

    def _start_bar(self, price: float, size: float, ts: int) -> None:
        """Start a new bar at the floor of ts to resolution boundary."""
        self._bar_start = (ts // self._resolution_s) * self._resolution_s
        self._open = price
        self._high = price
        self._low = price
        self._close = price
        self._volume = size
        self._has_data = True

    def _close_and_emit(self) -> Candle:
        """Close current bar, persist, fire callback, return candle."""
        candle = Candle(
            ts=self._bar_start,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=self._volume,
            market=self._market,
            resolution_s=self._resolution_s,
            venue=self._venue,
        )
        self._has_data = False

        # Persist to store
        if self._store:
            try:
                self._store.upsert_candles([candle])
            except Exception as e:
                logger.error("Failed to persist candle: %s", e)

        # Fire callback
        self._on_candle_close(candle)

        logger.debug("Bar closed: %s %s ts=%d O=%.2f H=%.2f L=%.2f C=%.2f V=%.2f",
                     self._venue, self._market, candle.ts,
                     candle.open, candle.high, candle.low, candle.close, candle.volume)
        return candle
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_candle_aggregator.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add flint/providers/candle_aggregator.py tests/test_candle_aggregator.py
git commit -m "feat: add CandleAggregator for trade-to-candle conversion"
```

---

### Task 5: WebSocketFeed base class

**Files:**
- Create: `flint/providers/websocket.py`
- Test: `tests/test_websocket_feed.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_websocket_feed.py`:

```python
"""Tests for WebSocketFeed base class — mocked, no real connections."""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from flint.providers.websocket import WebSocketFeed


class MockFeed(WebSocketFeed):
    """Concrete test implementation."""

    def __init__(self, **kwargs):
        super().__init__(url="wss://test.example.com/ws", name="test", **kwargs)
        self.messages_handled = []
        self.fallback_polls = 0
        self.backfill_calls = []
        self._mock_ws = None

    async def _connect_ws(self):
        self._mock_ws = AsyncMock()
        return self._mock_ws

    async def _subscribe(self, ws):
        pass

    async def _handle_message(self, raw):
        self.messages_handled.append(raw)

    async def _fallback_poll(self):
        self.fallback_polls += 1

    async def _backfill_gap(self, disconnect_ts, reconnect_ts):
        self.backfill_calls.append((disconnect_ts, reconnect_ts))


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestLifecycle:
    def test_initial_state(self):
        feed = MockFeed()
        assert feed.connected is False
        assert feed.name == "test"

    def test_start_and_stop(self):
        feed = MockFeed()
        # Mock the WS to raise on recv to end the loop quickly
        async def start_then_stop():
            feed._mock_ws = AsyncMock()
            feed._mock_ws.recv = AsyncMock(side_effect=Exception("closed"))
            # start() will connect, subscribe, then _message_loop will error and reconnect
            # We stop it quickly
            task = asyncio.create_task(feed.start())
            await asyncio.sleep(0.05)
            await feed.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        run(start_then_stop())
        assert feed.connected is False


class TestHealthCheck:
    def test_health_timeout_detected(self):
        feed = MockFeed(health_timeout_s=0.1)
        # Simulate connected but no messages
        feed._connected = True
        feed._last_message_ts = time.time() - 1.0  # 1 second ago, timeout is 0.1s
        assert feed._is_health_timeout() is True

    def test_no_timeout_when_recent_message(self):
        feed = MockFeed(health_timeout_s=30.0)
        feed._connected = True
        feed._last_message_ts = time.time()
        assert feed._is_health_timeout() is False


class TestReconnection:
    def test_backoff_delay_calculation(self):
        feed = MockFeed(max_reconnect_delay_s=60.0)
        assert feed._reconnect_delay(0) == 1.0
        assert feed._reconnect_delay(1) == 2.0
        assert feed._reconnect_delay(2) == 4.0
        assert feed._reconnect_delay(3) == 8.0
        assert feed._reconnect_delay(10) == 60.0  # capped at max


class TestFallback:
    def test_fallback_poll_called_when_disconnected(self):
        feed = MockFeed(fallback_poll_interval_s=0.01)
        async def run_fallback():
            feed._connected = False
            feed._running = True
            task = asyncio.create_task(feed._fallback_loop())
            await asyncio.sleep(0.05)
            feed._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        run(run_fallback())
        assert feed.fallback_polls > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_websocket_feed.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create websocket.py**

Create `flint/providers/websocket.py`:

```python
"""WebSocketFeed — abstract base class for venue WebSocket connections.

Handles lifecycle, reconnection with exponential backoff, health monitoring,
and REST fallback during disconnections. Subclasses implement venue-specific
connection, subscription, and message handling.
"""
from __future__ import annotations

import abc
import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger("flint.websocket")


class WebSocketFeed(abc.ABC):
    """Base class for all WebSocket data feeds.

    Args:
        url: WebSocket endpoint URL.
        name: Feed name for logging (e.g. "drift", "pyth").
        health_timeout_s: Force reconnect if no message for this many seconds.
        max_reconnect_delay_s: Cap on exponential backoff delay.
        fallback_poll_interval_s: REST poll interval while disconnected.
    """

    def __init__(
        self,
        url: str,
        name: str,
        health_timeout_s: float = 30.0,
        max_reconnect_delay_s: float = 60.0,
        fallback_poll_interval_s: float = 5.0,
    ):
        self._url = url
        self._name = name
        self._health_timeout_s = health_timeout_s
        self._max_reconnect_delay_s = max_reconnect_delay_s
        self._fallback_poll_interval_s = fallback_poll_interval_s

        self._ws = None
        self._connected = False
        self._running = False
        self._last_message_ts: float = 0.0
        self._disconnect_ts: int = 0
        self._reconnect_attempt = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def connected(self) -> bool:
        return self._connected

    # --- Subclass implements ---

    @abc.abstractmethod
    async def _connect_ws(self):
        """Establish the raw WebSocket connection. Returns ws object."""
        ...

    @abc.abstractmethod
    async def _subscribe(self, ws) -> None:
        """Send subscription messages after connect."""
        ...

    @abc.abstractmethod
    async def _handle_message(self, raw: dict) -> None:
        """Parse and dispatch a venue-specific message."""
        ...

    @abc.abstractmethod
    async def _fallback_poll(self) -> None:
        """One REST poll cycle (called while disconnected)."""
        ...

    @abc.abstractmethod
    async def _backfill_gap(self, disconnect_ts: int, reconnect_ts: int) -> None:
        """Fetch missed data from REST after reconnect."""
        ...

    # --- Lifecycle ---

    async def start(self) -> None:
        """Start the WebSocket feed with automatic reconnection."""
        self._running = True
        logger.info("[%s] Starting WebSocket feed: %s", self._name, self._url)

        while self._running:
            try:
                await self._connect_and_run()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[%s] Connection error: %s", self._name, e)

            if not self._running:
                break

            # Disconnected — start fallback + reconnect
            self._connected = False
            self._disconnect_ts = int(time.time())
            logger.warning("[%s] Disconnected, starting fallback polling", self._name)

            # Run fallback polling while attempting reconnect
            fallback_task = asyncio.create_task(self._fallback_loop())
            try:
                await self._reconnect()
            finally:
                fallback_task.cancel()
                try:
                    await fallback_task
                except asyncio.CancelledError:
                    pass

    async def stop(self) -> None:
        """Stop the feed gracefully."""
        self._running = False
        self._connected = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("[%s] Stopped", self._name)

    # --- Internal ---

    async def _connect_and_run(self) -> None:
        """Connect, subscribe, and run the message loop."""
        self._ws = await self._connect_ws()
        self._connected = True
        self._last_message_ts = time.time()
        self._reconnect_attempt = 0

        # Backfill if reconnecting
        if self._disconnect_ts > 0:
            reconnect_ts = int(time.time())
            logger.info("[%s] Reconnected after %ds, backfilling gap",
                       self._name, reconnect_ts - self._disconnect_ts)
            try:
                await self._backfill_gap(self._disconnect_ts, reconnect_ts)
            except Exception as e:
                logger.error("[%s] Backfill failed: %s", self._name, e)
            self._disconnect_ts = 0

        await self._subscribe(self._ws)
        logger.info("[%s] Connected and subscribed", self._name)

        # Run message loop with health monitoring
        health_task = asyncio.create_task(self._health_monitor())
        try:
            await self._message_loop()
        finally:
            health_task.cancel()
            try:
                await health_task
            except asyncio.CancelledError:
                pass

    async def _message_loop(self) -> None:
        """Read and dispatch messages until disconnected."""
        import json
        while self._running and self._connected:
            try:
                raw = await self._ws.recv()
                self._last_message_ts = time.time()
                if isinstance(raw, str):
                    data = json.loads(raw)
                elif isinstance(raw, bytes):
                    data = json.loads(raw.decode())
                else:
                    data = raw
                await self._handle_message(data)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("[%s] Message loop error: %s", self._name, e)
                self._connected = False
                break

    async def _health_monitor(self) -> None:
        """Force reconnect if no messages received within timeout."""
        while self._running and self._connected:
            await asyncio.sleep(5.0)
            if self._is_health_timeout():
                logger.warning("[%s] Health timeout (no message for %.0fs), forcing reconnect",
                             self._name, self._health_timeout_s)
                self._connected = False
                if self._ws:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                break

    def _is_health_timeout(self) -> bool:
        """Check if the health timeout has been exceeded."""
        if not self._connected or self._last_message_ts == 0:
            return False
        return (time.time() - self._last_message_ts) > self._health_timeout_s

    async def _reconnect(self) -> None:
        """Reconnect with exponential backoff."""
        while self._running:
            delay = self._reconnect_delay(self._reconnect_attempt)
            logger.info("[%s] Reconnect attempt %d in %.1fs",
                       self._name, self._reconnect_attempt + 1, delay)
            await asyncio.sleep(delay)
            self._reconnect_attempt += 1

            if not self._running:
                break

            try:
                await self._connect_and_run()
                return  # Success — exit reconnect loop
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[%s] Reconnect failed: %s", self._name, e)

    def _reconnect_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay."""
        return min(1.0 * (2 ** attempt), self._max_reconnect_delay_s)

    async def _fallback_loop(self) -> None:
        """Poll REST endpoints while WebSocket is disconnected."""
        while self._running and not self._connected:
            try:
                await self._fallback_poll()
            except Exception as e:
                logger.error("[%s] Fallback poll error: %s", self._name, e)
            await asyncio.sleep(self._fallback_poll_interval_s)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_websocket_feed.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add flint/providers/websocket.py tests/test_websocket_feed.py
git commit -m "feat: add WebSocketFeed base class with reconnection, health checks, REST fallback"
```

---

### Task 6: DriftWebSocketFeed

**Files:**
- Create: `flint/providers/drift_ws.py`
- Test: `tests/test_drift_ws.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_drift_ws.py`:

```python
"""Tests for DriftWebSocketFeed — mocked, no real connections."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from flint.models import Candle
from flint.providers.drift_ws import DriftWebSocketFeed


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestMessageHandling:
    def test_trade_message_feeds_aggregator(self):
        closed = []
        feed = DriftWebSocketFeed(
            markets=["SOL-PERP"],
            resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
        )
        # Simulate a trade message
        trade_msg = {
            "channel": "trades",
            "market": "SOL-PERP",
            "data": {"price": 150.0, "size": 10.0, "ts": 1000},
        }
        run(feed._handle_message(trade_msg))
        agg = feed._aggregators.get("SOL-PERP")
        assert agg is not None
        bar = agg.current_bar()
        assert bar is not None
        assert bar.open == 150.0

    def test_trade_closes_candle(self):
        closed = []
        feed = DriftWebSocketFeed(
            markets=["SOL-PERP"],
            resolution_s=60,
            on_candle_close=lambda c: closed.append(c),
        )
        run(feed._handle_message({
            "channel": "trades", "market": "SOL-PERP",
            "data": {"price": 150.0, "size": 10.0, "ts": 960},
        }))
        run(feed._handle_message({
            "channel": "trades", "market": "SOL-PERP",
            "data": {"price": 155.0, "size": 5.0, "ts": 1020},
        }))
        assert len(closed) == 1
        assert closed[0].venue == "drift"

    def test_funding_message_persisted(self, tmp_path):
        from flint.store import FlintStore
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        feed = DriftWebSocketFeed(
            markets=["SOL-PERP"],
            resolution_s=60,
            on_candle_close=lambda c: None,
            store=store,
        )
        funding_msg = {
            "channel": "funding",
            "market": "SOL-PERP",
            "data": {"rate": 0.0001, "mark_price": 150.0, "index_price": 149.8, "ts": 1000},
        }
        run(feed._handle_message(funding_msg))
        rates = store.query_venue_funding("drift", "SOL-PERP")
        assert len(rates) == 1
        store.close()

    def test_unknown_channel_ignored(self):
        feed = DriftWebSocketFeed(
            markets=["SOL-PERP"],
            resolution_s=60,
            on_candle_close=lambda c: None,
        )
        # Should not raise
        run(feed._handle_message({"channel": "unknown", "data": {}}))


class TestMultipleMarkets:
    def test_separate_aggregators_per_market(self):
        feed = DriftWebSocketFeed(
            markets=["SOL-PERP", "BTC-PERP"],
            resolution_s=60,
            on_candle_close=lambda c: None,
        )
        run(feed._handle_message({
            "channel": "trades", "market": "SOL-PERP",
            "data": {"price": 150.0, "size": 10.0, "ts": 1000},
        }))
        run(feed._handle_message({
            "channel": "trades", "market": "BTC-PERP",
            "data": {"price": 65000.0, "size": 0.1, "ts": 1000},
        }))
        assert len(feed._aggregators) == 2
        assert feed._aggregators["SOL-PERP"].current_bar().open == 150.0
        assert feed._aggregators["BTC-PERP"].current_bar().open == 65000.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_drift_ws.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create drift_ws.py**

Create `flint/providers/drift_ws.py`:

```python
"""DriftWebSocketFeed — real-time trade and funding data from Drift Protocol.

Subscribes to trade events (aggregated into candles via CandleAggregator)
and funding rate updates. Falls back to REST polling on disconnect.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from ..models import Candle
from .candle_aggregator import CandleAggregator
from .websocket import WebSocketFeed

logger = logging.getLogger("flint.drift_ws")

_DRIFT_WS_URL = "wss://dlob.drift.trade/ws"


class DriftWebSocketFeed(WebSocketFeed):
    """WebSocket feed for Drift Protocol trade and funding data.

    Args:
        markets: List of market symbols to subscribe to.
        resolution_s: Candle aggregation resolution in seconds.
        on_candle_close: Callback fired when a candle bar closes.
        store: Optional FlintStore for persisting candles and funding.
        url: Override WebSocket URL (for testing).
    """

    def __init__(
        self,
        markets: List[str],
        resolution_s: int = 60,
        on_candle_close: Optional[Callable[[Candle], None]] = None,
        store=None,
        url: str = _DRIFT_WS_URL,
        **kwargs,
    ):
        super().__init__(url=url, name="drift", **kwargs)
        self._markets = markets
        self._resolution_s = resolution_s
        self._on_candle_close = on_candle_close or (lambda c: None)
        self._store = store

        # One CandleAggregator per market
        self._aggregators: Dict[str, CandleAggregator] = {}
        for market in markets:
            self._aggregators[market] = CandleAggregator(
                market=market,
                venue="drift",
                resolution_s=resolution_s,
                on_candle_close=self._on_candle_close,
                store=store,
            )

    async def _connect_ws(self):
        """Connect to Drift WebSocket."""
        import websockets
        ws = await websockets.connect(self._url)
        return ws

    async def _subscribe(self, ws) -> None:
        """Subscribe to trade and funding channels for all markets."""
        import json
        for market in self._markets:
            await ws.send(json.dumps({
                "type": "subscribe",
                "channel": "trades",
                "market": market,
            }))
            await ws.send(json.dumps({
                "type": "subscribe",
                "channel": "funding",
                "market": market,
            }))
        logger.info("Subscribed to %d markets", len(self._markets))

    async def _handle_message(self, raw: dict) -> None:
        """Dispatch message to appropriate handler."""
        channel = raw.get("channel", "")
        if channel == "trades":
            self._handle_trade(raw)
        elif channel == "funding":
            self._handle_funding(raw)

    def _handle_trade(self, msg: dict) -> None:
        """Feed trade into the appropriate CandleAggregator."""
        market = msg.get("market", "")
        data = msg.get("data", {})
        agg = self._aggregators.get(market)
        if agg is None:
            return
        price = float(data.get("price", 0))
        size = float(data.get("size", 0))
        ts = int(data.get("ts", 0))
        if price > 0 and size > 0 and ts > 0:
            agg.process_trade(price, size, ts)

    def _handle_funding(self, msg: dict) -> None:
        """Persist funding rate update to store."""
        if not self._store:
            return
        market = msg.get("market", "")
        data = msg.get("data", {})
        rate = float(data.get("rate", 0))
        mark_price = float(data.get("mark_price", 0))
        index_price = float(data.get("index_price", 0))
        ts = int(data.get("ts", 0))
        if ts > 0:
            try:
                self._store.upsert_venue_funding([{
                    "venue": "drift",
                    "market": market,
                    "ts": ts,
                    "rate_hourly": rate,
                    "mark_price": mark_price,
                    "index_price": index_price,
                }])
            except Exception as e:
                logger.error("Failed to persist funding: %s", e)

    async def _fallback_poll(self) -> None:
        """Poll Drift REST API for latest candles while WS disconnected."""
        import asyncio
        import time as _time
        try:
            from .drift_candles import DriftCandleProvider
            provider = DriftCandleProvider()
            now = int(_time.time())
            for market in self._markets:
                candles = await asyncio.to_thread(
                    provider.fetch_candles,
                    market, self._resolution_s,
                    start_ts=now - self._resolution_s * 3,
                    end_ts=now,
                )
                for candle in candles:
                    agg = self._aggregators.get(market)
                    if agg and candle.ts > (agg.current_bar() or candle).ts:
                        self._on_candle_close(candle)
                        if self._store:
                            self._store.upsert_candles([candle])
        except Exception as e:
            logger.error("Drift fallback poll failed: %s", e)

    async def _backfill_gap(self, disconnect_ts: int, reconnect_ts: int) -> None:
        """Backfill missed candles from REST after reconnect."""
        import asyncio
        try:
            from .drift_candles import DriftCandleProvider
            provider = DriftCandleProvider()
            for market in self._markets:
                candles = await asyncio.to_thread(
                    provider.fetch_candles,
                    market, self._resolution_s,
                    start_ts=disconnect_ts,
                    end_ts=reconnect_ts,
                )
                if candles and self._store:
                    self._store.upsert_candles(candles)
                logger.info("Backfilled %d candles for %s", len(candles), market)
        except Exception as e:
            logger.error("Drift backfill failed: %s", e)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_drift_ws.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add flint/providers/drift_ws.py tests/test_drift_ws.py
git commit -m "feat: add DriftWebSocketFeed with trade aggregation and funding persistence"
```

---

### Task 7: PythWebSocketFeed

**Files:**
- Create: `flint/providers/pyth_ws.py`
- Test: `tests/test_pyth_ws.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_pyth_ws.py`:

```python
"""Tests for PythWebSocketFeed — mocked, no real connections."""
import asyncio
import time
import pytest

from flint.providers.pyth_ws import PythWebSocketFeed


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestPriceCache:
    def test_price_update_cached(self):
        feed = PythWebSocketFeed(markets=["SOL-PERP", "BTC-PERP"])
        run(feed._handle_message({
            "type": "price_update",
            "pair": "SOL/USD",
            "price": 150.25,
            "confidence": 0.05,
            "ts": 1000,
        }))
        result = feed.get_price("SOL-PERP")
        assert result is not None
        price, ts = result
        assert price == 150.25
        assert ts == 1000

    def test_unknown_pair_ignored(self):
        feed = PythWebSocketFeed(markets=["SOL-PERP"])
        run(feed._handle_message({
            "type": "price_update",
            "pair": "UNKNOWN/USD",
            "price": 1.0,
            "confidence": 0.01,
            "ts": 1000,
        }))
        assert feed.get_price("UNKNOWN-PERP") is None

    def test_multiple_updates_latest_wins(self):
        feed = PythWebSocketFeed(markets=["SOL-PERP"])
        run(feed._handle_message({
            "type": "price_update", "pair": "SOL/USD",
            "price": 150.0, "confidence": 0.05, "ts": 1000,
        }))
        run(feed._handle_message({
            "type": "price_update", "pair": "SOL/USD",
            "price": 151.0, "confidence": 0.04, "ts": 1001,
        }))
        price, ts = feed.get_price("SOL-PERP")
        assert price == 151.0
        assert ts == 1001

    def test_get_all_prices(self):
        feed = PythWebSocketFeed(markets=["SOL-PERP", "BTC-PERP"])
        run(feed._handle_message({
            "type": "price_update", "pair": "SOL/USD",
            "price": 150.0, "confidence": 0.05, "ts": 1000,
        }))
        run(feed._handle_message({
            "type": "price_update", "pair": "BTC/USD",
            "price": 65000.0, "confidence": 10.0, "ts": 1000,
        }))
        prices = feed.get_all_prices()
        assert "SOL-PERP" in prices
        assert "BTC-PERP" in prices


class TestBatchPersistence:
    def test_persist_prices_to_store(self, tmp_path):
        from flint.store import FlintStore
        store = FlintStore(path=str(tmp_path / "test.duckdb"))
        feed = PythWebSocketFeed(
            markets=["SOL-PERP"],
            store=store,
            batch_interval_s=0,  # persist immediately for testing
        )
        run(feed._handle_message({
            "type": "price_update", "pair": "SOL/USD",
            "price": 150.0, "confidence": 0.05, "ts": 1000,
        }))
        feed._flush_to_store()
        prices = store.query_oracle_prices("SOL-PERP")
        assert len(prices) == 1
        assert prices[0].price == 150.0
        store.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pyth_ws.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create pyth_ws.py**

Create `flint/providers/pyth_ws.py`:

```python
"""PythWebSocketFeed — real-time oracle prices from Pyth Hermes.

Streams sub-second price updates and maintains an in-memory price cache.
Batch-persists to FlintStore every batch_interval_s seconds.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

from ..models import OraclePrice
from .websocket import WebSocketFeed

logger = logging.getLogger("flint.pyth_ws")

_PYTH_WS_URL = "wss://hermes.pyth.network/ws"

# Map market symbols to Pyth pair format
_MARKET_TO_PAIR: Dict[str, str] = {
    "SOL-PERP": "SOL/USD", "BTC-PERP": "BTC/USD", "ETH-PERP": "ETH/USD",
    "DOGE-PERP": "DOGE/USD", "SUI-PERP": "SUI/USD", "ARB-PERP": "ARB/USD",
    "LINK-PERP": "LINK/USD", "AVAX-PERP": "AVAX/USD", "XRP-PERP": "XRP/USD",
    "WIF-PERP": "WIF/USD", "JUP-PERP": "JUP/USD", "INJ-PERP": "INJ/USD",
    "RENDER-PERP": "RENDER/USD", "OP-PERP": "OP/USD", "TIA-PERP": "TIA/USD",
    "SEI-PERP": "SEI/USD", "BNB-PERP": "BNB/USD", "DRIFT-PERP": "DRIFT/USD",
    "PYTH-PERP": "PYTH/USD", "1MBONK-PERP": "BONK/USD",
}
_PAIR_TO_MARKET = {v: k for k, v in _MARKET_TO_PAIR.items()}


class PythWebSocketFeed(WebSocketFeed):
    """WebSocket feed for Pyth Hermes oracle price updates.

    Args:
        markets: List of market symbols to track (e.g. ["SOL-PERP", "BTC-PERP"]).
        store: Optional FlintStore for batch-persisting prices.
        batch_interval_s: How often to flush price cache to store (default 10s).
        url: Override WebSocket URL (for testing).
    """

    def __init__(
        self,
        markets: List[str],
        store=None,
        batch_interval_s: float = 10.0,
        url: str = _PYTH_WS_URL,
        **kwargs,
    ):
        super().__init__(url=url, name="pyth", **kwargs)
        self._markets = markets
        self._store = store
        self._batch_interval_s = batch_interval_s

        # In-memory price cache: market → (price, ts)
        self._price_cache: Dict[str, Tuple[float, int]] = {}
        # Buffer for batch persistence
        self._pending_prices: List[OraclePrice] = []
        self._last_flush_ts: float = 0.0

        # Resolve which Pyth pairs we need
        self._pairs: Dict[str, str] = {}  # pair → market
        for market in markets:
            pair = _MARKET_TO_PAIR.get(market)
            if pair:
                self._pairs[pair] = market

    def get_price(self, market: str) -> Optional[Tuple[float, int]]:
        """Get the latest cached price for a market. Returns (price, ts) or None."""
        return self._price_cache.get(market)

    def get_all_prices(self) -> Dict[str, Tuple[float, int]]:
        """Get all cached prices. Returns {market: (price, ts)}."""
        return dict(self._price_cache)

    async def _connect_ws(self):
        """Connect to Pyth Hermes WebSocket."""
        import websockets
        ws = await websockets.connect(self._url)
        return ws

    async def _subscribe(self, ws) -> None:
        """Subscribe to price feeds for configured markets."""
        import json
        from .pyth import FEED_IDS
        feed_ids = []
        for pair in self._pairs:
            fid = FEED_IDS.get(pair)
            if fid:
                feed_ids.append(fid)
        if feed_ids:
            await ws.send(json.dumps({
                "type": "subscribe",
                "ids": feed_ids,
            }))
        logger.info("Subscribed to %d Pyth price feeds", len(feed_ids))

    async def _handle_message(self, raw: dict) -> None:
        """Parse price update and update cache."""
        msg_type = raw.get("type", "")
        if msg_type != "price_update":
            return

        pair = raw.get("pair", "")
        market = self._pairs.get(pair) or _PAIR_TO_MARKET.get(pair)
        if not market:
            return

        price = float(raw.get("price", 0))
        ts = int(raw.get("ts", 0))
        if price <= 0 or ts <= 0:
            return

        self._price_cache[market] = (price, ts)

        # Buffer for batch persistence
        self._pending_prices.append(OraclePrice(market=market, ts=ts, price=price))

        # Flush to store if interval elapsed
        now = time.time()
        if self._store and (now - self._last_flush_ts) >= self._batch_interval_s:
            self._flush_to_store()

    def _flush_to_store(self) -> None:
        """Write buffered prices to FlintStore."""
        if not self._store or not self._pending_prices:
            return
        try:
            self._store.upsert_oracle_prices(self._pending_prices)
            logger.debug("Flushed %d oracle prices to store", len(self._pending_prices))
        except Exception as e:
            logger.error("Failed to flush oracle prices: %s", e)
        self._pending_prices.clear()
        self._last_flush_ts = time.time()

    async def _fallback_poll(self) -> None:
        """Poll Pyth REST API while WS disconnected."""
        import asyncio
        try:
            from .pyth import PythProvider
            provider = PythProvider()
            pairs = list(self._pairs.keys())
            prices = await asyncio.to_thread(provider.fetch_prices, pairs)
            for p in prices:
                market = _PAIR_TO_MARKET.get(p.get("pair", ""))
                if market and p.get("price"):
                    self._price_cache[market] = (p["price"], p.get("ts", int(time.time())))
        except Exception as e:
            logger.error("Pyth fallback poll failed: %s", e)

    async def _backfill_gap(self, disconnect_ts: int, reconnect_ts: int) -> None:
        """No backfill needed for oracle prices (point-in-time, not cumulative)."""
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pyth_ws.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add flint/providers/pyth_ws.py tests/test_pyth_ws.py
git commit -m "feat: add PythWebSocketFeed with price cache and batch store persistence"
```

---

### Task 8: Event-driven tick mode in LiveExecutionContext

**Files:**
- Modify: `flint/execution/live_base.py`
- Modify: `flint/execution/context.py`
- Test: `tests/test_live_base.py`

- [ ] **Step 1: Write the tests**

Add to `tests/test_live_base.py`:

```python
class TestEventDrivenTick:
    def test_candle_queue_triggers_tick(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx._tick_mode = "on_candle_close"
        ctx._tick_markets = ["SOL-PERP"]
        mock_strategy = MagicMock()
        candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                        close=150.5, volume=1000.0, market="SOL-PERP",
                        resolution_s=60, venue="drift")

        async def test():
            ctx._candle_queue = asyncio.Queue()
            ctx._running = True
            # Put a candle then stop
            ctx._candle_queue.put_nowait(candle)
            # Run one iteration of event-driven loop
            try:
                got = await asyncio.wait_for(ctx._candle_queue.get(), timeout=1.0)
                ctx._current_candle = got
                ctx._tick_count += 1
                await ctx._tick(mock_strategy, "SOL-PERP")
            except asyncio.TimeoutError:
                pass
        run(test())
        mock_strategy.on_candle.assert_called_once()
        assert ctx._current_candle == candle

    def test_on_ws_candle_filters_by_tick_markets(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx._tick_mode = "on_candle_close"
        ctx._tick_markets = ["SOL-PERP"]
        ctx._candle_queue = asyncio.Queue()

        sol_candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                           close=150.5, volume=1000.0, market="SOL-PERP",
                           resolution_s=60, venue="drift")
        btc_candle = Candle(ts=1000, open=65000.0, high=65100.0, low=64900.0,
                           close=65050.0, volume=10.0, market="BTC-PERP",
                           resolution_s=60, venue="drift")

        ctx._on_ws_candle(sol_candle)  # should enqueue
        ctx._on_ws_candle(btc_candle)  # should NOT enqueue

        assert ctx._candle_queue.qsize() == 1

    def test_venue_specific_tick_markets(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        ctx._tick_mode = "on_candle_close"
        ctx._tick_markets = ["drift:SOL-PERP"]
        ctx._candle_queue = asyncio.Queue()

        drift_candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                             close=150.5, volume=1000.0, market="SOL-PERP",
                             resolution_s=60, venue="drift")
        hl_candle = Candle(ts=1000, open=150.0, high=151.0, low=149.0,
                          close=150.5, volume=1000.0, market="SOL-PERP",
                          resolution_s=60, venue="hyperliquid")

        ctx._on_ws_candle(drift_candle)  # should enqueue (matches drift:SOL-PERP)
        ctx._on_ws_candle(hl_candle)     # should NOT enqueue

        assert ctx._candle_queue.qsize() == 1


class TestOraclePrice:
    def test_get_oracle_price_default_none(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        assert ctx.get_oracle_price("SOL-PERP") is None

    def test_get_oracle_price_from_pyth_feed(self):
        ctx = MockVenueContext(venue="test", initial_capital=10000.0)
        # Simulate pyth feed attached
        mock_pyth = MagicMock()
        mock_pyth.get_price.return_value = (150.25, 1000)
        ctx._pyth_feed = mock_pyth
        result = ctx.get_oracle_price("SOL-PERP")
        assert result == (150.25, 1000)
```

Also add these imports at the top of `tests/test_live_base.py` if missing:
```python
import asyncio
from unittest.mock import MagicMock
from flint.models import Candle
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_live_base.py::TestEventDrivenTick -v`
Expected: FAIL — `AttributeError: '_tick_mode'` etc.

- [ ] **Step 3: Modify live_base.py**

In `flint/execution/live_base.py`, make these changes:

**Add to `__init__` parameters** (after `limit_order_timeout_bars`):
```python
        tick_mode: str = "on_candle_close",
        tick_markets: Optional[List[str]] = None,
```

**Add to `__init__` body** (after `self._limit_order_timeout_bars = ...`):
```python
        self._tick_mode = tick_mode
        self._tick_markets = tick_markets or []
        self._candle_queue: asyncio.Queue = asyncio.Queue()
        self._pyth_feed = None  # Set externally when PythWebSocketFeed is attached
```

**Replace the `run()` method** with:
```python
    async def run(self, strategy, market: str, feeds=None, fetch_candle=None) -> None:
        """Run the strategy tick loop.

        Args:
            strategy: Strategy instance with on_candle(ctx) method.
            market: Primary market symbol (e.g. "SOL-PERP").
            feeds: Optional list of WebSocketFeed instances to start.
            fetch_candle: Optional async callable() -> Candle for timer mode fallback.
        """
        self._running = True
        self._tick_count = 0
        self._candle_queue = asyncio.Queue()

        # Default tick_markets to primary market
        if not self._tick_markets:
            self._tick_markets = [market]

        logger.info("Starting %s tick loop (market=%s, tick_markets=%s)",
                     self._tick_mode, market, self._tick_markets)

        # Start WebSocket feeds
        feed_tasks = []
        if feeds:
            for feed in feeds:
                feed_tasks.append(asyncio.create_task(feed.start()))

        # Start order polling
        poll_task = asyncio.create_task(self._poll_orders_loop())

        try:
            if self._tick_mode == "on_candle_close":
                await self._run_event_driven(strategy, market, fetch_candle)
            else:
                await self._run_timer(strategy, market, fetch_candle)
        finally:
            for task in feed_tasks:
                task.cancel()
            poll_task.cancel()
            for task in feed_tasks + [poll_task]:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            logger.info("Tick loop stopped after %d ticks", self._tick_count)

    async def _run_event_driven(self, strategy, market: str, fetch_candle=None) -> None:
        """Event-driven loop: ticks on candle close events from WebSocket feeds."""
        while self._running:
            try:
                candle = await asyncio.wait_for(
                    self._candle_queue.get(),
                    timeout=self._tick_interval_s * 2,
                )
                self._current_candle = candle
                self._tick_count += 1
                await self._tick(strategy, market)
            except asyncio.TimeoutError:
                # No candle from WS — fallback to REST
                logger.debug("No WS candle within timeout, falling back to REST")
                self._tick_count += 1
                await self._tick(strategy, market, fetch_candle)

    async def _run_timer(self, strategy, market: str, fetch_candle=None) -> None:
        """Timer-based loop: ticks at fixed intervals (original behavior)."""
        while self._running:
            self._tick_count += 1
            try:
                await self._tick(strategy, market, fetch_candle)
            except Exception as e:
                logger.error("Tick %d failed: %s", self._tick_count, e)
            await asyncio.sleep(self._tick_interval_s)

    def _on_ws_candle(self, candle: Candle) -> None:
        """Called by CandleAggregator when a bar closes. Enqueues if market is in tick_markets."""
        venue_market = f"{candle.venue}:{candle.market}"
        if candle.market in self._tick_markets or venue_market in self._tick_markets:
            self._current_candle = candle
            self._candle_queue.put_nowait(candle)
```

**Add `get_oracle_price` method** (in the ExecutionContext interface section):
```python
    def get_oracle_price(self, market: Optional[str] = None) -> Optional[Tuple[float, int]]:
        """Get latest oracle price. Returns (price, ts) or None."""
        if self._pyth_feed:
            mkt = market or (self._current_candle.market if self._current_candle else None)
            if mkt:
                return self._pyth_feed.get_price(mkt)
        # Fallback to store
        if self._store and market:
            try:
                prices = self._store.query_oracle_prices(market)
                if prices:
                    return (prices[-1].price, prices[-1].ts)
            except Exception:
                pass
        return None
```

**Add `get_oracle_price` to `context.py` ABC** (at end of class, before `log()`):
```python
    def get_oracle_price(self, market: Optional[str] = None) -> Optional[tuple]:
        """Get latest oracle price for a market. Returns (price, timestamp) or None."""
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_live_base.py -v`
Expected: PASS (all tests including new ones)

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q --timeout=120`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add flint/execution/live_base.py flint/execution/context.py tests/test_live_base.py
git commit -m "feat: add event-driven tick mode, candle queue, oracle price access"
```

---

### Task 9: Update ROADMAP.md

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Add implementation notes to §1.3**

After the existing §1.3 content (around the "Note" line about WebSocket being the biggest new subsystem), add:

```markdown
**Implemented (Sub-project 2):**
- [x] `WebSocketFeed` base class with reconnection, health checks, REST fallback (`flint/providers/websocket.py`)
- [x] `CandleAggregator` — raw trades → OHLCV candle bars (`flint/providers/candle_aggregator.py`)
- [x] `DriftWebSocketFeed` — trade streaming + funding rate subscription (`flint/providers/drift_ws.py`)
- [x] `PythWebSocketFeed` — sub-second oracle prices with batch persistence (`flint/providers/pyth_ws.py`)
- [x] Event-driven tick mode (`on_candle_close`) replacing timer-based ticking
- [x] `venue` field on `Candle` dataclass for multi-venue support
- [x] `tick_markets` config for controlling which markets trigger strategy ticks
- [x] `get_oracle_price()` convenience method on ExecutionContext
```

- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: update ROADMAP.md with Phase 1.3 WebSocket feeds implementation notes"
```

---

### Task 10: Integration test — WebSocket feeds end-to-end

**Files:**
- Create: `tests/test_ws_integration.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_ws_integration.py`:

```python
"""Integration test: WebSocket feeds → CandleAggregator → event-driven tick loop."""
import asyncio
import time
import pytest
from unittest.mock import MagicMock

from flint.models import Candle, OrderState, Side
from flint.execution.live_base import LiveExecutionContext
from flint.providers.candle_aggregator import CandleAggregator
from flint.providers.pyth_ws import PythWebSocketFeed


class MockVenueForWS(LiveExecutionContext):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
    async def _connect(self): pass
    async def _disconnect(self): pass
    async def _place_order(self, order): return ("tx_1", 1)
    async def _cancel_order(self, venue_order_id): return True
    async def _fetch_positions(self): return []
    async def _fetch_balance(self): return 10000.0
    async def _poll_order_status(self, venue_order_id): return OrderState.CONFIRMED


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestCandleAggregatorToTickLoop:
    def test_aggregator_candle_triggers_strategy_tick(self):
        """Full pipeline: trade → aggregator → on_ws_candle → queue → tick."""
        ctx = MockVenueForWS(
            venue="test", initial_capital=10000.0,
            tick_mode="on_candle_close", tick_markets=["SOL-PERP"],
        )
        mock_strategy = MagicMock()

        # Create aggregator that feeds into ctx._on_ws_candle
        agg = CandleAggregator(
            market="SOL-PERP", venue="drift", resolution_s=60,
            on_candle_close=ctx._on_ws_candle,
        )

        # Feed trades that close a bar
        agg.process_trade(price=150.0, size=10.0, ts=0)
        agg.process_trade(price=152.0, size=5.0, ts=30)
        agg.process_trade(price=153.0, size=8.0, ts=60)  # closes bar at ts=0

        # Queue should have one candle
        assert ctx._candle_queue.qsize() == 1

        # Process it
        async def process():
            candle = await asyncio.wait_for(ctx._candle_queue.get(), timeout=1.0)
            ctx._current_candle = candle
            ctx._tick_count += 1
            await ctx._tick(mock_strategy, "SOL-PERP")

        run(process())
        mock_strategy.on_candle.assert_called_once()
        assert ctx._current_candle.venue == "drift"
        assert ctx._current_candle.close == 152.0


class TestPythFeedIntegration:
    def test_oracle_price_accessible_via_context(self):
        feed = PythWebSocketFeed(markets=["SOL-PERP"])
        run(feed._handle_message({
            "type": "price_update", "pair": "SOL/USD",
            "price": 150.25, "confidence": 0.05, "ts": 1000,
        }))

        ctx = MockVenueForWS(
            venue="test", initial_capital=10000.0,
        )
        ctx._pyth_feed = feed
        result = ctx.get_oracle_price("SOL-PERP")
        assert result == (150.25, 1000)
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_ws_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -x -q --timeout=120`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_ws_integration.py
git commit -m "test: add end-to-end integration tests for WebSocket feeds pipeline"
```

---

## File Map

| Action | File | Task |
|--------|------|------|
| Modify | `flint/models.py` | 1 |
| Modify | `flint/config.py` | 2 |
| Modify | `setup.py` / `pyproject.toml` | 3 |
| Create | `flint/providers/candle_aggregator.py` | 4 |
| Create | `flint/providers/websocket.py` | 5 |
| Create | `flint/providers/drift_ws.py` | 6 |
| Create | `flint/providers/pyth_ws.py` | 7 |
| Modify | `flint/execution/live_base.py` | 8 |
| Modify | `flint/execution/context.py` | 8 |
| Modify | `ROADMAP.md` | 9 |
| Create | `tests/test_models.py` (add class) | 1 |
| Create | `tests/test_config.py` (add class) | 2 |
| Create | `tests/test_candle_aggregator.py` | 4 |
| Create | `tests/test_websocket_feed.py` | 5 |
| Create | `tests/test_drift_ws.py` | 6 |
| Create | `tests/test_pyth_ws.py` | 7 |
| Modify | `tests/test_live_base.py` (add classes) | 8 |
| Create | `tests/test_ws_integration.py` | 10 |

## Dependency Order

```
Task 1 (Candle venue field)
Task 2 (config)             ──→ all independent, can run in parallel
Task 3 (websockets dep)

Task 4 (CandleAggregator)   ──→ depends on Task 1 (venue field)
Task 5 (WebSocketFeed base) ──→ depends on Task 3 (websockets)
Task 6 (DriftWebSocketFeed) ──→ depends on Tasks 4 + 5
Task 7 (PythWebSocketFeed)  ──→ depends on Task 5

Task 8 (event-driven tick)  ──→ depends on Tasks 4 + 7 (uses CandleAggregator + PythFeed)
Task 9 (ROADMAP update)     ──→ after all implementation
Task 10 (integration test)  ──→ after Tasks 6 + 7 + 8
```

Tasks 1-3 can run in parallel. Task 4 needs Task 1. Task 5 needs Task 3. Tasks 6-7 need 4+5. Task 8 needs 4+7. Tasks 9-10 come last.
