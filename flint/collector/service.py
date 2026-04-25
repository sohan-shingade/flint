"""Main collector service -- async loop with scheduling and status tracking."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional

from ..store import FlintStore
from .tasks import (
    CollectorConfig,
    collect_oracle_prices,
    collect_orderbook,
    collect_candles_backfill,
    collect_candles_latest,
)

logger = logging.getLogger("flint.collector")


def _config_from_flint(flint_config) -> CollectorConfig:
    """Build a CollectorConfig from a FlintConfig instance."""
    return CollectorConfig(
        markets=list(flint_config.default_markets),
        candle_backfill_days=flint_config.candle_backfill_days,
        candle_interval_s=flint_config.candle_interval_s,
        funding_interval_s=flint_config.funding_interval_s,
        orderbook_interval_s=flint_config.orderbook_interval_s,
        oracle_interval_s=flint_config.oracle_interval_s,
    )


class CollectorService:
    def __init__(self, store: FlintStore, config=None):
        self.store = store
        if config is not None and not isinstance(config, CollectorConfig):
            # Accept FlintConfig and convert
            self.config = _config_from_flint(config)
        else:
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
        max_retries = 2
        while retries < max_retries:
            try:
                count = await asyncio.to_thread(task_fn)
                self.status[(market, data_type)]["row_count"] += count
                self.update_status(market, data_type, "idle")
                return
            except Exception as e:
                retries += 1
                if retries >= max_retries:
                    logger.warning("Collection failed for %s/%s: %s", market, data_type, e)
                    self.update_status(market, data_type, "error", error_message=str(e))
                    return
                wait = 5
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
        last_orderbook = 0.0
        last_candles = 0.0

        while self._running:
            now = time.time()
            for market in self.config.markets:
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
                if now - last_candles >= self.config.candle_interval_s:
                    await self._run_task(
                        market, "candles",
                        lambda m=market: collect_candles_latest(self.store, m),
                    )
                # Funding rates are fetched when market data is downloaded
                # via _download_funding_all_venues(), not by the collector.

            if now - last_oracle >= self.config.oracle_interval_s:
                last_oracle = now
            if now - last_orderbook >= self.config.orderbook_interval_s:
                last_orderbook = now
            if now - last_candles >= self.config.candle_interval_s:
                last_candles = now
            await asyncio.sleep(10)

    def stop(self) -> None:
        self._running = False
