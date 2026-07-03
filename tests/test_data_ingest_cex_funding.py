"""Read-only CEX funding (+OI) ingestion via CCXT (2.6, §10, D28).

Every fixture is a hand-authored fragment matching CCXT's unified
``fetch_funding_rate_history`` / ``fetch_open_interest_history`` response shapes.
No test opens a socket: the ``CcxtExchangeFactory`` seam is driven by a fake that
replays queued fragments (D26 — recorded fragments / unit inputs, never generated
market data).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from flint.data import (
    FreeVenueProvider,
    InMemoryCacheSource,
    Kind,
    TimeRange,
    VenueProvider,
)
from flint.data.ingest.backfillers import (
    CEX_INCEPTION_MS,
    CexFundingProvider,
    cex_symbol,
    funding_rate_to_hourly,
)


def _ms(y: int, m: int, d: int, h: int = 0) -> int:
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp() * 1000)


T0 = _ms(2025, 1, 1)
H = 3_600_000
H8 = 8 * H
H4 = 4 * H


def _fr(ts: int, rate: float | None) -> dict[str, Any]:
    """One CCXT unified funding-rate-history entry."""
    return {
        "info": {},
        "symbol": "SOL/USDT:USDT",
        "fundingRate": rate,
        "timestamp": ts,
        "datetime": None,
    }


def _oi(ts: int, amount: float) -> dict[str, Any]:
    return {
        "info": {},
        "symbol": "SOL/USDT:USDT",
        "openInterestAmount": amount,
        "openInterestValue": amount * 100.0,
        "timestamp": ts,
    }


class FakeCcxtExchange:
    """Replays sorted funding/OI fragments with CCXT's ``since``/``limit`` paging."""

    def __init__(
        self,
        *,
        funding: list[dict[str, Any]] | None = None,
        oi: list[dict[str, Any]] | None = None,
        has: Mapping[str, Any] | None = None,
    ) -> None:
        self._funding = sorted(funding or [], key=lambda e: e["timestamp"])
        self._oi = sorted(oi or [], key=lambda e: e["timestamp"])
        self.has = dict(
            has
            or {"fetchFundingRateHistory": True, "fetchOpenInterestHistory": True}
        )
        self.funding_calls: list[tuple[str, int | None, int | None]] = []
        self.oi_calls: list[tuple[str, str, int | None, int | None]] = []

    def fetch_funding_rate_history(
        self, symbol, since=None, limit=None, params=None
    ):
        self.funding_calls.append((symbol, since, limit))
        rows = [e for e in self._funding if since is None or e["timestamp"] >= since]
        return rows[:limit] if limit else rows

    def fetch_open_interest_history(
        self, symbol, timeframe="1h", since=None, limit=None, params=None
    ):
        self.oi_calls.append((symbol, timeframe, since, limit))
        rows = [e for e in self._oi if since is None or e["timestamp"] >= since]
        return rows[:limit] if limit else rows


class FakeFactory:
    def __init__(self, exchange: FakeCcxtExchange) -> None:
        self._ex = exchange
        self.created: list[str] = []

    def create(self, venue: str) -> FakeCcxtExchange:
        self.created.append(venue)
        return self._ex


def _provider(exchange: FakeCcxtExchange, venue: str = "binance", **kw) -> CexFundingProvider:
    return CexFundingProvider(venue, FakeFactory(exchange), **kw)


# --- helpers ----------------------------------------------------------------


def test_cex_symbol_maps_perp_to_ccxt_swap_symbol():
    assert cex_symbol("SOL-PERP") == "SOL/USDT:USDT"
    assert cex_symbol("BTC-PERP", quote="USDC") == "BTC/USDC:USDC"


def test_funding_rate_to_hourly_divides_by_interval_hours():
    # 0.0008 charged per 8h settlement -> 0.0001 per hour.
    assert funding_rate_to_hourly(0.0008, 28_800) == pytest.approx(0.0001)
    with pytest.raises(ValueError):
        funding_rate_to_hourly(0.0001, 0)


def test_is_a_venue_provider():
    assert isinstance(_provider(FakeCcxtExchange()), VenueProvider)


# --- funding normalisation --------------------------------------------------


