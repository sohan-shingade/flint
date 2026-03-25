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

from .models import Candle, FundingRate, OraclePrice

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
    PRIMARY KEY (market, resolution_s, ts)
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
        except Exception as e:
            _logger.debug("WAL pragma not supported: %s", e)
        self._conn.execute(_CREATE_CANDLES)
        self._conn.execute(_CREATE_ORACLE_PRICES)
        self._conn.execute(_CREATE_ORDERBOOK_SNAPSHOTS)
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

    # -- candles ---------------------------------------------------------------

    def upsert_candles(self, candles: List[Candle]) -> int:
        if not candles:
            return 0
        rows = [
            (c.market, c.resolution_s, c.ts, c.open, c.high, c.low, c.close, c.volume)
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
                        "(market, resolution_s, ts, open, high, low, close, volume) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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

    def close(self) -> None:
        with self._lock:
            self._conn.close()
