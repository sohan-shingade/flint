"""DuckDB-backed candle store.

Thread-safe: all connection access is serialized through a threading.Lock.
This allows the store to be shared between the API main thread, the collector
background thread, and backtest worker threads without DuckDB lock conflicts.
"""
from __future__ import annotations

import threading
from typing import List, Optional

import duckdb

from .models import Candle, FundingRate, OraclePrice

_CREATE_CANDLES = """
CREATE TABLE IF NOT EXISTS candles (
    market      VARCHAR NOT NULL,
    resolution_s INTEGER NOT NULL,
    ts          BIGINT  NOT NULL,
    open        DOUBLE  NOT NULL,
    high        DOUBLE  NOT NULL,
    low         DOUBLE  NOT NULL,
    close       DOUBLE  NOT NULL,
    volume      DOUBLE  NOT NULL,
    PRIMARY KEY (market, resolution_s, ts)
);
"""

_CREATE_FUNDING_RATES = """
CREATE TABLE IF NOT EXISTS funding_rates (
    market       VARCHAR NOT NULL,
    ts           BIGINT  NOT NULL,
    rate         DOUBLE  NOT NULL,
    oracle_price DOUBLE  NOT NULL,
    mark_price   DOUBLE  NOT NULL,
    slot         BIGINT  NOT NULL DEFAULT 0,
    PRIMARY KEY (market, ts)
);
"""

_CREATE_ORACLE_PRICES = """
CREATE TABLE IF NOT EXISTS oracle_prices (
    market  VARCHAR NOT NULL,
    ts      BIGINT  NOT NULL,
    price   DOUBLE  NOT NULL,
    slot    BIGINT,
    PRIMARY KEY (market, ts)
);
"""

_CREATE_ORDERBOOK_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    market     VARCHAR  NOT NULL,
    ts         BIGINT   NOT NULL,
    bid_prices DOUBLE[],
    bid_sizes  DOUBLE[],
    ask_prices DOUBLE[],
    ask_sizes  DOUBLE[],
    PRIMARY KEY (market, ts)
);
"""

_CREATE_POOL_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS pool_snapshots (
    pool_address VARCHAR NOT NULL,
    dex          VARCHAR NOT NULL,
    token_a_mint VARCHAR NOT NULL,
    token_b_mint VARCHAR NOT NULL,
    reserve_a    DOUBLE  NOT NULL,
    reserve_b    DOUBLE  NOT NULL,
    fee_rate     DOUBLE  NOT NULL,
    ts           BIGINT  NOT NULL,
    PRIMARY KEY (pool_address, ts)
);
"""


_CREATE_VENUE_FUNDING = """
CREATE TABLE IF NOT EXISTS venue_funding_rates (
    venue       VARCHAR NOT NULL,
    market      VARCHAR NOT NULL,
    ts          BIGINT  NOT NULL,
    rate_hourly DOUBLE  NOT NULL,
    mark_price  DOUBLE  NOT NULL DEFAULT 0,
    index_price DOUBLE  NOT NULL DEFAULT 0,
    PRIMARY KEY (venue, market, ts)
);
"""


class FlintStore:
    """Thread-safe DuckDB store.

    All operations acquire ``_lock`` before touching ``_conn``.
    Safe to share one instance across multiple threads.
    """

    def __init__(self, path: str = ":memory:"):
        self._path = path
        self._lock = threading.Lock()
        self._conn = duckdb.connect(path)
        self._create_tables()

    def _create_tables(self) -> None:
        try:
            self._conn.execute("PRAGMA wal_autocheckpoint='1000'")
        except Exception:
            pass
        self._conn.execute(_CREATE_CANDLES)
        self._conn.execute(_CREATE_FUNDING_RATES)
        self._conn.execute(_CREATE_ORACLE_PRICES)
        self._conn.execute(_CREATE_ORDERBOOK_SNAPSHOTS)
        self._conn.execute(_CREATE_POOL_SNAPSHOTS)
        self._conn.execute(_CREATE_VENUE_FUNDING)

    # -- candles ---------------------------------------------------------------

    def upsert_candles(self, candles: List[Candle]) -> int:
        if not candles:
            return 0
        rows = [
            (c.market, c.resolution_s, c.ts, c.open, c.high, c.low, c.close, c.volume)
            for c in candles
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO candles "
                "(market, resolution_s, ts, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def query_candles(
        self,
        market: str,
        resolution_s: int,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Candle]:
        sql = "SELECT market, resolution_s, ts, open, high, low, close, volume FROM candles WHERE market = ? AND resolution_s = ?"
        params: list = [market, resolution_s]
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        sql += " ORDER BY ts ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            Candle(market=r[0], resolution_s=r[1], ts=r[2], open=r[3],
                   high=r[4], low=r[5], close=r[6], volume=r[7])
            for r in rows
        ]

    def count_candles(self, market: str, resolution_s: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM candles WHERE market = ? AND resolution_s = ?",
                [market, resolution_s],
            ).fetchone()
        return row[0] if row else 0

    # -- funding rates ---------------------------------------------------------

    def upsert_funding_rates(self, rates: List[FundingRate]) -> int:
        if not rates:
            return 0
        rows = [
            (r.market, r.ts, r.rate, r.oracle_price, r.mark_price, r.slot)
            for r in rates
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO funding_rates "
                "(market, ts, rate, oracle_price, mark_price, slot) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def query_funding_rates(
        self,
        market: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> List[FundingRate]:
        sql = "SELECT market, ts, rate, oracle_price, mark_price, slot FROM funding_rates WHERE market = ?"
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
        return [
            FundingRate(market=r[0], ts=r[1], rate=r[2], oracle_price=r[3],
                        mark_price=r[4], slot=r[5])
            for r in rows
        ]

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
            (s["market"], s["ts"], s["bid_prices"], s["bid_sizes"], s["ask_prices"], s["ask_sizes"])
            for s in snapshots
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO orderbook_snapshots "
                "(market, ts, bid_prices, bid_sizes, ask_prices, ask_sizes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

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

    def close(self) -> None:
        with self._lock:
            self._conn.close()
