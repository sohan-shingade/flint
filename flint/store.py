"""DuckDB-backed candle store.

Thread-safe: all connection access is serialized through a threading.Lock.
This allows the store to be shared between the API main thread, the collector
background thread, and backtest worker threads without DuckDB lock conflicts.
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional

import duckdb

from .models import BorrowSnapshot, Candle, FundingRate, OraclePrice

_logger = logging.getLogger("flint.store")

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
    venue       VARCHAR NOT NULL DEFAULT 'default',
    PRIMARY KEY (venue, market, resolution_s, ts)
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
    venue      VARCHAR  NOT NULL DEFAULT 'default',
    market     VARCHAR  NOT NULL,
    ts         BIGINT   NOT NULL,
    bid_prices DOUBLE[],
    bid_sizes  DOUBLE[],
    ask_prices DOUBLE[],
    ask_sizes  DOUBLE[],
    PRIMARY KEY (venue, market, ts)
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

_CREATE_OPEN_INTEREST = """
CREATE TABLE IF NOT EXISTS open_interest (
    venue     VARCHAR NOT NULL DEFAULT 'drift',
    market    VARCHAR NOT NULL,
    ts        BIGINT  NOT NULL,
    long_oi   DOUBLE  NOT NULL,
    short_oi  DOUBLE  NOT NULL,
    PRIMARY KEY (venue, market, ts)
);
"""

_CREATE_LIQUIDATIONS = """
CREATE TABLE IF NOT EXISTS liquidations (
    market  VARCHAR NOT NULL,
    ts      BIGINT  NOT NULL,
    side    VARCHAR NOT NULL,
    size    DOUBLE  NOT NULL,
    price   DOUBLE  NOT NULL,
    slot    BIGINT  NOT NULL DEFAULT 0,
    tx_sig  VARCHAR NOT NULL DEFAULT '',
    PRIMARY KEY (market, ts, tx_sig)
);
"""

_CREATE_WHALE_TRANSFERS = """
CREATE TABLE IF NOT EXISTS whale_transfers (
    wallet      VARCHAR NOT NULL,
    token_mint  VARCHAR NOT NULL,
    amount      DOUBLE  NOT NULL,
    ts          BIGINT  NOT NULL,
    direction   VARCHAR NOT NULL,
    tx_sig      VARCHAR NOT NULL DEFAULT '',
    PRIMARY KEY (token_mint, ts, tx_sig)
);
"""

_CREATE_DEX_VOLUME = """
CREATE TABLE IF NOT EXISTS dex_volume (
    market     VARCHAR NOT NULL,
    dex        VARCHAR NOT NULL,
    ts         BIGINT  NOT NULL,
    volume_usd DOUBLE  NOT NULL,
    txn_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (market, dex, ts)
);
"""

_CREATE_TOKEN_UNLOCKS = """
CREATE TABLE IF NOT EXISTS token_unlocks (
    token_mint      VARCHAR NOT NULL,
    unlock_ts       BIGINT  NOT NULL,
    amount          DOUBLE  NOT NULL,
    vesting_account VARCHAR NOT NULL DEFAULT '',
    PRIMARY KEY (token_mint, unlock_ts)
);
"""

_CREATE_SYNC_METADATA = """
CREATE TABLE IF NOT EXISTS sync_metadata (
    provider     VARCHAR NOT NULL,
    market       VARCHAR NOT NULL,
    data_type    VARCHAR NOT NULL,
    last_sync_ts BIGINT  NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    status       VARCHAR NOT NULL DEFAULT 'ok',
    error_msg    VARCHAR NOT NULL DEFAULT '',
    PRIMARY KEY (provider, market, data_type)
);
"""

_CREATE_PAPER_SESSIONS = """
CREATE TABLE IF NOT EXISTS paper_sessions (
    session_id      VARCHAR PRIMARY KEY,
    strategy_name   VARCHAR NOT NULL,
    strategy_code   TEXT NOT NULL,
    strategy_params VARCHAR NOT NULL DEFAULT '{}',
    market          VARCHAR NOT NULL,
    initial_capital DOUBLE NOT NULL,
    replay_start_ts BIGINT NOT NULL,
    live_start_ts   BIGINT NOT NULL DEFAULT 0,
    started_at      BIGINT NOT NULL,
    stopped_at      BIGINT,
    status          VARCHAR NOT NULL DEFAULT 'replaying',
    stop_reason     VARCHAR NOT NULL DEFAULT '',
    risk_config     VARCHAR NOT NULL DEFAULT '{}'
);
"""

_CREATE_PAPER_EQUITY_HISTORY = """
CREATE TABLE IF NOT EXISTS paper_equity_history (
    session_id     VARCHAR NOT NULL,
    ts             BIGINT NOT NULL,
    equity         DOUBLE NOT NULL,
    cash           DOUBLE NOT NULL,
    unrealized_pnl DOUBLE NOT NULL DEFAULT 0,
    is_replay      BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (session_id, ts)
);
"""

_CREATE_PAPER_TRADES = """
CREATE TABLE IF NOT EXISTS paper_trades (
    session_id  VARCHAR NOT NULL,
    trade_id    VARCHAR NOT NULL,
    market      VARCHAR NOT NULL,
    side        VARCHAR NOT NULL,
    size        DOUBLE NOT NULL,
    entry_price DOUBLE NOT NULL,
    exit_price  DOUBLE NOT NULL,
    entry_ts    BIGINT NOT NULL,
    exit_ts     BIGINT NOT NULL,
    pnl         DOUBLE NOT NULL,
    fees        DOUBLE NOT NULL DEFAULT 0,
    is_replay   BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (session_id, trade_id)
);
"""

_CREATE_PAPER_POSITIONS = """
CREATE TABLE IF NOT EXISTS paper_positions (
    session_id     VARCHAR NOT NULL,
    market         VARCHAR NOT NULL,
    side           VARCHAR NOT NULL,
    size           DOUBLE NOT NULL,
    entry_price    DOUBLE NOT NULL,
    entry_ts       BIGINT NOT NULL,
    unrealized_pnl DOUBLE NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, market)
);
"""

_CREATE_PAPER_FUNDING_PAYMENTS = """
CREATE TABLE IF NOT EXISTS paper_funding_payments (
    session_id    VARCHAR NOT NULL,
    ts            BIGINT NOT NULL,
    market        VARCHAR NOT NULL,
    rate          DOUBLE NOT NULL,
    payment       DOUBLE NOT NULL,
    position_size DOUBLE NOT NULL,
    mark_price    DOUBLE NOT NULL
);
"""

_CREATE_LIVE_SESSIONS = """
CREATE TABLE IF NOT EXISTS live_sessions (
    session_id      VARCHAR PRIMARY KEY,
    strategy_name   VARCHAR NOT NULL,
    market          VARCHAR NOT NULL,
    network         VARCHAR NOT NULL,
    venue           VARCHAR NOT NULL DEFAULT 'drift',
    initial_capital DOUBLE,
    config_snapshot VARCHAR,
    status          VARCHAR DEFAULT 'running',
    started_at      BIGINT NOT NULL,
    stopped_at      BIGINT
);
"""

_CREATE_LIVE_ORDERS = """
CREATE TABLE IF NOT EXISTS live_orders (
    order_id       VARCHAR PRIMARY KEY,
    session_id     VARCHAR NOT NULL,
    venue_order_id INTEGER,
    market         VARCHAR NOT NULL,
    side           VARCHAR NOT NULL,
    order_type     VARCHAR NOT NULL,
    size           DOUBLE NOT NULL,
    price          DOUBLE,
    state          VARCHAR NOT NULL,
    retry_count    INTEGER DEFAULT 0,
    tx_sig         VARCHAR,
    created_at     BIGINT NOT NULL,
    updated_at     BIGINT NOT NULL,
    state_history  VARCHAR
);
"""

_CREATE_LIVE_FILLS = """
CREATE TABLE IF NOT EXISTS live_fills (
    fill_id    VARCHAR PRIMARY KEY,
    order_id   VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    market     VARCHAR NOT NULL,
    side       VARCHAR NOT NULL,
    price      DOUBLE NOT NULL,
    size       DOUBLE NOT NULL,
    fee        DOUBLE NOT NULL,
    tx_sig     VARCHAR NOT NULL,
    venue      VARCHAR NOT NULL DEFAULT 'drift',
    is_partial BOOLEAN DEFAULT FALSE,
    ts         BIGINT NOT NULL
);
"""

_CREATE_LIVE_EQUITY_HISTORY = """
CREATE TABLE IF NOT EXISTS live_equity_history (
    session_id     VARCHAR NOT NULL,
    ts             BIGINT NOT NULL,
    equity         DOUBLE NOT NULL,
    cash           DOUBLE NOT NULL,
    unrealized_pnl DOUBLE NOT NULL,
    PRIMARY KEY (session_id, ts)
);
"""

_CREATE_TICK_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS tick_snapshots (
    pool_address VARCHAR NOT NULL, ts BIGINT NOT NULL, dex VARCHAR NOT NULL,
    token_a_mint VARCHAR NOT NULL, token_b_mint VARCHAR NOT NULL,
    current_tick INTEGER NOT NULL, tick_spacing INTEGER NOT NULL,
    fee_rate DOUBLE NOT NULL, sqrt_price DOUBLE NOT NULL,
    tick_data VARCHAR NOT NULL, PRIMARY KEY (pool_address, ts)
);
"""

