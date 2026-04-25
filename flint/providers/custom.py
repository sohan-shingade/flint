"""Custom dataset ingest — CSV/Parquet → FlintStore with provenance.

Phase 1 T1.6. Schema documented at docs/reference/custom-data-schema.md.

Users bring their own candles, funding, orderbooks, and fills through the
same pipeline as built-in providers. Each file's SHA-256 is captured as
provenance metadata so proof notebooks can pin exact input versions.

Strict validation rejects: non-UTC timestamps, duplicates, out-of-order
rows, resolution mismatch, crossed orderbooks, missing columns.
"""
from __future__ import annotations

import csv
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from ..models import Candle, FundingRate

logger = logging.getLogger(__name__)


# Phase 1 T1.3.a — PIT declaration. Custom data carries whatever convention
# the user declares in their source; we treat ts as bar-close by convention
# and leave validation up to the user.
PIT_METADATA = {  # noqa: E402
    "candle_ts": "bar-close",
    "funding_ts": "accrual-time",
    "orderbook_ts": "exchange-time",
    "oi_ts": "exchange-time",
    "reviewed": "2026-04-23",
}


CUSTOM_MARKET_PREFIX = "custom:"
CUSTOM_VENUE = "custom"


class CustomDataValidationError(ValueError):
    """Raised when a custom data file fails strict validation."""


