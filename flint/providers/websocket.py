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

            self._connected = False
            self._disconnect_ts = int(time.time())
            logger.warning("[%s] Disconnected, starting fallback polling", self._name)

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
                return
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
