"""Market-data models — the recorded/observed facts an engine reads (§5).

All timestamps are unix-**milliseconds** UTC ``int``; a candle ``ts`` is the bar
**START**. Prices are ``float``. Accumulating quantities (funding totals, fees,
realized PnL, cash) never live here — they belong to the engine's ledgers in
``Decimal``/scaled-int (§5 numeric policy).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candle:
    """One OHLCV bar. ``ts`` is the bar START (unix ms)."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float  # base-asset units
    market: str  # e.g. "SOL-PERP"
    resolution_s: int  # bar width in seconds, e.g. 3600
    venue: str


@dataclass(frozen=True)
class MarkSnapshot:
    """The 'three prices' recorded together (§5, §6.1).

    ``mark_price`` drives PnL and liquidation; ``index_price`` (the oracle)
    drives funding. Keeping both in one snapshot is the model-level encoding of
    the correctness rule — you cannot write funding/liquidation code without
    choosing the right price.
    """

    market: str
    ts: int
    mark_price: float
    index_price: float
    venue: str
    oracle_confidence_bps: float = 0.0  # Pyth conf half-spread (Jupiter fills/liqs)
    oracle_lag_s: float = 0.0  # oracle staleness vs venue time

    @property
    def basis_bps(self) -> float:
        """Perp premium/discount of mark vs index, in basis points."""
        if self.index_price <= 0:
            return 0.0
        return (self.mark_price - self.index_price) / self.index_price * 10_000


@dataclass(frozen=True)
class FundingRate:
    """A single published funding observation (§5, §2.2).

    ``ts`` is when the rate was PUBLISHED. ``rate_hourly`` is normalized to 1h
    (linear: native / interval_h) regardless of the venue's native interval, so
    strategies compare like with like. ``price_basis`` records whether the rate
    is computed off the oracle (HL) or the mark (Binance) — the §2.2 trap. The
    venue's rate CAP is venue law and lives in the VenueSpec, not on the row.
    """

    market: str
    ts: int
    rate_hourly: float
    interval_s: int  # the venue's native interval (3600 or 28800)
    price_basis: str  # "oracle" (HL) or "mark" (Binance)
    rate_type: str  # "predicted" (strategy-visible) or "final" (payments)
    venue: str


@dataclass(frozen=True)
class OrderbookSnapshot:
    """One moment of recorded/archived CLOB depth (§5).

    ``bids``/``asks`` are best-first tuples of ``(price, size)`` pairs; tuples
    keep the snapshot hashable and immutable.
    """

    market: str
    ts: int
    bids: tuple  # ((price, size), ...) best-first
    asks: tuple
    venue: str


@dataclass(frozen=True)
class BorrowSnapshot:
    """Jupiter/GMX carrying cost — NOT a funding analog (§5, §6.3).

    ``utilization`` (borrowed / pool size) is the rate DRIVER; the raw
    ``pool_size``/``borrowed_size`` are kept so ``rate = f(utilization)`` is
    reconstructable. ``rate_hourly`` is a derived convenience for display.
    """

    market: str
    ts: int
    venue: str
    rate_hourly: float
    utilization: float
    pool_size: float
    borrowed_size: float
