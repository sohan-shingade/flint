"""Core data models."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


class Signal(enum.Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class Side(enum.Enum):
    LONG = "long"
    SHORT = "short"


class OrderType(enum.Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


class OrderStatus(enum.Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"


class TimeInForce(str, enum.Enum):
    IOC = "ioc"
    FOK = "fok"
    GTC = "gtc"


# ---------------------------------------------------------------------------
# Phase 1-2 models
# ---------------------------------------------------------------------------

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


@dataclass
class Trade:
    ts: int
    price: float
    size: float  # base asset
    side: Side
    market: str


@dataclass
class Position:
    entry_price: float
    size: float  # positive = long, negative = short
    entry_ts: int
    exit_price: float = 0.0
    exit_ts: int = 0
    pnl: float = 0.0
    closed: bool = False

    @property
    def side(self) -> Side:
        return Side.LONG if self.size >= 0 else Side.SHORT

    def close(self, price: float, ts: int) -> float:
        self.exit_price = price
        self.exit_ts = ts
        self.pnl = (price - self.entry_price) * self.size
        self.closed = True
        return self.pnl


@dataclass
class BacktestResult:
    total_pnl: float
    win_rate: float
    max_drawdown: float
    sharpe_ratio: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    positions: List[Position] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    fills: List["Fill"] = field(default_factory=list)
    total_fees: float = 0.0
    funding_paid: float = 0.0
    strategy_warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Execution context models (v0.2)
# ---------------------------------------------------------------------------

@dataclass
class AccountState:
    """Snapshot of account value."""
    equity: float  # cash + unrealized PnL
    cash: float  # available cash
    unrealized_pnl: float = 0.0
    margin_used: float = 0.0
    free_margin: float = 0.0
    leverage: float = 0.0


@dataclass
class PositionInfo:
    """Read-only view of an open position."""
    market: str
    side: Side
    size: float  # always positive
    entry_price: float
    unrealized_pnl: float = 0.0
    entry_ts: int = 0
    venue: str = "default"


# ---------------------------------------------------------------------------
# Phase 3-4 models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FundingRate:
    market: str
    ts: int
    rate: float  # hourly funding rate (raw / 1e6)
    oracle_price: float
    mark_price: float
    slot: int = 0
    source: str = "drift"  # "drift", "hyperliquid", "drift_s3"


@dataclass(frozen=True)
class OrderbookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderbookSnapshot:
    market: str
    ts: int
    bids: Tuple[OrderbookLevel, ...]
    asks: Tuple[OrderbookLevel, ...]
    slot: int = 0


@dataclass(frozen=True)
class MarketInfo:
    """Protocol-level market metadata."""
    symbol: str  # e.g. "SOL-PERP"
    market_index: int
    oracle: str  # oracle pubkey
    tick_size: float
    min_order_size: float
    maker_fee: float
    taker_fee: float


@dataclass(frozen=True)
class PoolState:
    """AMM pool snapshot for arb detection."""
    pool_address: str
    dex: str  # "raydium", "orca", "lifinity"
    token_a_mint: str
    token_b_mint: str
    reserve_a: float
    reserve_b: float
    fee_rate: float
    slot: int = 0

    @property
    def price_a_in_b(self) -> float:
        return self.reserve_b / self.reserve_a if self.reserve_a else 0.0

    @property
    def price_b_in_a(self) -> float:
        return self.reserve_a / self.reserve_b if self.reserve_b else 0.0


@dataclass
class Order:
    market: str
    side: Side
    order_type: OrderType
    size: float
    price: float = 0.0  # 0 = market order
    order_id: str = ""
    status: OrderStatus = OrderStatus.PENDING
    ts: int = 0
    filled_size: float = 0.0
    filled_price: float = 0.0
    venue: str = "default"
    time_in_force: TimeInForce = TimeInForce.IOC


@dataclass(frozen=True)
class Fill:
    market: str
    side: Side
    price: float
    size: float
    fee: float
    ts: int
    order_id: str = ""
    tx_sig: str = ""
    venue: str = "default"
    is_partial: bool = False
    latency_ms: float = 0.0
    impact_bps: float = 0.0


@dataclass(frozen=True)
class ArbRoute:
    """A profitable arbitrage path through AMM pools."""
    pools: Tuple[str, ...]  # pool addresses in order
    tokens: Tuple[str, ...]  # token path
    input_amount: float
    output_amount: float
    profit: float
    profit_bps: float  # profit in basis points

    @property
    def hops(self) -> int:
        return len(self.pools)


@dataclass(frozen=True)
class LiquidationOpportunity:
    """A position nearing liquidation on Drift/Mango."""
    protocol: str  # "drift" or "mango"
    user_account: str
    market: str
    side: Side
    position_size: float
    entry_price: float
    liquidation_price: float
    oracle_price: float
    margin_ratio: float
    estimated_profit: float


# ---------------------------------------------------------------------------
# Phase 7 models — collector + extended storage
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OraclePrice:
    market: str
    ts: int
    price: float
    slot: Optional[int] = None


@dataclass(frozen=True)
class OpenInterest:
    market: str
    ts: int
    long_oi: float
    short_oi: float
    venue: str = "drift"

    @property
    def net_oi(self) -> float:
        return self.long_oi - self.short_oi

    @property
    def total_oi(self) -> float:
        return self.long_oi + self.short_oi


@dataclass(frozen=True)
class Liquidation:
    market: str
    ts: int
    side: str           # "long" or "short"
    size: float         # base asset amount
    price: float        # liquidation price
    slot: int = 0
    tx_sig: str = ""


@dataclass(frozen=True)
class WhaleTransfer:
    wallet: str
    token_mint: str
    amount: float
    ts: int
    direction: str      # "in" or "out"
    tx_sig: str = ""


@dataclass(frozen=True)
class DexVolume:
    market: str         # e.g. "SOL/USDC"
    dex: str            # "raydium", "orca", "jupiter"
    ts: int
    volume_usd: float
    txn_count: int = 0


@dataclass(frozen=True)
class TokenUnlock:
    token_mint: str
    unlock_ts: int
    amount: float
    vesting_account: str = ""


@dataclass(frozen=True)
class SyncMetadata:
    provider: str
    market: str
    data_type: str
    last_sync_ts: int
    record_count: int = 0
    status: str = "ok"      # "ok", "error", "syncing"
    error_msg: str = ""


@dataclass
class CollectorStatus:
    market: str
    data_type: str  # "candles", "funding", "orderbook", "pools", "oracle"
    state: str  # "idle", "collecting", "backfilling", "error"
    last_updated: Optional[int] = None
    row_count: int = 0
    date_range_start: Optional[int] = None
    date_range_end: Optional[int] = None
    error_message: Optional[str] = None
    progress_pct: Optional[float] = None