def test_funding_normalised_to_hourly_with_derived_8h_interval():
    ex = FakeCcxtExchange(
        funding=[_fr(T0, 0.0008), _fr(T0 + H8, 0.0016), _fr(T0 + 2 * H8, -0.0008)]
    )
    prov = _provider(ex)
    table = prov.fetch_range("SOL-PERP", Kind.FUNDING, TimeRange(T0, T0 + 3 * H8))

    assert table.column_names == [
        "ts", "rate_hourly", "interval_s", "price_basis", "rate_type", "venue", "market",
    ]
    assert table.column("ts").to_pylist() == [T0, T0 + H8, T0 + 2 * H8]
    # per-interval rate / 8h
    assert table.column("rate_hourly").to_pylist() == pytest.approx(
        [0.0001, 0.0002, -0.0001]
    )
    assert table.column("interval_s").to_pylist() == [28_800, 28_800, 28_800]
    assert set(table.column("price_basis").to_pylist()) == {"mark"}
    assert set(table.column("rate_type").to_pylist()) == {"final"}
    assert set(table.column("venue").to_pylist()) == {"binance"}
    assert set(table.column("market").to_pylist()) == {"SOL-PERP"}


def test_funding_span_is_half_open():
    ex = FakeCcxtExchange(
        funding=[_fr(T0, 0.0008), _fr(T0 + H8, 0.0008), _fr(T0 + 2 * H8, 0.0008)]
    )
    prov = _provider(ex)
    # end at T0+2*H8 excludes the settlement exactly on the boundary.
    table = prov.fetch_range("SOL-PERP", Kind.FUNDING, TimeRange(T0, T0 + 2 * H8))
    assert table.column("ts").to_pylist() == [T0, T0 + H8]


def test_funding_derives_4h_cadence_per_symbol():
    ex = FakeCcxtExchange(
        funding=[_fr(T0, 0.0004), _fr(T0 + H4, 0.0004), _fr(T0 + 2 * H4, 0.0004)]
    )
    prov = _provider(ex)
    table = prov.fetch_range("SOL-PERP", Kind.FUNDING, TimeRange(T0, T0 + 3 * H4))
    assert table.column("interval_s").to_pylist() == [14_400, 14_400, 14_400]
    # 0.0004 per 4h -> 0.0001 per hour
    assert table.column("rate_hourly").to_pylist() == pytest.approx(
        [0.0001, 0.0001, 0.0001]
    )


def test_funding_single_row_falls_back_to_default_interval():
    ex = FakeCcxtExchange(funding=[_fr(T0, 0.0008)])
    prov = _provider(ex)
    table = prov.fetch_range("SOL-PERP", Kind.FUNDING, TimeRange(T0, T0 + H8))
    assert table.column("interval_s").to_pylist() == [28_800]
    assert table.column("rate_hourly").to_pylist() == pytest.approx([0.0001])


def test_funding_skips_none_rates():
    ex = FakeCcxtExchange(
        funding=[_fr(T0, 0.0008), _fr(T0 + H8, None), _fr(T0 + 2 * H8, 0.0008)]
    )
    prov = _provider(ex)
    table = prov.fetch_range("SOL-PERP", Kind.FUNDING, TimeRange(T0, T0 + 3 * H8))
    assert table.column("ts").to_pylist() == [T0, T0 + 2 * H8]


def test_funding_pages_across_the_ccxt_limit():
    entries = [_fr(T0 + i * H8, 0.0008) for i in range(4)]
    ex = FakeCcxtExchange(funding=entries)
    prov = _provider(ex, page_limit=2)
    table = prov.fetch_range("SOL-PERP", Kind.FUNDING, TimeRange(T0, T0 + 5 * H8))
    assert table.column("ts").to_pylist() == [T0 + i * H8 for i in range(4)]
    # page1 (2 rows, ==limit -> continue), page2 (2 rows -> continue), page3 (empty -> stop)
    assert len(ex.funding_calls) == 3
    assert ex.funding_calls[0][1] == T0  # first ``since`` == span start


def test_empty_span_returns_typed_empty_table_without_calling_ccxt():
    ex = FakeCcxtExchange(funding=[_fr(T0, 0.0008)])
    prov = _provider(ex)
    table = prov.fetch_range("SOL-PERP", Kind.FUNDING, TimeRange(T0, T0))
    assert table.num_rows == 0
    assert "rate_hourly" in table.column_names
    assert ex.funding_calls == []


