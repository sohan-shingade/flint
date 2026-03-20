"""DuckDB-backed candle store."""
from __future__ import annotations

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


class FlintStore:
    def __init__(self, path: str = ":memory:"):
        self._conn = duckdb.connect(path)
        self._create_tables()

    def _create_tables(self) -> None:
        try:
            self._conn.execute("PRAGMA wal_autocheckpoint='1000'")
        except Exception:
            pass  # WAL not supported on all DuckDB builds; non-fatal
        self._conn.execute(_CREATE_CANDLES)
        self._conn.execute(_CREATE_FUNDING_RATES)
        self._conn.execute(_CREATE_ORACLE_PRICES)
        self._conn.execute(_CREATE_ORDERBOOK_SNAPSHOTS)
        self._conn.execute(_CREATE_POOL_SNAPSHOTS)

    def upsert_candles(self, candles: List[Candle]) -> int:
        if not candles:
            return 0
        rows = [
            (c.market, c.resolution_s, c.ts, c.open, c.high, c.low, c.close, c.volume)
            for c in candles
        ]
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO candles
                (market, resolution_s, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return len(rows)

    def query_candles(
        self,
        market: str,
        resolution_s: int,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
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
        rows = self._conn.execute(sql, params).fetchall()
        return [
            Candle(
                market=r[0],
                resolution_s=r[1],
                ts=r[2],
                open=r[3],
                high=r[4],
                low=r[5],
                close=r[6],
                volume=r[7],
            )
            for r in rows
        ]

    def count_candles(self, market: str, resolution_s: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM candles WHERE market = ? AND resolution_s = ?",
            [market, resolution_s],
        ).fetchone()
        return row[0] if row else 0

    # -- funding rates --------------------------------------------------------

    def upsert_funding_rates(self, rates: List[FundingRate]) -> int:
        if not rates:
            return 0
        rows = [
            (r.market, r.ts, r.rate, r.oracle_price, r.mark_price, r.slot)
            for r in rates
        ]
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO funding_rates
                (market, ts, rate, oracle_price, mark_price, slot)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
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
        rows = self._conn.execute(sql, params).fetchall()
        return [
            FundingRate(market=r[0], ts=r[1], rate=r[2], oracle_price=r[3], mark_price=r[4], slot=r[5])
            for r in rows
        ]

    # -- oracle prices --------------------------------------------------------

    def upsert_oracle_prices(self, prices: List[OraclePrice]) -> int:
        if not prices:
            return 0
        rows = [(p.market, p.ts, p.price, p.slot) for p in prices]
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
        rows = self._conn.execute(sql, params).fetchall()
        return [OraclePrice(market=r[0], ts=r[1], price=r[2], slot=r[3]) for r in rows]

    def count_oracle_prices(self, market: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM oracle_prices WHERE market = ?", [market]
        ).fetchone()
        return row[0] if row else 0

    # -- orderbook snapshots -------------------------------------------------

    def upsert_orderbook_snapshots(self, snapshots: list) -> int:
        if not snapshots:
            return 0
        rows = [
            (s["market"], s["ts"], s["bid_prices"], s["bid_sizes"], s["ask_prices"], s["ask_sizes"])
            for s in snapshots
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO orderbook_snapshots (market, ts, bid_prices, bid_sizes, ask_prices, ask_sizes) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        return len(rows)

    # -- pool snapshots ------------------------------------------------------

    def upsert_pool_snapshots(self, snapshots: list) -> int:
        if not snapshots:
            return 0
        rows = [
            (s["pool_address"], s["dex"], s["token_a_mint"], s["token_b_mint"], s["reserve_a"], s["reserve_b"], s["fee_rate"], s["ts"])
            for s in snapshots
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO pool_snapshots (pool_address, dex, token_a_mint, token_b_mint, reserve_a, reserve_b, fee_rate, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        return len(rows)

    def close(self) -> None:
        self._conn.close()
