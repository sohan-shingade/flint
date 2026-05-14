"""DuckDB-backed candle store.

Thread-safe: all connection access is serialized through a threading.Lock.
This allows the store to be shared between the API main thread, the collector
background thread, and backtest worker threads without DuckDB lock conflicts.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, List, Optional

import duckdb

from ..models import BorrowSnapshot, Candle, FundingRate, OraclePrice
from . import schema

if TYPE_CHECKING:  # pragma: no cover
    from ..models import (
        DexVolume,
        Liquidation,
        OpenInterest,
        OrderbookSnapshot,
        SyncMetadata,
        WhaleTransfer,
    )

_logger = logging.getLogger("flint.store")


class FlintStore:
    """Thread-safe DuckDB store.

    All operations acquire ``_lock`` before touching ``_conn``.
    Safe to share one instance across multiple threads.
    """

    def __init__(self, path: str = ""):
        if not path:
            import os
            data_dir = os.path.expanduser("~/.flint")
            os.makedirs(data_dir, exist_ok=True)
            path = os.path.join(data_dir, "data.duckdb")
        self._path = path
        self._lock = threading.Lock()
        self._conn = duckdb.connect(path)
        schema.create_tables(self._conn)

    # -- candles ---------------------------------------------------------------

    def upsert_candles(self, candles: List[Candle]) -> int:
        if not candles:
            return 0
        rows = [
            (c.venue or "pyth", c.market, c.resolution_s, c.ts, c.open, c.high, c.low, c.close, c.volume)
            for c in candles
        ]
        batch_size = 2000
        with self._lock:
            self._conn.execute("BEGIN TRANSACTION")
            try:
                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i + batch_size]
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO candles "
                        "(venue, market, resolution_s, ts, open, high, low, close, volume) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        batch,
                    )
                self._conn.execute("COMMIT")
            except duckdb.Error:
                self._conn.execute("ROLLBACK")
                raise
        return len(rows)

    def query_candles(
        self,
        market: str,
        resolution_s: int,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: Optional[int] = None,
        venue: Optional[str] = None,
    ) -> List[Candle]:
        sql = (
            "SELECT market, resolution_s, ts, open, high, low, close, volume, venue "
            "FROM candles WHERE market = ? AND resolution_s = ?"
        )
        params: list = [market, resolution_s]
        if venue is not None:
            sql += " AND venue = ?"
            params.append(venue)
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        if limit is not None and start_ts is None:
            sql += " ORDER BY ts DESC LIMIT ?"
            params.append(limit)
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
            rows = rows[::-1]  # reverse to chronological order
        else:
            sql += " ORDER BY ts ASC"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
        return [
            Candle(market=r[0], resolution_s=r[1], ts=r[2], open=r[3],
                   high=r[4], low=r[5], close=r[6], volume=r[7],
                   venue=r[8] if len(r) > 8 else "pyth")
            for r in rows
        ]

    def count_candles(self, market: str, resolution_s: int, venue: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) FROM candles WHERE market = ? AND resolution_s = ?"
        params: list = [market, resolution_s]
        if venue is not None:
            sql += " AND venue = ?"
            params.append(venue)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return row[0] if row else 0

    def has_candles(self) -> bool:
        """Check if any candle data exists."""
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM candles LIMIT 1").fetchone()
            return row is not None

    def get_markets_needing_pyth_migration(self) -> list:
        """Return market names that have non-Pyth candles but no Pyth candles.

        These are candidates for back-filling with Pyth oracle price data.
        """
        with self._lock:
            rows = self._conn.execute("""
                SELECT DISTINCT market FROM candles
                WHERE venue != 'pyth'
                AND market NOT IN (
                    SELECT DISTINCT market FROM candles WHERE venue = 'pyth'
                )
            """).fetchall()
        return [r[0] for r in rows]

    def get_market_date_range(self, market: str):
        """Return (min_ts, max_ts) for all candles of a market, or None if no data."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT MIN(ts), MAX(ts) FROM candles WHERE market = ?", [market]
            ).fetchall()
        if rows and rows[0][0] is not None:
            return (rows[0][0], rows[0][1])
        return None

    def query_candles_with_fallback(
        self,
        market: str,
        resolution_s: int,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> List[Candle]:
        """Query candles preferring venue='pyth'; falls back to any venue if none found."""
        candles = self.query_candles(market, resolution_s, start_ts, end_ts, venue="pyth")
        if candles:
            return candles
        return self.query_candles(market, resolution_s, start_ts, end_ts)

    # -- funding rates ---------------------------------------------------------

    def upsert_funding_rates(self, rates: List[FundingRate]) -> int:
        """Insert funding rates into venue_funding_rates table.
        Backward-compatible: converts FundingRate objects to venue format.
        """
        if not rates:
            return 0
        rows = [
            (r.source if r.source else 'drift', r.market, r.ts, r.rate, r.mark_price, r.oracle_price)
            for r in rates
            if abs(r.rate) < 0.005  # reject corrupted rates
        ]
        if not rows:
            return 0
        batch_size = 2000
        with self._lock:
            self._conn.execute("BEGIN TRANSACTION")
            try:
                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i + batch_size]
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO venue_funding_rates "
                        "(venue, market, ts, rate_hourly, mark_price, index_price) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        batch,
                    )
                self._conn.execute("COMMIT")
            except duckdb.Error:
                self._conn.execute("ROLLBACK")
                raise
        return len(rows)

    def query_funding_rates(
        self,
        market: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        venue: Optional[str] = None,
    ) -> List[FundingRate]:
        """Query funding rates from venue_funding_rates.

        If venue is specified, returns only that venue's rates.
        Otherwise returns all venues' rates (one per ts, prefers drift).
        """
        if venue:
            sql = "SELECT venue, market, ts, rate_hourly, mark_price, index_price FROM venue_funding_rates WHERE market = ? AND venue = ?"
            params: list = [market, venue]
        else:
            sql = "SELECT venue, market, ts, rate_hourly, mark_price, index_price FROM venue_funding_rates WHERE market = ?"
            params = [market]
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
            FundingRate(market=r[1], ts=r[2], rate=r[3], oracle_price=r[5],
                        mark_price=r[4], slot=0, source=r[0])
            for r in rows
        ]

    def query_funding_by_venue(
        self,
        market: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> dict:
        """Query funding rates grouped by venue.
        Returns {venue: [{ts, rate, mark_price, index_price}, ...]}
        """
        sql = "SELECT venue, ts, rate_hourly, mark_price, index_price FROM venue_funding_rates WHERE market = ?"
        params: list = [market]
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        sql += " ORDER BY venue, ts ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        result: dict = {}
        for r in rows:
            venue = r[0]
            if venue not in result:
                result[venue] = []
            result[venue].append({"ts": r[1], "rate": r[2], "mark_price": r[3], "index_price": r[4]})
        return result

    # -- Jupiter borrow rates --------------------------------------------------

    def upsert_borrow_rates(self, snapshots: List[BorrowSnapshot]) -> int:
        """Upsert Jupiter Perps borrow rate snapshots.

        Uses the same lock+transaction pattern as upsert_funding_rates.
        On conflict (market, ts), the row is replaced with the new data.
        """
        if not snapshots:
            return 0
        rows = [
            (s.market, s.ts, s.rate_hourly, s.utilization, s.cumulative_rate, s.source)
            for s in snapshots
        ]
        batch_size = 2000
        with self._lock:
            self._conn.execute("BEGIN TRANSACTION")
            try:
                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i + batch_size]
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO jupiter_borrow_rates "
                        "(market, ts, rate_hourly, utilization, cumulative_rate, source) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        batch,
                    )
                self._conn.execute("COMMIT")
            except duckdb.Error:
                self._conn.execute("ROLLBACK")
                raise
        return len(rows)

    def query_borrow_rates(
        self,
        market: str,
        start_ts: int,
        end_ts: int,
    ) -> List[BorrowSnapshot]:
        """Query borrow rate snapshots for a market within [start_ts, end_ts]."""
        sql = (
            "SELECT market, ts, rate_hourly, utilization, cumulative_rate, source "
            "FROM jupiter_borrow_rates "
            "WHERE market = ? AND ts >= ? AND ts <= ? "
            "ORDER BY ts ASC"
        )
        with self._lock:
            rows = self._conn.execute(sql, [market, start_ts, end_ts]).fetchall()
        return [
            BorrowSnapshot(
                market=r[0], ts=r[1], rate_hourly=r[2],
                utilization=r[3], cumulative_rate=r[4], source=r[5],
            )
            for r in rows
        ]

    def query_borrow_cumulative(
        self,
        market: str,
        ts: int,
    ) -> Optional[float]:
        """Return the cumulative_rate at or before ts, or None if no data exists."""
        sql = (
            "SELECT cumulative_rate FROM jupiter_borrow_rates "
            "WHERE market = ? AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1"
        )
        with self._lock:
            row = self._conn.execute(sql, [market, ts]).fetchone()
        return row[0] if row else None

    # -- oracle prices ---------------------------------------------------------

    def upsert_oracle_prices(self, prices: List[OraclePrice]) -> int:
        if not prices:
            return 0
        rows = [(p.market, p.ts, p.price, p.slot) for p in prices]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO oracle_prices (market, ts, price, slot) VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def query_oracle_prices(
        self,
        market: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> List[OraclePrice]:
        sql = "SELECT market, ts, price, slot FROM oracle_prices WHERE market = ?"
        params: list = [market]
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        sql += " ORDER BY ts ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [OraclePrice(market=r[0], ts=r[1], price=r[2], slot=r[3]) for r in rows]

    def count_oracle_prices(self, market: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM oracle_prices WHERE market = ?", [market]
            ).fetchone()
        return row[0] if row else 0

    # -- orderbook snapshots ---------------------------------------------------

    def upsert_orderbook_snapshots(self, snapshots: list) -> int:
        if not snapshots:
            return 0
        rows = [
            (
                s.get("venue", "pyth"),
                s["market"],
                s["ts"],
                s["bid_prices"],
                s["bid_sizes"],
                s["ask_prices"],
                s["ask_sizes"],
            )
            for s in snapshots
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO orderbook_snapshots "
                "(venue, market, ts, bid_prices, bid_sizes, ask_prices, ask_sizes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def query_orderbook_snapshots(
        self,
        market: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> List["OrderbookSnapshot"]:
        """Query orderbook snapshots for a market within a time range."""
        from ..models import OrderbookLevel, OrderbookSnapshot
        sql = "SELECT market, ts, bid_prices, bid_sizes, ask_prices, ask_sizes FROM orderbook_snapshots WHERE market = ?"
        params: list = [market]
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        sql += " ORDER BY ts ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        result = []
        for r in rows:
            bids = tuple(OrderbookLevel(price=p, size=s) for p, s in zip(r[2] or [], r[3] or []))
            asks = tuple(OrderbookLevel(price=p, size=s) for p, s in zip(r[4] or [], r[5] or []))
            result.append(OrderbookSnapshot(market=r[0], ts=r[1], bids=bids, asks=asks))
        return result

    def query_nearest_orderbook(
        self,
        venue: str,
        market: str,
        ts: int,
    ) -> Optional["OrderbookSnapshot"]:
        """Return the most recent orderbook snapshot for (venue, market) at or before ts.

        Returns None if no snapshot exists at or before the given timestamp.
        """
        from ..models import OrderbookLevel, OrderbookSnapshot
        sql = (
            "SELECT market, ts, bid_prices, bid_sizes, ask_prices, ask_sizes "
            "FROM orderbook_snapshots "
            "WHERE venue = ? AND market = ? AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1"
        )
        with self._lock:
            row = self._conn.execute(sql, [venue, market, ts]).fetchone()
        if row is None:
            return None
        bids = tuple(OrderbookLevel(price=p, size=sz) for p, sz in zip(row[2] or [], row[3] or []))
        asks = tuple(OrderbookLevel(price=p, size=sz) for p, sz in zip(row[4] or [], row[5] or []))
        return OrderbookSnapshot(market=row[0], ts=row[1], bids=bids, asks=asks)

    # -- pool snapshots --------------------------------------------------------

    def upsert_pool_snapshots(self, snapshots: list) -> int:
        if not snapshots:
            return 0
        rows = [
            (s["pool_address"], s["dex"], s["token_a_mint"], s["token_b_mint"],
             s["reserve_a"], s["reserve_b"], s["fee_rate"], s["ts"])
            for s in snapshots
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO pool_snapshots "
                "(pool_address, dex, token_a_mint, token_b_mint, "
                "reserve_a, reserve_b, fee_rate, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    # -- venue funding rates ---------------------------------------------------

    def upsert_venue_funding(self, snapshots: list) -> int:
        """Insert cross-venue funding rate snapshots.

        Each snapshot is a FundingSnapshot or dict with: venue, market, ts, rate_hourly, mark_price, index_price.
        """
        if not snapshots:
            return 0
        rows = []
        for s in snapshots:
            if hasattr(s, 'venue'):
                rows.append((s.venue, s.market, s.ts, s.rate_hourly, s.mark_price, s.index_price))
            else:
                rows.append((s["venue"], s["market"], s["ts"], s["rate_hourly"],
                             s.get("mark_price", 0), s.get("index_price", 0)))
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO venue_funding_rates "
                "(venue, market, ts, rate_hourly, mark_price, index_price) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def query_venue_funding(
        self,
        venue: str,
        market: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> list:
        """Query funding rates for a specific venue + market."""
        sql = "SELECT venue, market, ts, rate_hourly, mark_price, index_price FROM venue_funding_rates WHERE venue = ? AND market = ?"
        params: list = [venue, market]
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        sql += " ORDER BY ts ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [{"venue": r[0], "market": r[1], "ts": r[2], "rate_hourly": r[3],
                 "mark_price": r[4], "index_price": r[5]} for r in rows]

    def list_venues(self, market: Optional[str] = None) -> list:
        """List venues that have funding data, optionally filtered by market."""
        sql = "SELECT DISTINCT venue, market, COUNT(*) as cnt FROM venue_funding_rates"
        params: list = []
        if market:
            sql += " WHERE market = ?"
            params.append(market)
        sql += " GROUP BY venue, market ORDER BY venue"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [{"venue": r[0], "market": r[1], "count": r[2]} for r in rows]

    # -- open interest -------------------------------------------------------

    def upsert_open_interest(self, records: List["OpenInterest"]) -> int:
        if not records:
            return 0
        rows = [(getattr(r, 'venue', 'drift'), r.market, r.ts, r.long_oi, r.short_oi) for r in records]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO open_interest "
                "(venue, market, ts, long_oi, short_oi) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def query_open_interest(
        self,
        market: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        venue: Optional[str] = None,
    ) -> list:
        if venue:
            sql = "SELECT venue, market, ts, long_oi, short_oi FROM open_interest WHERE market = ? AND venue = ?"
            params: list = [market, venue]
        else:
            sql = "SELECT venue, market, ts, long_oi, short_oi FROM open_interest WHERE market = ?"
            params = [market]
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        sql += " ORDER BY ts ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        from ..models import OpenInterest
        return [OpenInterest(venue=r[0], market=r[1], ts=r[2], long_oi=r[3], short_oi=r[4]) for r in rows]

    # -- liquidations --------------------------------------------------------

    def upsert_liquidations(self, records: List["Liquidation"]) -> int:
        if not records:
            return 0
        rows = [(r.market, r.ts, r.side, r.size, r.price, r.slot, r.tx_sig) for r in records]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO liquidations "
                "(market, ts, side, size, price, slot, tx_sig) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def query_liquidations(
        self,
        market: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> list:
        sql = "SELECT market, ts, side, size, price, slot, tx_sig FROM liquidations WHERE market = ?"
        params: list = [market]
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        sql += " ORDER BY ts ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        from ..models import Liquidation
        return [Liquidation(market=r[0], ts=r[1], side=r[2], size=r[3], price=r[4], slot=r[5], tx_sig=r[6]) for r in rows]

    # -- whale transfers -----------------------------------------------------

    def upsert_whale_transfers(self, records: List["WhaleTransfer"]) -> int:
        if not records:
            return 0
        rows = [(r.wallet, r.token_mint, r.amount, r.ts, r.direction, r.tx_sig) for r in records]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO whale_transfers "
                "(wallet, token_mint, amount, ts, direction, tx_sig) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def query_whale_transfers(
        self,
        token_mint: Optional[str] = None,
        wallet: Optional[str] = None,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> list:
        sql = "SELECT wallet, token_mint, amount, ts, direction, tx_sig FROM whale_transfers WHERE 1=1"
        params: list = []
        if token_mint:
            sql += " AND token_mint = ?"
            params.append(token_mint)
        if wallet:
            sql += " AND wallet = ?"
            params.append(wallet)
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        sql += " ORDER BY ts ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        from ..models import WhaleTransfer
        return [WhaleTransfer(wallet=r[0], token_mint=r[1], amount=r[2], ts=r[3], direction=r[4], tx_sig=r[5]) for r in rows]

    # -- dex volume ----------------------------------------------------------

    def upsert_dex_volume(self, records: List["DexVolume"]) -> int:
        if not records:
            return 0
        rows = [(r.market, r.dex, r.ts, r.volume_usd, r.txn_count) for r in records]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO dex_volume "
                "(market, dex, ts, volume_usd, txn_count) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def query_dex_volume(
        self,
        market: str,
        dex: Optional[str] = None,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> list:
        sql = "SELECT market, dex, ts, volume_usd, txn_count FROM dex_volume WHERE market = ?"
        params: list = [market]
        if dex:
            sql += " AND dex = ?"
            params.append(dex)
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        sql += " ORDER BY ts ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        from ..models import DexVolume
        return [DexVolume(market=r[0], dex=r[1], ts=r[2], volume_usd=r[3], txn_count=r[4]) for r in rows]

    # -- token unlocks -------------------------------------------------------

    def upsert_token_unlocks(self, records: list) -> int:
        if not records:
            return 0
        rows = [(r.token_mint, r.unlock_ts, r.amount, r.vesting_account) for r in records]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO token_unlocks "
                "(token_mint, unlock_ts, amount, vesting_account) VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    # -- sync metadata -------------------------------------------------------

    def upsert_sync_metadata(self, meta: "SyncMetadata") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sync_metadata "
                "(provider, market, data_type, last_sync_ts, record_count, status, error_msg) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [meta.provider, meta.market, meta.data_type, meta.last_sync_ts,
                 meta.record_count, meta.status, meta.error_msg],
            )

    def get_sync_metadata(self, provider: str, market: str, data_type: str) -> Optional["SyncMetadata"]:
        with self._lock:
            row = self._conn.execute(
                "SELECT provider, market, data_type, last_sync_ts, record_count, status, error_msg "
                "FROM sync_metadata WHERE provider = ? AND market = ? AND data_type = ?",
                [provider, market, data_type],
            ).fetchone()
        if not row:
            return None
        from ..models import SyncMetadata
        return SyncMetadata(provider=row[0], market=row[1], data_type=row[2],
                            last_sync_ts=row[3], record_count=row[4],
                            status=row[5], error_msg=row[6])

    def is_range_synced(self, market: str, resolution_s: int, start_ts: int, end_ts: int,
                        max_age_s: int = 3600) -> bool:
        """Check if a candle range is already covered in the local DB.

        Returns True if we have candles covering at least 90% of the requested
        range AND the most recent sync was within max_age_s seconds.
        Used to skip redundant downloads.
        """
        import time as _time
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*), MIN(ts), MAX(ts) FROM candles "
                "WHERE market = ? AND resolution_s = ? AND ts >= ? AND ts <= ?",
                [market, resolution_s, start_ts, end_ts],
            ).fetchone()
        if not row or row[0] == 0:
            return False
        count, first_ts, last_ts = row
        expected = (end_ts - start_ts) // resolution_s
        if expected <= 0:
            return True
        coverage = count / expected
        if coverage < 0.9:
            return False
        meta = self.get_sync_metadata("drift_api", market, "candles")
        if meta and (_time.time() - meta.last_sync_ts) < max_age_s:
            return True
        return coverage >= 0.95

    def list_sync_metadata(self, provider: Optional[str] = None) -> list:
        sql = "SELECT provider, market, data_type, last_sync_ts, record_count, status, error_msg FROM sync_metadata"
        params: list = []
        if provider:
            sql += " WHERE provider = ?"
            params.append(provider)
        sql += " ORDER BY provider, market, data_type"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        from ..models import SyncMetadata
        return [SyncMetadata(provider=r[0], market=r[1], data_type=r[2],
                             last_sync_ts=r[3], record_count=r[4],
                             status=r[5], error_msg=r[6]) for r in rows]

    def get_data_freshness(self) -> list:
        """Return freshness info for all tracked provider/market pairs."""
        import time as _time
        now = int(_time.time())
        with self._lock:
            rows = self._conn.execute(
                "SELECT provider, market, data_type, last_sync_ts, record_count, status, error_msg "
                "FROM sync_metadata ORDER BY last_sync_ts DESC"
            ).fetchall()
        return [
            {"provider": r[0], "market": r[1], "data_type": r[2], "last_sync_ts": r[3],
             "age_s": now - r[3], "record_count": r[4], "status": r[5], "error_msg": r[6]}
            for r in rows
        ]

    # -- live trading persistence -----------------------------------------------

    def create_live_session(
        self, session_id: str, strategy_name: str, market: str,
        network: str, venue: str, initial_capital: float, config_snapshot: str,
    ) -> None:
        import time as _time
        with self._lock:
            self._conn.execute(
                "INSERT INTO live_sessions "
                "(session_id, strategy_name, market, network, venue, "
                "initial_capital, config_snapshot, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [session_id, strategy_name, market, network, venue,
                 initial_capital, config_snapshot, int(_time.time())],
            )

    def update_live_session_status(
        self, session_id: str, status: str, stopped_at: Optional[int] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE live_sessions SET status = ?, stopped_at = ? WHERE session_id = ?",
                [status, stopped_at, session_id],
            )

    def get_live_session(self, session_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id, strategy_name, market, network, venue, "
                "initial_capital, config_snapshot, status, started_at, stopped_at "
                "FROM live_sessions WHERE session_id = ?",
                [session_id],
            ).fetchone()
        if not row:
            return None
        return {
            "session_id": row[0], "strategy_name": row[1], "market": row[2],
            "network": row[3], "venue": row[4], "initial_capital": row[5],
            "config_snapshot": row[6], "status": row[7],
            "started_at": row[8], "stopped_at": row[9],
        }

    def upsert_live_order(
        self, order_id: str, session_id: str, venue_order_id: Optional[int],
        market: str, side: str, order_type: str, size: float, price: float,
        state: str, retry_count: int, tx_sig: Optional[str],
        created_at: int, updated_at: int, state_history: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO live_orders "
                "(order_id, session_id, venue_order_id, market, side, order_type, "
                "size, price, state, retry_count, tx_sig, created_at, updated_at, state_history) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [order_id, session_id, venue_order_id, market, side, order_type,
                 size, price, state, retry_count, tx_sig, created_at, updated_at, state_history],
            )

    def get_live_orders(self, session_id: str, state: Optional[str] = None) -> list:
        sql = (
            "SELECT order_id, session_id, venue_order_id, market, side, order_type, "
            "size, price, state, retry_count, tx_sig, created_at, updated_at, state_history "
            "FROM live_orders WHERE session_id = ?"
        )
        params: list = [session_id]
        if state:
            sql += " AND state = ?"
            params.append(state)
        sql += " ORDER BY created_at ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {"order_id": r[0], "session_id": r[1], "venue_order_id": r[2],
             "market": r[3], "side": r[4], "order_type": r[5], "size": r[6],
             "price": r[7], "state": r[8], "retry_count": r[9], "tx_sig": r[10],
             "created_at": r[11], "updated_at": r[12], "state_history": r[13]}
            for r in rows
        ]

    def insert_live_fill(
        self, fill_id: str, order_id: str, session_id: str,
        market: str, side: str, price: float, size: float, fee: float,
        tx_sig: str, venue: str, is_partial: bool, ts: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO live_fills "
                "(fill_id, order_id, session_id, market, side, price, size, "
                "fee, tx_sig, venue, is_partial, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [fill_id, order_id, session_id, market, side, price, size,
                 fee, tx_sig, venue, is_partial, ts],
            )

    def get_live_fills(self, session_id: str, market: Optional[str] = None) -> list:
        sql = (
            "SELECT fill_id, order_id, session_id, market, side, price, size, "
            "fee, tx_sig, venue, is_partial, ts FROM live_fills WHERE session_id = ?"
        )
        params: list = [session_id]
        if market:
            sql += " AND market = ?"
            params.append(market)
        sql += " ORDER BY ts ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {"fill_id": r[0], "order_id": r[1], "session_id": r[2], "market": r[3],
             "side": r[4], "price": r[5], "size": r[6], "fee": r[7], "tx_sig": r[8],
             "venue": r[9], "is_partial": r[10], "ts": r[11]}
            for r in rows
        ]

    def query_live_fills_by_venue(
        self, venue: str, market: str,
        start_ts: Optional[int] = None, end_ts: Optional[int] = None,
    ) -> list:
        """Query fills from live_fills by venue and market."""
        sql = (
            "SELECT fill_id, order_id, session_id, market, side, price, size, "
            "fee, tx_sig, venue, is_partial, ts FROM live_fills "
            "WHERE venue = ? AND market = ?"
        )
        params: list = [venue, market]
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
            {"fill_id": r[0], "order_id": r[1], "session_id": r[2], "market": r[3],
             "side": r[4], "price": r[5], "size": r[6], "fee": r[7], "tx_sig": r[8],
             "venue": r[9], "is_partial": r[10], "ts": r[11]}
            for r in rows
        ]

    def insert_live_equity(
        self, session_id: str, ts: int, equity: float, cash: float, unrealized_pnl: float,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO live_equity_history "
                "(session_id, ts, equity, cash, unrealized_pnl) VALUES (?, ?, ?, ?, ?)",
                [session_id, ts, equity, cash, unrealized_pnl],
            )

    def get_live_equity_history(self, session_id: str) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, ts, equity, cash, unrealized_pnl "
                "FROM live_equity_history WHERE session_id = ? ORDER BY ts ASC",
                [session_id],
            ).fetchall()
        return [
            {"session_id": r[0], "ts": r[1], "equity": r[2],
             "cash": r[3], "unrealized_pnl": r[4]}
            for r in rows
        ]

    # -- tick snapshots (CLMM) -------------------------------------------------

    def upsert_tick_snapshot(
        self, pool_address: str, ts: int, dex: str,
        token_a_mint: str, token_b_mint: str,
        current_tick: int, tick_spacing: int,
        fee_rate: float, sqrt_price: float, tick_data_json: str,
    ) -> None:
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
            {
                "pool_address": r[0], "ts": r[1], "dex": r[2],
                "token_a_mint": r[3], "token_b_mint": r[4],
                "current_tick": r[5], "tick_spacing": r[6],
                "fee_rate": r[7], "sqrt_price": r[8], "tick_data": r[9],
            }
            for r in rows
        ]

    # -- strategies ---------------------------------------------------------------

    def upsert_strategy(self, strategy_id: str, name: str, code: str = "",
                        params_json: str = "{}", category: str = "custom",
                        status: str = "draft", notes: str = "") -> None:
        """Save or update a strategy in the registry."""
        import time as _time
        now = int(_time.time())
        with self._lock:
            existing = self._conn.execute(
                "SELECT created_at FROM strategies WHERE strategy_id = ?",
                [strategy_id],
            ).fetchone()
            created = existing[0] if existing else now
            self._conn.execute(
                "INSERT OR REPLACE INTO strategies "
                "(strategy_id, name, code, params_json, category, status, created_at, updated_at, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [strategy_id, name, code, params_json, category, status, created, now, notes],
            )

    def update_strategy_status(self, name: str, status: str) -> None:
        """Update the lifecycle status of a strategy by name.

        Status progression: draft → backtested → optimized → paper → live → retired
        Only advances forward (won't downgrade live → backtested).
        """
        import time as _time
        order = {"draft": 0, "backtested": 1, "optimized": 2, "paper": 3, "live": 4, "retired": 5}
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM strategies WHERE name = ?", [name]
            ).fetchone()
            if row is None:
                return
            current = row[0]
            if order.get(status, 0) > order.get(current, 0):
                self._conn.execute(
                    "UPDATE strategies SET status = ?, updated_at = ? WHERE name = ?",
                    [status, int(_time.time()), name],
                )

    def list_strategies(self) -> list:
        """List all registered strategies with lifecycle stats.

        Joins against backtest_runs, paper_sessions, live_sessions to compute
        backtest count, best Sharpe, and latest paper/live session status.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT strategy_id, name, category, status, code, "
                "params_json, created_at, updated_at, notes "
                "FROM strategies ORDER BY updated_at DESC"
            ).fetchall()

            bt_stats: dict = {}
            paper_stats: dict = {}
            live_stats: dict = {}
            try:
                for r in self._conn.execute(
                    "SELECT strategy_name, COUNT(*), MAX(sharpe_ratio), MAX(total_pnl) "
                    "FROM backtest_runs GROUP BY strategy_name"
                ).fetchall():
                    bt_stats[r[0]] = {"count": r[1], "best_sharpe": r[2], "best_pnl": r[3]}
            except duckdb.Error:
                pass
            try:
                for r in self._conn.execute(
                    "SELECT strategy_name, session_id, status FROM paper_sessions "
                    "ORDER BY started_at DESC"
                ).fetchall():
                    if r[0] not in paper_stats:
                        paper_stats[r[0]] = {"session_id": r[1], "status": r[2]}
            except duckdb.Error:
                pass
            try:
                for r in self._conn.execute(
                    "SELECT strategy_name, session_id, status FROM live_sessions "
                    "ORDER BY started_at DESC"
                ).fetchall():
                    if r[0] not in live_stats:
                        live_stats[r[0]] = {"session_id": r[1], "status": r[2]}
            except duckdb.Error:
                pass

        result = []
        for r in rows:
            name = r[1]
            bt = bt_stats.get(name, {})
            ps = paper_stats.get(name, {})
            ls = live_stats.get(name, {})
            result.append({
                "strategy_id": r[0], "name": name, "category": r[2],
                "status": r[3], "code": r[4], "params_json": r[5],
                "created_at": r[6], "updated_at": r[7], "notes": r[8],
                "backtest_count": bt.get("count", 0),
                "best_sharpe": bt.get("best_sharpe", 0),
                "best_pnl": bt.get("best_pnl", 0),
                "paper_session_id": ps.get("session_id"),
                "paper_status": ps.get("status"),
                "live_session_id": ls.get("session_id"),
                "live_status": ls.get("status"),
            })
        return result

    def get_strategy(self, name: str) -> dict | None:
        """Get a single strategy by name."""
        with self._lock:
            row = self._conn.execute(
                "SELECT strategy_id, name, code, params_json, category, "
                "status, created_at, updated_at, notes "
                "FROM strategies WHERE name = ?", [name]
            ).fetchone()
        if row is None:
            return None
        return {
            "strategy_id": row[0], "name": row[1], "code": row[2],
            "params_json": row[3], "category": row[4], "status": row[5],
            "created_at": row[6], "updated_at": row[7], "notes": row[8],
        }

    def delete_strategy(self, name: str) -> bool:
        """Delete a strategy by name."""
        with self._lock:
            self._conn.execute("DELETE FROM strategies WHERE name = ?", [name])
            return True

    # -- encapsulation methods -------------------------------------------------

    def list_live_sessions(self, limit: int = 20) -> list:
        """List recent live trading sessions (most recent first)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, strategy_name, market, venue, status, "
                "started_at, stopped_at FROM live_sessions "
                "ORDER BY started_at DESC LIMIT ?",
                [limit],
            ).fetchall()
        return [
            {"session_id": r[0], "strategy": r[1], "market": r[2],
             "venue": r[3], "status": r[4], "started_at": r[5],
             "stopped_at": r[6]}
            for r in rows
        ]

    def list_markets_with_data(self) -> list:
        """List every (market, resolution) pair that has candle data."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT market, resolution_s, COUNT(*) AS candle_count, "
                "MIN(ts) AS first_ts, MAX(ts) AS last_ts "
                "FROM candles GROUP BY market, resolution_s ORDER BY market"
            ).fetchall()
        return [
            {"market": r[0], "resolution_s": r[1],
             "candle_count": r[2], "first_ts": r[3], "last_ts": r[4]}
            for r in rows
        ]

    def list_venues_for_market(self, market: str,
                               exclude_venues: Optional[List[str]] = None) -> List[str]:
        """Distinct venues that have candle data for a given market."""
        excluded = tuple(exclude_venues or ["pyth", "default"])
        placeholders = ",".join("?" for _ in excluded)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT DISTINCT venue FROM candles "
                f"WHERE market = ? AND venue NOT IN ({placeholders})",
                [market, *excluded],
            ).fetchall()
        return [r[0] for r in rows]

    def delete_market_data(self, market: str) -> dict:
        """Delete candles + funding + oracle + orderbook + OI + liquidations
        + sync_metadata rows for a market. Returns per-table counts."""
        tables = [
            ("candles", "market"),
            ("venue_funding_rates", "market"),
            ("oracle_prices", "market"),
            ("orderbook_snapshots", "market"),
            ("open_interest", "market"),
            ("liquidations", "market"),
        ]
        deleted: dict = {}
        with self._lock:
            for table, col in tables:
                try:
                    before = self._conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {col} = ?",
                        [market],
                    ).fetchone()[0]
                    if before > 0:
                        self._conn.execute(
                            f"DELETE FROM {table} WHERE {col} = ?",
                            [market],
                        )
                        deleted[table] = before
                except duckdb.Error:
                    pass
            try:
                self._conn.execute(
                    "DELETE FROM sync_metadata WHERE market = ?", [market]
                )
            except duckdb.Error:
                pass
        return deleted

    def count_funding_rates(self, market: str, start_ts: int, end_ts: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM venue_funding_rates "
                "WHERE market = ? AND ts >= ? AND ts <= ?",
                [market, start_ts, end_ts],
            ).fetchone()
        return int(row[0] if row else 0)

    def count_orderbook_snapshots(self, market: str, start_ts: int, end_ts: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM orderbook_snapshots "
                "WHERE market = ? AND ts >= ? AND ts <= ?",
                [market, start_ts, end_ts],
            ).fetchone()
        return int(row[0] if row else 0)

    def count_open_interest(self, market: str, start_ts: int, end_ts: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM open_interest "
                "WHERE market = ? AND ts >= ? AND ts <= ?",
                [market, start_ts, end_ts],
            ).fetchone()
        return int(row[0] if row else 0)

    def mark_running_live_sessions_interrupted(self, stopped_at: int) -> list:
        """Startup-recovery helper: any live_session row stuck at 'running'
        gets flipped to 'interrupted'. Returns the interrupted session rows
        so the caller can log them."""
        with self._lock:
            stale = self._conn.execute(
                "SELECT session_id, strategy_name, market, venue FROM live_sessions "
                "WHERE status = 'running'"
            ).fetchall()
            if stale:
                self._conn.execute(
                    "UPDATE live_sessions SET status = 'interrupted', "
                    "stopped_at = ? WHERE status = 'running'",
                    [stopped_at],
                )
        return [
            {"session_id": r[0], "strategy": r[1], "market": r[2], "venue": r[3]}
            for r in stale
        ]

    def count_venue_candles(self, market: str, start_ts: int, end_ts: int,
                            exclude_venues: Optional[List[str]] = None) -> int:
        """Count candles for a market across non-excluded venues within ts range."""
        excluded = tuple(exclude_venues or ["pyth", "default"])
        placeholders = ",".join("?" for _ in excluded)
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM candles "
                f"WHERE market = ? AND venue NOT IN ({placeholders}) "
                f"AND ts >= ? AND ts <= ?",
                [market, *excluded, start_ts, end_ts],
            ).fetchone()
        return int(row[0] if row else 0)

    # ─── Lock-wrapping SQL gateways ──────────────────────────────────────
    # Storage-layer helpers in flint/journal/ and flint/paper/session_store.py
    # go through these to avoid touching self._conn / self._lock directly.

    def _sql_exec(self, sql: str, params=None) -> None:
        """Run a write statement under the store's lock. No return value."""
        with self._lock:
            if params is None:
                self._conn.execute(sql)
            else:
                self._conn.execute(sql, params)

    def _sql_read_all(self, sql: str, params=None) -> list:
        """Run a read query under the lock. Returns all rows as a list of
        tuples."""
        with self._lock:
            cur = (self._conn.execute(sql) if params is None
                   else self._conn.execute(sql, params))
            return cur.fetchall()

    def _sql_read_one(self, sql: str, params=None):
        """Run a read query under the lock. Returns first row or None."""
        with self._lock:
            cur = (self._conn.execute(sql) if params is None
                   else self._conn.execute(sql, params))
            return cur.fetchone()

    def _sql_read_all_with_cols(self, sql: str, params=None):
        """Read-all + column names as list[str]. Two-tuple return."""
        with self._lock:
            cur = (self._conn.execute(sql) if params is None
                   else self._conn.execute(sql, params))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            return rows, cols

    def _sql_exec_many(self, stmts: list) -> None:
        """Run several write statements atomically under the lock. Each
        stmt is (sql, params|None)."""
        with self._lock:
            for sql, params in stmts:
                if params is None:
                    self._conn.execute(sql)
                else:
                    self._conn.execute(sql, params)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