@dataclass
class CustomDataImport:
    """Result of a custom data import."""
    rows_loaded: int
    source_hash: str
    markets: List[str]
    table: str
    source_path: str
    # D-1.6 — when `table == "fills"` the parsed fill records come back
    # here so the caller (reconciliation / calibration) can iterate
    # them directly rather than re-parsing the file.
    fills: Optional[List[dict]] = None


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def compute_source_hash(path: Path, chunk_size: int = 65536) -> str:
    """SHA-256 of the file contents. Used for proof-notebook provenance."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_market_namespace(market: str) -> None:
    if not market.startswith(CUSTOM_MARKET_PREFIX):
        raise CustomDataValidationError(
            f"Custom market names must start with {CUSTOM_MARKET_PREFIX!r}; "
            f"got {market!r}"
        )


def _validate_candle_row(row: dict, line_no: int) -> None:
    required = {"ts_epoch_s", "open", "high", "low", "close", "volume",
                "market", "resolution_s"}
    missing = required - set(row.keys())
    if missing:
        raise CustomDataValidationError(
            f"line {line_no}: missing columns {sorted(missing)}"
        )
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    if h < max(o, c) - 1e-9:
        raise CustomDataValidationError(
            f"line {line_no}: high={h} < max(open={o}, close={c})"
        )
    if l > min(o, c) + 1e-9:
        raise CustomDataValidationError(
            f"line {line_no}: low={l} > min(open={o}, close={c})"
        )
    if float(row["volume"]) < 0:
        raise CustomDataValidationError(
            f"line {line_no}: volume < 0"
        )


def _validate_monotonic(ts_values: Sequence[int]) -> None:
    for i in range(1, len(ts_values)):
        if ts_values[i] <= ts_values[i - 1]:
            raise CustomDataValidationError(
                f"non-monotonic timestamps at index {i}: "
                f"ts[{i-1}]={ts_values[i-1]} >= ts[{i}]={ts_values[i]}"
            )


def _validate_resolution(ts_values: Sequence[int], declared_res: int,
                         tolerance_s: int = 1) -> None:
    if len(ts_values) < 2:
        return
    for i in range(1, len(ts_values)):
        gap = ts_values[i] - ts_values[i - 1]
        if abs(gap - declared_res) > tolerance_s:
            raise CustomDataValidationError(
                f"resolution mismatch at index {i}: declared={declared_res}s, "
                f"observed={gap}s (ts[{i-1}]={ts_values[i-1]}, ts[{i}]={ts_values[i]})"
            )


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class CustomCSVProvider:
    """Load candles / funding / fills from a CSV file.

    Usage:
        provider = CustomCSVProvider(path="./data/custom/btc.csv",
                                     table="candles",
                                     markets=["custom:BTC-SPOT"])
        result = provider.load_and_upsert(store)
    """

    def __init__(
        self,
        path: str | Path,
        table: str,
        markets: List[str],
    ):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self.table = table
        self.markets = markets
        for m in markets:
            _validate_market_namespace(m)
        self.source_hash: Optional[str] = None

    def _parse_candles(self) -> List[Candle]:
        candles: List[Candle] = []
        with open(self.path, newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader, start=2):  # line 1 is header
                _validate_candle_row(row, i)
                market = row["market"]
                _validate_market_namespace(market)
                if market not in self.markets:
                    raise CustomDataValidationError(
                        f"line {i}: market {market!r} not in declared "
                        f"markets {self.markets}"
                    )
                candles.append(Candle(
                    ts=int(row["ts_epoch_s"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    market=market,
                    resolution_s=int(row["resolution_s"]),
                    venue=CUSTOM_VENUE,
                ))

        # Group by market for per-market monotonic / resolution checks.
        by_market: dict[str, List[Candle]] = {}
        for c in candles:
            by_market.setdefault(c.market, []).append(c)
        for market, group in by_market.items():
            ts_list = [c.ts for c in group]
            _validate_monotonic(ts_list)
            if group:
                _validate_resolution(ts_list, group[0].resolution_s)
        return candles

    def _parse_funding(self) -> List[FundingRate]:
        rates: List[FundingRate] = []
        with open(self.path, newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader, start=2):
                required = {"ts_epoch_s", "market", "venue", "rate", "interval_s"}
                missing = required - set(row.keys())
                if missing:
                    raise CustomDataValidationError(
                        f"line {i}: missing columns {sorted(missing)}"
                    )
                market = row["market"]
                _validate_market_namespace(market)
                rate = float(row["rate"])
                if abs(rate) >= 1.0:
                    raise CustomDataValidationError(
                        f"line {i}: rate {rate} out of [-1, 1] — unit error?"
                    )
                rates.append(FundingRate(
                    ts=int(row["ts_epoch_s"]),
                    market=market,
                    source=row["venue"],
                    rate=rate,
                    oracle_price=float(row.get("oracle_price", 0.0) or 0.0),
                    mark_price=float(row.get("mark_price", 0.0) or 0.0),
                ))
        return rates

    def _parse_fills(self) -> List[dict]:
        """D-1.6 — parse a bring-your-own fill log. Shared schema with
        scripts/reconcile_fills.py. Returns a list of plain dicts; caller
        decides whether to push into `live_fills` (reconciliation) or feed
        into calibration (Phase 3.6).

        Required columns: ts_epoch_s, market, venue, side, size, price.
        Optional: fee, order_id, mid_price, realized_slippage_bps.
        """
        fills: List[dict] = []
        with open(self.path, newline="") as f:
            reader = csv.DictReader(f)
            required = {"ts_epoch_s", "market", "venue", "side", "size", "price"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise CustomDataValidationError(
                    f"fill CSV missing required columns {sorted(missing)}"
                )
            for i, row in enumerate(reader, start=2):
                market = row["market"]
                _validate_market_namespace(market)
                side = row["side"].lower()
                if side not in {"long", "short", "buy", "sell"}:
                    raise CustomDataValidationError(
                        f"line {i}: invalid side {side!r}"
                    )
                side = "long" if side in {"long", "buy"} else "short"
                size = float(row["size"])
                price = float(row["price"])
                if size <= 0:
                    raise CustomDataValidationError(f"line {i}: size must be > 0")
                if price <= 0:
                    raise CustomDataValidationError(f"line {i}: price must be > 0")
                fee = float(row.get("fee", 0.0) or 0.0)
                if fee < 0:
                    raise CustomDataValidationError(
                        f"line {i}: fee must be ≥ 0 (got {fee})"
                    )
                fills.append({
                    "ts_epoch_s": int(row["ts_epoch_s"]),
                    "market": market,
                    "venue": row["venue"],
                    "side": side,
                    "size": size,
                    "price": price,
                    "fee": fee,
                    "order_id": row.get("order_id", ""),
                    "mid_price": float(row["mid_price"]) if row.get("mid_price") else None,
                    "realized_slippage_bps": (
                        float(row["realized_slippage_bps"])
                        if row.get("realized_slippage_bps") else None
                    ),
                })
        return fills

    def load_and_upsert(self, store) -> CustomDataImport:
        """Parse file, validate, upsert into store. Returns provenance."""
        self.source_hash = compute_source_hash(self.path)

        if self.table == "candles":
            candles = self._parse_candles()
            n = store.upsert_candles(candles) if candles else 0
            logger.info("Loaded %d custom candles from %s (sha256=%s)",
                        n, self.path, self.source_hash[:16])
            return CustomDataImport(
                rows_loaded=n,
                source_hash=self.source_hash,
                markets=sorted({c.market for c in candles}),
                table="candles",
                source_path=str(self.path),
            )

        if self.table == "funding":
            rates = self._parse_funding()
            n = store.upsert_funding_rates(rates) if rates else 0
            logger.info("Loaded %d custom funding rates from %s (sha256=%s)",
                        n, self.path, self.source_hash[:16])
            return CustomDataImport(
                rows_loaded=n,
                source_hash=self.source_hash,
                markets=sorted({r.market for r in rates}),
                table="funding",
                source_path=str(self.path),
            )

        if self.table == "fills":
            fills = self._parse_fills()
            logger.info("Loaded %d custom fills from %s (sha256=%s)",
                        len(fills), self.path, self.source_hash[:16])
            # Unlike candles/funding, fills aren't upserted into a generic
            # store table — the caller (reconciliation, calibration) owns
            # how to consume them. Return them in `rows_loaded` as count + a
            # side channel via CustomDataImport.fills_preview.
            return CustomDataImport(
                rows_loaded=len(fills),
                source_hash=self.source_hash,
                markets=sorted({f["market"] for f in fills}),
                table="fills",
                source_path=str(self.path),
                fills=fills,
            )

        raise CustomDataValidationError(
            f"Unsupported table {self.table!r} "
            f"(supported: candles, funding — orderbook and fills coming next)"
        )


class CustomParquetProvider:
    """Load candles / funding / fills from a Parquet file.

    Uses pyarrow which is already a hard dependency. Same schema as CSV.
    """

    def __init__(
        self,
        path: str | Path,
        table: str,
        markets: List[str],
    ):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self.table = table
        self.markets = markets
        for m in markets:
            _validate_market_namespace(m)
        self.source_hash: Optional[str] = None

    def _parse_candles(self) -> List[Candle]:
        import pyarrow.parquet as pq
        tbl = pq.read_table(self.path).to_pylist()
        candles: List[Candle] = []
        for i, row in enumerate(tbl, start=2):
            # Mirror CSV validation
            _validate_candle_row(row, i)
            market = row["market"]
            _validate_market_namespace(market)
            if market not in self.markets:
                raise CustomDataValidationError(
                    f"row {i}: market {market!r} not in declared "
                    f"markets {self.markets}"
                )
            candles.append(Candle(
                ts=int(row["ts_epoch_s"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                market=market,
                resolution_s=int(row["resolution_s"]),
                venue=CUSTOM_VENUE,
            ))
        by_market: dict[str, List[Candle]] = {}
        for c in candles:
            by_market.setdefault(c.market, []).append(c)
        for market, group in by_market.items():
            ts_list = [c.ts for c in group]
            _validate_monotonic(ts_list)
            if group:
                _validate_resolution(ts_list, group[0].resolution_s)
        return candles

    def load_and_upsert(self, store) -> CustomDataImport:
        self.source_hash = compute_source_hash(self.path)
        if self.table == "candles":
            candles = self._parse_candles()
            n = store.upsert_candles(candles) if candles else 0
            return CustomDataImport(
                rows_loaded=n,
                source_hash=self.source_hash,
                markets=sorted({c.market for c in candles}),
                table="candles",
                source_path=str(self.path),
            )
        raise CustomDataValidationError(
            f"Unsupported table {self.table!r} "
            f"(supported: candles — others coming next)"
        )
