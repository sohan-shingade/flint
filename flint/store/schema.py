"""DDL constants and migration logic for the DuckDB-backed store."""
from __future__ import annotations

import logging

import duckdb

_logger = logging.getLogger("flint.store")

# ── Table definitions ────────────────────────────────────────────────────

CREATE_CANDLES = """
CREATE TABLE IF NOT EXISTS candles (
    market      VARCHAR NOT NULL,
    resolution_s INTEGER NOT NULL,
    ts          BIGINT  NOT NULL,
    open        DOUBLE  NOT NULL,
    high        DOUBLE  NOT NULL,
    low         DOUBLE  NOT NULL,
    close       DOUBLE  NOT NULL,
    volume      DOUBLE  NOT NULL,
    venue       VARCHAR NOT NULL DEFAULT 'pyth',
    PRIMARY KEY (venue, market, resolution_s, ts)
);
"""

CREATE_ORACLE_PRICES = """
CREATE TABLE IF NOT EXISTS oracle_prices (
    market  VARCHAR NOT NULL,
    ts      BIGINT  NOT NULL,
    price   DOUBLE  NOT NULL,
    slot    BIGINT,
    PRIMARY KEY (market, ts)
);
"""

CREATE_ORDERBOOK_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    venue      VARCHAR  NOT NULL DEFAULT 'pyth',
    market     VARCHAR  NOT NULL,
    ts         BIGINT   NOT NULL,
    bid_prices DOUBLE[],
    bid_sizes  DOUBLE[],
    ask_prices DOUBLE[],
    ask_sizes  DOUBLE[],
    PRIMARY KEY (venue, market, ts)
);
"""

CREATE_POOL_SNAPSHOTS = """
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

CREATE_VENUE_FUNDING = """
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

CREATE_OPEN_INTEREST = """
CREATE TABLE IF NOT EXISTS open_interest (
    venue     VARCHAR NOT NULL DEFAULT 'drift',
    market    VARCHAR NOT NULL,
    ts        BIGINT  NOT NULL,
    long_oi   DOUBLE  NOT NULL,
    short_oi  DOUBLE  NOT NULL,
    PRIMARY KEY (venue, market, ts)
);
"""

CREATE_LIQUIDATIONS = """
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

CREATE_WHALE_TRANSFERS = """
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

CREATE_DEX_VOLUME = """
CREATE TABLE IF NOT EXISTS dex_volume (
    market     VARCHAR NOT NULL,
    dex        VARCHAR NOT NULL,
    ts         BIGINT  NOT NULL,
    volume_usd DOUBLE  NOT NULL,
    txn_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (market, dex, ts)
);
"""

CREATE_TOKEN_UNLOCKS = """
CREATE TABLE IF NOT EXISTS token_unlocks (
    token_mint      VARCHAR NOT NULL,
    unlock_ts       BIGINT  NOT NULL,
    amount          DOUBLE  NOT NULL,
    vesting_account VARCHAR NOT NULL DEFAULT '',
    PRIMARY KEY (token_mint, unlock_ts)
);
"""

CREATE_SYNC_METADATA = """
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

CREATE_PAPER_SESSIONS = """
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

CREATE_PAPER_EQUITY_HISTORY = """
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

CREATE_PAPER_TRADES = """
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

CREATE_PAPER_POSITIONS = """
CREATE TABLE IF NOT EXISTS paper_positions (
    session_id     VARCHAR NOT NULL,
    venue          VARCHAR NOT NULL DEFAULT 'unknown',
    market         VARCHAR NOT NULL,
    side           VARCHAR NOT NULL,
    size           DOUBLE NOT NULL,
    entry_price    DOUBLE NOT NULL,
    entry_ts       BIGINT NOT NULL,
    unrealized_pnl DOUBLE NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, venue, market)
);
"""

CREATE_PAPER_FUNDING_PAYMENTS = """
CREATE TABLE IF NOT EXISTS paper_funding_payments (
    session_id    VARCHAR NOT NULL,
    ts            BIGINT NOT NULL,
    market        VARCHAR NOT NULL,
    venue         VARCHAR NOT NULL DEFAULT 'unknown',
    rate          DOUBLE NOT NULL,
    payment       DOUBLE NOT NULL,
    position_size DOUBLE NOT NULL,
    mark_price    DOUBLE NOT NULL,
    PRIMARY KEY (session_id, market, venue, ts)
);
"""

CREATE_LIVE_SESSIONS = """
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

