"""Hyperliquid REST provider + S3 archive backfiller (2.4, §9.1, D28).

Every fixture is a hand-authored fragment matching Hyperliquid's documented
``/info`` responses (``candleSnapshot`` / ``fundingHistory``) and its S3 archive
format (newline-delimited JSON ``l2Book`` / ``asset_ctxs``). No test opens a
socket: the ``HttpTransport`` and ``ObjectStore`` seams are driven by fakes
(D26 — fixtures are recorded fragments / unit inputs, never generated data).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from flint.data import (
    DataManager,
    FreeVenueProvider,
    FundingCoverageError,
    InMemoryCacheSource,
    Kind,
    TimeRange,
    VenueProvider,
)
from flint.data.ingest.backfillers import (
    HL_INCEPTION_MS,
    HyperliquidRestProvider,
    HyperliquidS3Backfiller,
    coin_of,
    interval_of,
)
from flint.data.ingest.backfillers.hyperliquid import _MAX_CANDLES


def _ms(y: int, m: int, d: int, h: int = 0) -> int:
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp() * 1000)


T0 = _ms(2025, 1, 1)
HOUR = 3_600_000
MIN = 60_000


class FakeTransport:
    """Returns queued ``/info`` responses in order; records every request body."""

    def __init__(self, pages: list[Any]) -> None:
        self._pages = list(pages)
        self.requests: list[Mapping[str, Any]] = []

    def post_json(self, url: str, body: Mapping[str, Any]) -> Any:
        self.requests.append(body)
        return self._pages.pop(0) if self._pages else []


class FakeObjectStore:
    """Serves raw (uncompressed) bytes for known keys; None for missing ones."""

    def __init__(self, blobs: Mapping[str, bytes]) -> None:
        self._blobs = dict(blobs)

    def get(self, key: str) -> bytes | None:
        return self._blobs.get(key)


def _candle(t: int, o: str, h: str, low: str, c: str, v: str) -> dict[str, Any]:
    return {"t": t, "T": t + MIN, "s": "SOL", "i": "1m", "o": o, "h": h, "l": low,
            "c": c, "v": v, "n": 1}


# --- helpers -----------------------------------------------------------------


def test_coin_and_interval_mapping():
    assert coin_of("SOL-PERP") == "SOL"
    assert coin_of("kPEPE-PERP") == "kPEPE"
    assert interval_of(60) == "1m"
    assert interval_of(3600) == "1h"
    with pytest.raises(ValueError):
        interval_of(7)


# --- candles -----------------------------------------------------------------


def test_candles_normalize_to_core_schema():
    page = [_candle(T0, "100", "101", "99", "100.5", "10"),
            _candle(T0 + MIN, "100.5", "102", "100", "101", "12")]
    provider = HyperliquidRestProvider(FakeTransport([page]), resolution_s=60)
    table = provider.fetch_range("SOL-PERP", Kind.CANDLES, TimeRange(T0, T0 + 2 * MIN))

    assert table.column_names == [
        "ts", "open", "high", "low", "close", "volume", "market",
        "resolution_s", "venue",
    ]
    assert table.column("ts").to_pylist() == [T0, T0 + MIN]
    assert table.column("close").to_pylist() == [100.5, 101.0]
    assert table.column("market").to_pylist() == ["SOL-PERP", "SOL-PERP"]
    assert table.column("venue").to_pylist() == ["hyperliquid", "hyperliquid"]
    assert table.column("resolution_s").to_pylist() == [60, 60]


def test_candles_drop_rows_outside_the_requested_span():
    # HL returns a candle at the exclusive end; it must be excluded (half-open).
    page = [_candle(T0, "1", "1", "1", "1", "1"),
            _candle(T0 + MIN, "2", "2", "2", "2", "2")]
    provider = HyperliquidRestProvider(FakeTransport([page]), resolution_s=60)
    table = provider.fetch_range("SOL-PERP", Kind.CANDLES, TimeRange(T0, T0 + MIN))
    assert table.column("ts").to_pylist() == [T0]


def test_candles_page_around_the_5000_candle_cap():
    # A full page (== the cap) forces a second request; a short page ends paging.
    first = [_candle(T0 + i * MIN, "1", "1", "1", "1", "1") for i in range(_MAX_CANDLES)]
    second = [_candle(T0 + _MAX_CANDLES * MIN, "9", "9", "9", "9", "9")]
    transport = FakeTransport([first, second])
    provider = HyperliquidRestProvider(transport, resolution_s=60)

    end = T0 + (_MAX_CANDLES + 1) * MIN
    table = provider.fetch_range("SOL-PERP", Kind.CANDLES, TimeRange(T0, end))

    assert table.num_rows == _MAX_CANDLES + 1
    # Two requests were made; the second advanced past the last candle of page 1.
    assert len(transport.requests) == 2
    assert transport.requests[1]["req"]["startTime"] == T0 + _MAX_CANDLES * MIN


def test_candles_stop_on_empty_page_without_infinite_loop():
    provider = HyperliquidRestProvider(FakeTransport([[]]), resolution_s=60)
    table = provider.fetch_range("SOL-PERP", Kind.CANDLES, TimeRange(T0, T0 + 10 * MIN))
    assert table.num_rows == 0


# --- funding -----------------------------------------------------------------


def test_funding_normalizes_hl_semantics():
    page = [{"coin": "SOL", "fundingRate": "0.0000125", "premium": "0.0", "time": T0},
            {"coin": "SOL", "fundingRate": "-0.00001", "premium": "0.0", "time": T0 + HOUR}]
    provider = HyperliquidRestProvider(FakeTransport([page]), resolution_s=3600)
    table = provider.fetch_range("SOL-PERP", Kind.FUNDING, TimeRange(T0, T0 + 2 * HOUR))

    assert table.column("rate_hourly").to_pylist() == [1.25e-05, -1e-05]
    # HL funding is hourly, oracle-priced, and the history endpoint is settled.
    assert set(table.column("interval_s").to_pylist()) == {3600}
    assert set(table.column("price_basis").to_pylist()) == {"oracle"}
    assert set(table.column("rate_type").to_pylist()) == {"final"}
    assert set(table.column("market").to_pylist()) == {"SOL-PERP"}


# --- VenueProvider protocol + coverage floor + chain wiring ------------------


def test_provider_satisfies_venue_provider_protocol():
    provider = HyperliquidRestProvider(FakeTransport([]))
    assert isinstance(provider, VenueProvider)
    assert provider.supports("SOL-PERP", Kind.CANDLES)
    assert provider.supports("SOL-PERP", Kind.FUNDING)
    assert not provider.supports("SOL-PERP", Kind.DEPTH)
    assert provider.coverage_floor("SOL-PERP", Kind.CANDLES) == HL_INCEPTION_MS
    assert provider.coverage_floor("SOL-PERP", Kind.DEPTH) is None


def test_free_provider_serves_funding_through_the_chain_and_writes_through():
    page = [{"coin": "SOL", "fundingRate": "0.0000125", "time": T0},
            {"coin": "SOL", "fundingRate": "-0.00001", "time": T0 + HOUR}]
    hl = HyperliquidRestProvider(FakeTransport([page]), resolution_s=3600)
    cache = InMemoryCacheSource()
    dm = DataManager(sources=[cache, FreeVenueProvider([hl])])

    prepared = dm.prepare(
        ["SOL-PERP"], ["hyperliquid"], [Kind.FUNDING], TimeRange(T0, T0 + 2 * HOUR)
    )
    assert prepared.fidelity.all_full
    assert not prepared.clipped
    # Fetched funding was written through to the local cache tier.
    cached = cache.fetch("hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(T0, T0 + 2 * HOUR))
    assert cached.num_rows == 2


def test_coverage_floor_makes_a_pre_inception_request_reject():
    # A request entirely before HL existed is a funding gap, not a silent pass.
    hl = HyperliquidRestProvider(FakeTransport([]), resolution_s=3600)
    dm = DataManager(sources=[InMemoryCacheSource(), FreeVenueProvider([hl])])
    before = HL_INCEPTION_MS - 10 * HOUR
    with pytest.raises(FundingCoverageError):
        dm.prepare(
            ["SOL-PERP"], ["hyperliquid"], [Kind.FUNDING],
            TimeRange(before, before + HOUR),
        )


def test_free_provider_is_inert_without_registered_providers():
    free = FreeVenueProvider()
    want = TimeRange(T0, T0 + HOUR)
    assert free.available("hyperliquid", "SOL-PERP", Kind.FUNDING, want).is_empty
    assert free.fetch("hyperliquid", "SOL-PERP", Kind.FUNDING, want).num_rows == 0


# --- S3 backfiller: l2Book depth ---------------------------------------------


def _book_line(t: int, bids: list[list[str]], asks: list[list[str]]) -> str:
    return json.dumps(
        {
            "time": t,
            "coin": "SOL",
            "levels": [
                [{"px": p, "sz": s} for p, s in bids],
                [{"px": p, "sz": s} for p, s in asks],
            ],
        }
    )


def test_backfill_l2book_normalizes_and_upserts_with_gaps():
    key = HyperliquidS3Backfiller.l2book_key("SOL-PERP", "20250101", 0)
    blob = "\n".join(
        [
            _book_line(T0 + 1000, [["100.0", "5"]], [["100.1", "3"]]),
            _book_line(T0 + 2000, [["100.0", "6"]], [["100.2", "2"]]),
        ]
    )
    sink = InMemoryCacheSource()
    bf = HyperliquidS3Backfiller(FakeObjectStore({key: blob.encode()}), sink)

    # Hour 0 present, hour 1 missing -> hour 1 recorded as a gap.
    result = bf.backfill_l2book("SOL-PERP", "20250101", [0, 1])
    assert result.rows_written == 2
    assert result.quality.ok
    assert result.gaps.total_ms == HOUR

    depth = sink.fetch("hyperliquid", "SOL-PERP", Kind.DEPTH, TimeRange(T0, T0 + HOUR))
    assert depth.column("ts").to_pylist() == [T0 + 1000, T0 + 2000]
    assert depth.column("bids").to_pylist()[0] == [[100.0, 5.0]]
    assert depth.column("asks").to_pylist()[0] == [[100.1, 3.0]]


def test_backfill_l2book_is_idempotent():
    key = HyperliquidS3Backfiller.l2book_key("SOL-PERP", "20250101", 0)
    blob = _book_line(T0 + 1000, [["100.0", "5"]], [["100.1", "3"]])
    sink = InMemoryCacheSource()
    bf = HyperliquidS3Backfiller(FakeObjectStore({key: blob.encode()}), sink)

    bf.backfill_l2book("SOL-PERP", "20250101", [0])
    bf.backfill_l2book("SOL-PERP", "20250101", [0])  # re-run
    depth = sink.fetch("hyperliquid", "SOL-PERP", Kind.DEPTH, TimeRange(T0, T0 + HOUR))
    assert depth.num_rows == 1  # upsert keyed (venue, market, ts) — no duplicate


def test_backfill_l2book_all_hours_missing_writes_nothing():
    sink = InMemoryCacheSource()
    bf = HyperliquidS3Backfiller(FakeObjectStore({}), sink)
    result = bf.backfill_l2book("SOL-PERP", "20250101", [0, 1, 2])
    assert result.rows_written == 0
    assert result.gaps.total_ms == 3 * HOUR


# --- S3 backfiller: asset_ctxs (OI / mark / oracle) --------------------------


def test_backfill_asset_ctxs_splits_per_market():
    key = HyperliquidS3Backfiller.asset_ctxs_key("20250101")
    blob = "\n".join(
        [
            json.dumps({"time": T0, "coin": "SOL", "openInterest": "1234.5",
                        "markPx": "100.05", "oraclePx": "100.0", "funding": "0.0000125"}),
            json.dumps({"time": T0, "coin": "ETH", "openInterest": "50.0",
                        "markPx": "3000.0", "oraclePx": "2999.0", "funding": "0.00001"}),
        ]
    )
    sink = InMemoryCacheSource()
    bf = HyperliquidS3Backfiller(FakeObjectStore({key: blob.encode()}), sink)

    results = bf.backfill_asset_ctxs(["SOL-PERP", "BTC-PERP"], "20250101")
    assert results["SOL-PERP"].rows_written == 1
    assert results["BTC-PERP"].rows_written == 0  # not present in the file

    oi = sink.fetch("hyperliquid", "SOL-PERP", Kind.OI, TimeRange(T0, T0 + HOUR)).to_pylist()
    assert oi == [
        {"ts": T0, "market": "SOL-PERP", "venue": "hyperliquid", "oi": 1234.5,
         "mark_price": 100.05, "index_price": 100.0, "funding_hourly": 1.25e-05}
    ]


def test_backfiller_decodes_lz4_codec_when_selected(monkeypatch):
    # Prove the codec seam calls lz4 decompression only when codec="lz4".
    import sys
    import types

    calls: list[bytes] = []
    fake_lz4 = types.ModuleType("lz4")
    fake_frame = types.ModuleType("lz4.frame")

    def _decompress(raw: bytes) -> bytes:
        calls.append(raw)
        return _book_line(T0 + 1000, [["1", "1"]], [["2", "1"]]).encode()

    fake_frame.decompress = _decompress  # type: ignore[attr-defined]
    fake_lz4.frame = fake_frame  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lz4", fake_lz4)
    monkeypatch.setitem(sys.modules, "lz4.frame", fake_frame)

    key = HyperliquidS3Backfiller.l2book_key("SOL-PERP", "20250101", 0)
    bf = HyperliquidS3Backfiller(
        FakeObjectStore({key: b"\x00compressed"}), InMemoryCacheSource(), codec="lz4"
    )
    result = bf.backfill_l2book("SOL-PERP", "20250101", [0])
    assert result.rows_written == 1
    assert calls == [b"\x00compressed"]
