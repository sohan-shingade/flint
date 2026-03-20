"""Main collector service -- async loop with scheduling and status tracking."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional

from ..store import FlintStore
from .tasks import (
    CollectorConfig,
    MARKET_INDEX,
    collect_oracle_prices,
    collect_funding_rates,
    collect_orderbook,
    collect_candles_backfill,
)

logger = logging.getLogger("flint.collector")

write_lock = asyncio.Lock()


class CollectorService:
    def __init__(self, store: FlintStore, config: Optional[CollectorConfig] = None):
        self.store = store
        self.config = config or CollectorConfig()
        self.status: Dict = {}
        self._running = False

    def update_status(
        self,
        market: str,
        data_type: str,
        state: str,
        error_message: Optional[str] = None,
        progress_pct: Optional[float] = None,
    ) -> None:
        key = (market, data_type)
        if key not in self.status:
            self.status[key] = {
                "market": market,
                "data_type": data_type,
                "state": "idle",
                "last_updated": None,
                "row_count": 0,
                "error_message": None,
                "progress_pct": None,
            }
        self.status[key]["state"] = state
        if error_message is not None:
            self.status[key]["error_message"] = error_message
        if progress_pct is not None:
            self.status[key]["progress_pct"] = progress_pct
        if state == "idle":
            self.status[key]["last_updated"] = int(time.time())
            self.status[key]["error_message"] = None

    def get_status(self) -> List:
        return list(self.status.values())

    async def _run_task(self, market: str, data_type: str, task_fn: Callable) -> None:
        """Run a sync collection task in a thread with retry."""
        self.update_status(market, data_type, "collecting")
        retries = 0
        max_retries = 5
        while retries < max_retries:
            try:
                async with write_lock:
                    count = await asyncio.to_thread(task_fn)
                self.status[(market, data_type)]["row_count"] += count
                self.update_status(market, data_type, "idle")
                return
            except Exception as e:
                retries += 1
                if retries >= max_retries:
                    logger.error("Collection failed for %s/%s: %s", market, data_type, e)
                    self.update_status(market, data_type, "error", error_message=str(e))
                    return
                wait = min(5 * (2 ** (retries - 1)), 300)
                logger.warning(
                    "Retry %d/%d for %s/%s in %ds: %s",
                    retries, max_retries, market, data_type, wait, e,
                )
                await asyncio.sleep(wait)

    def _needs_backfill(self) -> bool:
        count = self.store.count_candles("SOL-PERP", 3600)
        if count == 0:
            return True
        candles = self.store.query_candles("SOL-PERP", 3600)
        if not candles:
            return True
        newest_ts = max(c.ts for c in candles)
        age_hours = (time.time() - newest_ts) / 3600
        return age_hours > 2

    async def backfill(self) -> None:
        total = len(self.config.markets)
        for i, market in enumerate(self.config.markets):
            pct = (i / total) * 100
            self.update_status(market, "candles", "backfilling", progress_pct=pct)
            try:
                async with write_lock:
                    count = await asyncio.to_thread(
                        collect_candles_backfill, self.store, market, self.config.candle_backfill_days
                    )
                self.status[(market, "candles")]["row_count"] = count
                self.update_status(market, "candles", "idle", progress_pct=100.0)
            except Exception as e:
                logger.error("Backfill failed for %s: %s", market, e)
                self.update_status(market, "candles", "error", error_message=str(e))

    async def run(self) -> None:
        self._running = True
        logger.info("Collector service starting")

        if self._needs_backfill():
            logger.info("Database empty or stale -- starting backfill")
            await self.backfill()

        last_oracle = 0.0
        last_funding = 0.0
        last_orderbook = 0.0

        while self._running:
            now = time.time()
            for market in self.config.markets:
                idx = MARKET_INDEX.get(market, 0)
                if now - last_oracle >= self.config.oracle_interval_s:
                    await self._run_task(
                        market, "oracle",
                        lambda m=market: collect_oracle_prices(self.store, m),
                    )
                if now - last_orderbook >= self.config.orderbook_interval_s:
                    await self._run_task(
                        market, "orderbook",
                        lambda m=market: collect_orderbook(self.store, m),
                    )
                if now - last_funding >= self.config.funding_interval_s:
                    await self._run_task(
                        market, "funding",
                        lambda m=market, i=idx: collect_funding_rates(self.store, m, i),
                    )

            if now - last_oracle >= self.config.oracle_interval_s:
                last_oracle = now
            if now - last_orderbook >= self.config.orderbook_interval_s:
                last_orderbook = now
            if now - last_funding >= self.config.funding_interval_s:
                last_funding = now
            await asyncio.sleep(10)

    def stop(self) -> None:
        self._running = False