CREATE_LIVE_ORDERS = """
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

CREATE_LIVE_FILLS = """
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

CREATE_LIVE_EQUITY_HISTORY = """
CREATE TABLE IF NOT EXISTS live_equity_history (
    session_id     VARCHAR NOT NULL,
    ts             BIGINT NOT NULL,
    equity         DOUBLE NOT NULL,
    cash           DOUBLE NOT NULL,
    unrealized_pnl DOUBLE NOT NULL,
    PRIMARY KEY (session_id, ts)
);
"""

CREATE_TICK_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS tick_snapshots (
    pool_address VARCHAR NOT NULL, ts BIGINT NOT NULL, dex VARCHAR NOT NULL,
    token_a_mint VARCHAR NOT NULL, token_b_mint VARCHAR NOT NULL,
    current_tick INTEGER NOT NULL, tick_spacing INTEGER NOT NULL,
    fee_rate DOUBLE NOT NULL, sqrt_price DOUBLE NOT NULL,
    tick_data VARCHAR NOT NULL, PRIMARY KEY (pool_address, ts)
);
"""

CREATE_JUPITER_BORROW_RATES = """
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

CREATE_JOURNAL_EQUITY = """
CREATE TABLE IF NOT EXISTS journal_equity (
    run_id   VARCHAR NOT NULL,
    ts       BIGINT  NOT NULL,
    equity   DOUBLE  NOT NULL,
    PRIMARY KEY (run_id, ts)
);
"""

CREATE_STRATEGIES = """
CREATE TABLE IF NOT EXISTS strategies (
    strategy_id   VARCHAR PRIMARY KEY,
    name          VARCHAR NOT NULL,
    code          TEXT    NOT NULL DEFAULT '',
    params_json   VARCHAR NOT NULL DEFAULT '{}',
    category      VARCHAR NOT NULL DEFAULT 'custom',
    status        VARCHAR NOT NULL DEFAULT 'draft',
    created_at    BIGINT  NOT NULL,
    updated_at    BIGINT  NOT NULL,
    notes         VARCHAR NOT NULL DEFAULT ''
);
"""


# ── Schema creation + migrations ─────────────────────────────────────────