# --- capability + coverage --------------------------------------------------


def test_supports_and_coverage_floor():
    ex = FakeCcxtExchange()
    prov = _provider(ex, venue="binance")
    assert prov.supports("SOL-PERP", Kind.FUNDING) is True
    assert prov.supports("SOL-PERP", Kind.OI) is True
    assert prov.supports("SOL-PERP", Kind.CANDLES) is False
    assert prov.coverage_floor("SOL-PERP", Kind.FUNDING) == CEX_INCEPTION_MS["binance"]
    assert prov.coverage_floor("SOL-PERP", Kind.CANDLES) is None


def test_oi_unsupported_when_venue_lacks_capability():
    ex = FakeCcxtExchange(has={"fetchFundingRateHistory": True})
    prov = _provider(ex)
    assert prov.supports("SOL-PERP", Kind.OI) is False
    assert prov.coverage_floor("SOL-PERP", Kind.OI) is None
    # A request for an unsupported kind yields a typed-empty OI table, no fetch.
    table = prov.fetch_range("SOL-PERP", Kind.OI, TimeRange(T0, T0 + H8))
    assert table.num_rows == 0
    assert table.schema.field("oi") is not None
    assert ex.oi_calls == []


def test_unknown_venue_floor_is_none_unless_overridden():
    assert _provider(FakeCcxtExchange(), venue="kraken").coverage_floor(
        "SOL-PERP", Kind.FUNDING
    ) is None
    override = _provider(FakeCcxtExchange(), venue="kraken", coverage_floor_ms=T0)
    assert override.coverage_floor("SOL-PERP", Kind.FUNDING) == T0


# --- open interest (best-effort; only ``oi`` populated) ---------------------


def test_oi_populates_only_the_oi_column():
    ex = FakeCcxtExchange(oi=[_oi(T0, 1000.0), _oi(T0 + H, 1200.0)])
    prov = _provider(ex)
    table = prov.fetch_range("SOL-PERP", Kind.OI, TimeRange(T0, T0 + 2 * H))
    assert table.column("ts").to_pylist() == [T0, T0 + H]
    assert table.column("oi").to_pylist() == pytest.approx([1000.0, 1200.0])
    assert table.column("mark_price").to_pylist() == [0.0, 0.0]
    assert table.column("index_price").to_pylist() == [0.0, 0.0]
    assert table.column("funding_hourly").to_pylist() == [0.0, 0.0]


# --- backfill: quality bars + idempotent upsert -----------------------------


def test_backfill_upserts_into_sink_and_is_idempotent():
    ex = FakeCcxtExchange(
        funding=[_fr(T0, 0.0008), _fr(T0 + H8, 0.0016), _fr(T0 + 2 * H8, 0.0008)]
    )
    prov = _provider(ex)
    sink = InMemoryCacheSource()
    span = TimeRange(T0, T0 + 3 * H8)

    first = prov.backfill("SOL-PERP", Kind.FUNDING, span, sink)
    assert first.kind == str(Kind.FUNDING)
    assert first.rows_written == 3
    assert first.quality.ok

    held = sink.fetch("binance", "SOL-PERP", Kind.FUNDING, span)
    assert held.num_rows == 3

    # Re-running the same range must not grow the store (idempotent upsert by ts).
    prov.backfill("SOL-PERP", Kind.FUNDING, span, sink)
    assert sink.fetch("binance", "SOL-PERP", Kind.FUNDING, span).num_rows == 3


# --- composes into the free-venue chain (feeds the funding/basis lab) --------


def test_composes_into_free_venue_provider():
    ex = FakeCcxtExchange(funding=[_fr(T0, 0.0008), _fr(T0 + H8, 0.0008)])
    prov = _provider(ex, venue="binance", coverage_floor_ms=T0 - H8)
    chain = FreeVenueProvider(providers=[prov])
    want = TimeRange(T0, T0 + 2 * H8)

    avail = chain.available("binance", "SOL-PERP", Kind.FUNDING, want)
    assert avail.ranges == (want,)

    table = chain.fetch("binance", "SOL-PERP", Kind.FUNDING, want)
    assert table.column("ts").to_pylist() == [T0, T0 + H8]
    assert set(table.column("venue").to_pylist()) == {"binance"}