_CREATE_JUPITER_BORROW_RATES = """
CREATE TABLE IF NOT EXISTS jupiter_borrow_rates (
    market          VARCHAR NOT NULL,
    ts              BIGINT  NOT NULL,
    rate_hourly     DOUBLE  NOT NULL,
    utilization     DOUBLE  NOT NULL,
    cumulative_rate DOUBLE  NOT NULL,
    source          VARCHAR NOT NULL DEFAULT 'rpc',
    PRIMARY KEY (market, ts)
);
"""


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
        self._create_tables()

    def _create_tables(self) -> None:
        try:
            self._conn.execute("PRAGMA wal_autocheckpoint='1000'")
        except Exception as e:
            _logger.debug("WAL pragma not supported: %s", e)
        self._conn.execute(_CREATE_CANDLES)
        # Migration: add venue column to candles if missing (existing DBs)
        try:
            cols = {r[0] for r in self._conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='candles'"
            ).fetchall()}
            if cols and "venue" not in cols:
                # DuckDB doesn't support ALTER PRIMARY KEY, so recreate the table
                self._conn.execute("BEGIN TRANSACTION")
                try:
                    self._conn.execute("""
                        CREATE TABLE candles_new (
                            market       VARCHAR NOT NULL,
                            resolution_s INTEGER NOT NULL,
                            ts           BIGINT  NOT NULL,
                            open         DOUBLE  NOT NULL,
                            high         DOUBLE  NOT NULL,
                            low          DOUBLE  NOT NULL,
                            close        DOUBLE  NOT NULL,
                            volume       DOUBLE  NOT NULL,
                            venue        VARCHAR NOT NULL DEFAULT 'default',
                            PRIMARY KEY (venue, market, resolution_s, ts)
                        )
                    """)
                    self._conn.execute(
                        "INSERT INTO candles_new "
                        "SELECT market, resolution_s, ts, open, high, low, close, volume, 'default' "
                        "FROM candles"
                    )
                    self._conn.execute("DROP TABLE candles")
                    self._conn.execute("ALTER TABLE candles_new RENAME TO candles")
                    self._conn.execute("COMMIT")
                    _logger.info("Migrated candles table: added venue column")
                except Exception as e:
                    self._conn.execute("ROLLBACK")
                    _logger.warning("Candles venue migration failed: %s", e)
        except Exception as e:
            _logger.debug("Candles migration check: %s", e)
        self._conn.execute(_CREATE_ORACLE_PRICES)
        self._conn.execute(_CREATE_ORDERBOOK_SNAPSHOTS)
        # Migration: add venue column to orderbook_snapshots if missing (existing DBs)
        try:
            cols = {r[0] for r in self._conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='orderbook_snapshots'"
            ).fetchall()}
            if cols and "venue" not in cols:
                self._conn.execute("BEGIN TRANSACTION")
                try:
                    self._conn.execute("""
                        CREATE TABLE orderbook_snapshots_new (
                            venue      VARCHAR  NOT NULL DEFAULT 'default',
                            market     VARCHAR  NOT NULL,
                            ts         BIGINT   NOT NULL,
                            bid_prices DOUBLE[],
                            bid_sizes  DOUBLE[],
                            ask_prices DOUBLE[],
                            ask_sizes  DOUBLE[],
                            PRIMARY KEY (venue, market, ts)
                        )
                    """)
                    self._conn.execute(
                        "INSERT INTO orderbook_snapshots_new "
                        "SELECT 'default', market, ts, bid_prices, bid_sizes, ask_prices, ask_sizes "
                        "FROM orderbook_snapshots"
                    )
                    self._conn.execute("DROP TABLE orderbook_snapshots")
                    self._conn.execute("ALTER TABLE orderbook_snapshots_new RENAME TO orderbook_snapshots")
                    self._conn.execute("COMMIT")
                    _logger.info("Migrated orderbook_snapshots table: added venue column")
                except Exception as e:
                    self._conn.execute("ROLLBACK")
                    _logger.warning("Orderbook snapshots venue migration failed: %s", e)
        except Exception as e:
            _logger.debug("Orderbook snapshots migration check: %s", e)
        self._conn.execute(_CREATE_POOL_SNAPSHOTS)
        self._conn.execute(_CREATE_VENUE_FUNDING)
        # Migrate: if old funding_rates table exists, copy data to venue_funding_rates
        try:
            count = self._conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'funding_rates'"
            ).fetchone()[0]
            if count > 0:
                # Check which columns the old table has before migrating
                old_cols = {r[0] for r in self._conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'funding_rates'"
                ).fetchall()}
                if 'rate' in old_cols:
                    venue_expr = "COALESCE(source, 'drift')" if 'source' in old_cols else "'drift'"
                    self._conn.execute("BEGIN TRANSACTION")
                    try:
                        self._conn.execute(f"""
                            INSERT OR IGNORE INTO venue_funding_rates
                                (venue, market, ts, rate_hourly, mark_price, index_price)
                            SELECT {venue_expr}, market, ts, rate,
                                   COALESCE(mark_price, 0), COALESCE(oracle_price, 0)
                            FROM funding_rates
                            WHERE ABS(rate) < 0.005
                        """)
                        self._conn.execute("DROP TABLE funding_rates")
                        self._conn.execute("COMMIT")
                        _logger.info("Migrated funding_rates → venue_funding_rates")
                    except Exception as e:
                        self._conn.execute("ROLLBACK")
                        _logger.warning("Funding rates migration failed (data preserved): %s", e)
                else:
                    # Old table has incompatible schema — safe to drop
                    self._conn.execute("DROP TABLE funding_rates")
                    _logger.info("Dropped incompatible legacy funding_rates table")
        except Exception as e:
            _logger.debug("Funding rates migration check: %s", e)
        # Migrate: add venue column to open_interest if missing
        try:
            cols = [r[0] for r in self._conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='open_interest'"
            ).fetchall()]
            if cols and "venue" not in cols:
                self._conn.execute("DROP TABLE open_interest")
        except Exception as e:
            _logger.debug("Open interest migration check: %s", e)
        self._conn.execute(_CREATE_OPEN_INTEREST)
        self._conn.execute(_CREATE_LIQUIDATIONS)
        self._conn.execute(_CREATE_WHALE_TRANSFERS)
        self._conn.execute(_CREATE_DEX_VOLUME)
        self._conn.execute(_CREATE_TOKEN_UNLOCKS)
        self._conn.execute(_CREATE_SYNC_METADATA)
        # Paper trading persistence
        self._conn.execute(_CREATE_PAPER_SESSIONS)
        self._conn.execute(_CREATE_PAPER_EQUITY_HISTORY)
        self._conn.execute(_CREATE_PAPER_TRADES)
        self._conn.execute(_CREATE_PAPER_POSITIONS)
        self._conn.execute(_CREATE_PAPER_FUNDING_PAYMENTS)
        # Live trading persistence
        self._conn.execute(_CREATE_LIVE_SESSIONS)
        self._conn.execute(_CREATE_LIVE_ORDERS)
        self._conn.execute(_CREATE_LIVE_FILLS)
        self._conn.execute(_CREATE_LIVE_EQUITY_HISTORY)
        # CLMM tick snapshots
        self._conn.execute(_CREATE_TICK_SNAPSHOTS)
        # Jupiter Perps borrow rates
        self._conn.execute(_CREATE_JUPITER_BORROW_RATES)

    # -- candles ---------------------------------------------------------------

    def upsert_candles(self, candles: List[Candle]) -> int:
        if not candles:
            return 0
        rows = [
            (c.venue or "default", c.market, c.resolution_s, c.ts, c.open, c.high, c.low, c.close, c.volume)
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
            except Exception:
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
        sql += " ORDER BY ts ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            Candle(market=r[0], resolution_s=r[1], ts=r[2], open=r[3],
                   high=r[4], low=r[5], close=r[6], volume=r[7],
                   venue=r[8] if len(r) > 8 else "default")
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
            except Exception:
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
            except Exception:
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
                s.get("venue", "default"),
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
        from .models import OrderbookLevel, OrderbookSnapshot
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
        from .models import OrderbookLevel, OrderbookSnapshot
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
        from .models import OpenInterest
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
        from .models import Liquidation
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
        from .models import WhaleTransfer
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
        from .models import DexVolume
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
        from .models import SyncMetadata
        return SyncMetadata(provider=row[0], market=row[1], data_type=row[2],
                            last_sync_ts=row[3], record_count=row[4],
                            status=row[5], error_msg=row[6])

    def list_sync_metadata(self, provider: Optional[str] = None) -> list:
        sql = "SELECT provider, market, data_type, last_sync_ts, record_count, status, error_msg FROM sync_metadata"
        params: list = []
        if provider:
            sql += " WHERE provider = ?"
            params.append(provider)
        sql += " ORDER BY provider, market, data_type"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        from .models import SyncMetadata
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

    def close(self) -> None:
        with self._lock:
            self._conn.close()