def create_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all tables and run legacy migrations.

    Called once during FlintStore.__init__ (before the lock is exposed to
    other threads), so no locking is needed here.
    """
    try:
        conn.execute("PRAGMA wal_autocheckpoint='1000'")
    except duckdb.Error:
        pass

    conn.execute(CREATE_CANDLES)
    _migrate_candles_venue(conn)

    conn.execute(CREATE_ORACLE_PRICES)
    conn.execute(CREATE_ORDERBOOK_SNAPSHOTS)
    _migrate_orderbook_venue(conn)

    conn.execute(CREATE_POOL_SNAPSHOTS)
    conn.execute(CREATE_VENUE_FUNDING)
    _migrate_funding_rates(conn)

    _migrate_open_interest_venue(conn)
    conn.execute(CREATE_OPEN_INTEREST)
    conn.execute(CREATE_LIQUIDATIONS)
    conn.execute(CREATE_WHALE_TRANSFERS)
    conn.execute(CREATE_DEX_VOLUME)
    conn.execute(CREATE_TOKEN_UNLOCKS)
    conn.execute(CREATE_SYNC_METADATA)

    # Clean legacy venue tag
    try:
        conn.execute("DELETE FROM candles WHERE venue = 'default'")
        conn.execute("UPDATE orderbook_snapshots SET venue = 'pyth' WHERE venue = 'default'")
    except duckdb.Error:
        pass

    # Paper trading persistence
    conn.execute(CREATE_PAPER_SESSIONS)
    conn.execute(CREATE_PAPER_EQUITY_HISTORY)
    conn.execute(CREATE_PAPER_TRADES)
    conn.execute(CREATE_PAPER_POSITIONS)
    _migrate_paper_positions_venue(conn)
    conn.execute(CREATE_PAPER_FUNDING_PAYMENTS)
    _migrate_paper_funding_venue(conn)

    # Live trading persistence
    conn.execute(CREATE_LIVE_SESSIONS)
    conn.execute(CREATE_LIVE_ORDERS)
    conn.execute(CREATE_LIVE_FILLS)
    conn.execute(CREATE_LIVE_EQUITY_HISTORY)

    # CLMM tick snapshots
    conn.execute(CREATE_TICK_SNAPSHOTS)
    # Jupiter Perps borrow rates
    conn.execute(CREATE_JUPITER_BORROW_RATES)
    # Journal equity curves
    conn.execute(CREATE_JOURNAL_EQUITY)
    # Strategy registry
    conn.execute(CREATE_STRATEGIES)

    # Indexes
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_candles_market_res ON candles (market, resolution_s)",
        "CREATE INDEX IF NOT EXISTS idx_funding_market_venue ON venue_funding_rates (market, venue)",
        "CREATE INDEX IF NOT EXISTS idx_journal_strategy ON backtest_runs (strategy_name)",
        "CREATE INDEX IF NOT EXISTS idx_paper_sessions_strategy ON paper_sessions (strategy_name)",
        "CREATE INDEX IF NOT EXISTS idx_live_sessions_strategy ON live_sessions (strategy_name)",
        "CREATE INDEX IF NOT EXISTS idx_journal_equity_run ON journal_equity (run_id)",
    ]:
        try:
            conn.execute(idx_sql)
        except duckdb.Error:
            pass


# ── Individual migration helpers ─────────────────────────────────────────

def _migrate_candles_venue(conn: duckdb.DuckDBPyConnection) -> None:
    """Add venue column to candles if missing (existing DBs)."""
    try:
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='candles'"
        ).fetchall()}
        if not cols or "venue" in cols:
            return
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("""
                CREATE TABLE candles_new (
                    market       VARCHAR NOT NULL,
                    resolution_s INTEGER NOT NULL,
                    ts           BIGINT  NOT NULL,
                    open         DOUBLE  NOT NULL,
                    high         DOUBLE  NOT NULL,
                    low          DOUBLE  NOT NULL,
                    close        DOUBLE  NOT NULL,
                    volume       DOUBLE  NOT NULL,
                    venue        VARCHAR NOT NULL DEFAULT 'pyth',
                    PRIMARY KEY (venue, market, resolution_s, ts)
                )
            """)
            conn.execute(
                "INSERT INTO candles_new "
                "SELECT market, resolution_s, ts, open, high, low, close, volume, 'pyth' "
                "FROM candles"
            )
            conn.execute("DROP TABLE candles")
            conn.execute("ALTER TABLE candles_new RENAME TO candles")
            conn.execute("COMMIT")
            _logger.info("Migrated candles table: added venue column")
        except duckdb.Error as e:
            conn.execute("ROLLBACK")
            _logger.warning("Candles venue migration failed: %s", e)
    except duckdb.Error as e:
        _logger.debug("Candles migration check: %s", e)


def _migrate_orderbook_venue(conn: duckdb.DuckDBPyConnection) -> None:
    """Add venue column to orderbook_snapshots if missing."""
    try:
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='orderbook_snapshots'"
        ).fetchall()}
        if not cols or "venue" in cols:
            return
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("""
                CREATE TABLE orderbook_snapshots_new (
                    venue      VARCHAR  NOT NULL DEFAULT 'pyth',
                    market     VARCHAR  NOT NULL,
                    ts         BIGINT   NOT NULL,
                    bid_prices DOUBLE[],
                    bid_sizes  DOUBLE[],
                    ask_prices DOUBLE[],
                    ask_sizes  DOUBLE[],
                    PRIMARY KEY (venue, market, ts)
                )
            """)
            conn.execute(
                "INSERT INTO orderbook_snapshots_new "
                "SELECT 'pyth', market, ts, bid_prices, bid_sizes, ask_prices, ask_sizes "
                "FROM orderbook_snapshots"
            )
            conn.execute("DROP TABLE orderbook_snapshots")
            conn.execute("ALTER TABLE orderbook_snapshots_new RENAME TO orderbook_snapshots")
            conn.execute("COMMIT")
            _logger.info("Migrated orderbook_snapshots table: added venue column")
        except duckdb.Error as e:
            conn.execute("ROLLBACK")
            _logger.warning("Orderbook snapshots venue migration failed: %s", e)
    except duckdb.Error as e:
        _logger.debug("Orderbook snapshots migration check: %s", e)


def _migrate_funding_rates(conn: duckdb.DuckDBPyConnection) -> None:
    """Migrate legacy funding_rates table to venue_funding_rates."""
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'funding_rates'"
        ).fetchone()[0]
        if count == 0:
            return
        old_cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'funding_rates'"
        ).fetchall()}
        if 'rate' not in old_cols:
            conn.execute("DROP TABLE funding_rates")
            _logger.info("Dropped incompatible legacy funding_rates table")
            return
        venue_expr = "COALESCE(source, 'drift')" if 'source' in old_cols else "'drift'"
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(f"""
                INSERT OR IGNORE INTO venue_funding_rates
                    (venue, market, ts, rate_hourly, mark_price, index_price)
                SELECT {venue_expr}, market, ts, rate,
                       COALESCE(mark_price, 0), COALESCE(oracle_price, 0)
                FROM funding_rates
                WHERE ABS(rate) < 0.005
            """)
            conn.execute("DROP TABLE funding_rates")
            conn.execute("COMMIT")
            _logger.info("Migrated funding_rates → venue_funding_rates")
        except duckdb.Error as e:
            conn.execute("ROLLBACK")
            _logger.warning("Funding rates migration failed (data preserved): %s", e)
    except duckdb.Error as e:
        _logger.debug("Funding rates migration check: %s", e)


def _migrate_open_interest_venue(conn: duckdb.DuckDBPyConnection) -> None:
    """Drop open_interest table if it lacks a venue column (schema changed)."""
    try:
        cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='open_interest'"
        ).fetchall()]
        if cols and "venue" not in cols:
            conn.execute("DROP TABLE open_interest")
    except duckdb.Error as e:
        _logger.debug("Open interest migration check: %s", e)


def _migrate_paper_positions_venue(conn: duckdb.DuckDBPyConnection) -> None:
    """Add venue column + widen PK on paper_positions if needed."""
    try:
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='paper_positions'"
        ).fetchall()}
        if not cols or "venue" in cols:
            return
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("""
                CREATE TABLE paper_positions_new (
                    session_id     VARCHAR NOT NULL,
                    venue          VARCHAR NOT NULL DEFAULT 'unknown',
                    market         VARCHAR NOT NULL,
                    side           VARCHAR NOT NULL,
                    size           DOUBLE NOT NULL,
                    entry_price    DOUBLE NOT NULL,
                    entry_ts       BIGINT NOT NULL,
                    unrealized_pnl DOUBLE NOT NULL DEFAULT 0,
                    PRIMARY KEY (session_id, venue, market)
                )
            """)
            conn.execute(
                "INSERT INTO paper_positions_new "
                "SELECT session_id, 'unknown', market, side, size, "
                "entry_price, entry_ts, unrealized_pnl "
                "FROM paper_positions"
            )
            conn.execute("DROP TABLE paper_positions")
            conn.execute(
                "ALTER TABLE paper_positions_new RENAME TO paper_positions"
            )
            conn.execute("COMMIT")
            _logger.info("Migrated paper_positions: added venue column")
        except duckdb.Error as e:
            conn.execute("ROLLBACK")
            _logger.warning("paper_positions venue migration failed: %s", e)
    except duckdb.Error as e:
        _logger.debug("paper_positions migration check: %s", e)


def _migrate_paper_funding_venue(conn: duckdb.DuckDBPyConnection) -> None:
    """Add venue column + widen PK on paper_funding_payments if needed."""
    try:
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='paper_funding_payments'"
        ).fetchall()}
        if not cols or "venue" in cols:
            return
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("""
                CREATE TABLE paper_funding_payments_new (
                    session_id    VARCHAR NOT NULL,
                    ts            BIGINT NOT NULL,
                    market        VARCHAR NOT NULL,
                    venue         VARCHAR NOT NULL DEFAULT 'unknown',
                    rate          DOUBLE NOT NULL,
                    payment       DOUBLE NOT NULL,
                    position_size DOUBLE NOT NULL,
                    mark_price    DOUBLE NOT NULL,
                    PRIMARY KEY (session_id, market, venue, ts)
                )
            """)
            conn.execute(
                "INSERT INTO paper_funding_payments_new "
                "SELECT session_id, ts, market, 'unknown', rate, payment, "
                "position_size, mark_price "
                "FROM paper_funding_payments"
            )
            conn.execute("DROP TABLE paper_funding_payments")
            conn.execute(
                "ALTER TABLE paper_funding_payments_new "
                "RENAME TO paper_funding_payments"
            )
            conn.execute("COMMIT")
            _logger.info("Migrated paper_funding_payments: added venue column")
        except duckdb.Error as e:
            conn.execute("ROLLBACK")
            _logger.warning("paper_funding_payments venue migration failed: %s", e)
    except duckdb.Error as e:
        _logger.debug("paper_funding_payments migration check: %s", e)
