"""Tardis CSV-datasets adapter — client, ledger-driven fetch, chain gating (§9.2, D23).

Every test runs off recorded bytes: the committed fixtures are **truncated real
first-of-month Tardis day files** for hyperliquid SOL, 2026-06-01 (free without
a key — D26 real fragments), plus a truncated real ``/exchanges/hyperliquid``
response. Hand-authored CSV fragments appear only where a shape the real
sample cannot show is needed (a populated ``funding_timestamp``, a zero-row
day, a non-numeric trade id). No test opens a socket.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from flint.adapters import InProcessJobRunner
from flint.data import Kind, TimeRange, local_data_manager
from flint.data.ingest.vendors import (
    HttpxBytesTransport,
    TardisCsvClient,
    TardisFetcher,
    TardisTransportError,
    TardisVendorSource,
    VendorKeyMissing,
)
from flint.data.normalize import (
    BOOK_DELTA_SCHEMA,
    DEPTH_SCHEMA,
    FUNDING_SCHEMA,
    OI_SCHEMA,
    QUOTES_SCHEMA,
    TRADES_SCHEMA,
)
from flint.data.store.coverage import CoverageLedger
from flint.data.store.durable_cache import DurableCacheSource
from flint.ports.jobs import ResourceQuota
from flint.ports.tenant import TenantContext
from flint.services import pull_data

FIXTURES = Path(__file__).parent / "fixtures" / "tardis"

META_URL = "https://api.tardis.dev/v1/exchanges/hyperliquid"
DATASETS = "https://datasets.tardis.dev/v1/hyperliquid"

DAY_MS = 86_400_000
DAY1_MS = 1_780_272_000_000  # 2026-06-01T00:00:00Z — the fixtures' day (a free 1st)
DAY2_MS = DAY1_MS + DAY_MS  # 2026-06-02 — not free
NOW_MS = DAY1_MS + 20 * DAY_MS  # a deterministic "now" inside the sample month

SOL_SINCE_MS = int(datetime(2024, 10, 29, tzinfo=UTC).timestamp() * 1000)


def _fx(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _gz(text: str) -> bytes:
    return gzip.compress(text.encode())


class FakeSecrets:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get_secret(self, tenant: TenantContext, name: str) -> str | None:
        return self._secrets.get(name)


class FakeBytesTransport:
    """Recorded-bytes transport; an unknown URL is a vendor 404 (None)."""

    def __init__(self, responses: dict[str, bytes]) -> None:
        self._responses = dict(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_bytes(self, url, *, headers=None) -> bytes | None:
        self.calls.append((url, dict(headers or {})))
        return self._responses.get(url)

    def dataset_urls(self) -> list[str]:
        return [u for u, _ in self.calls if u.startswith(DATASETS)]


def _tenant() -> TenantContext:
    return TenantContext(tenant_id="t1")


def _make_fetcher(
    tmp_path: Path,
    responses: dict[str, bytes],
    *,
    key: str | None = "byo-key",
    sink=None,
    ledger_root=None,
    on_day=None,
):
    transport = FakeBytesTransport({META_URL: _fx("exchange_hyperliquid.json"), **responses})
    fetcher = TardisFetcher(
        transport,
        FakeSecrets({"TARDIS_API_KEY": key} if key else {}),
        _tenant(),
        meta_cache_dir=tmp_path / "_tardis",
        sink=sink,
        ledger_root=ledger_root,
        now_ms=lambda: NOW_MS,
        on_day=on_day,
    )
    return transport, fetcher


# --- URL grammar -------------------------------------------------------------


def test_dataset_url_grammar():
    url = TardisCsvClient.dataset_url(
        "hyperliquid", "derivative_ticker", date(2026, 6, 1), "SOL"
    )
    assert url == f"{DATASETS}/derivative_ticker/2026/06/01/SOL.csv.gz"
    # Zero-padded months/days.
    url = TardisCsvClient.dataset_url("hyperliquid", "trades", date(2025, 12, 9), "BTC")
    assert url == "https://datasets.tardis.dev/v1/hyperliquid/trades/2025/12/09/BTC.csv.gz"


# --- gunzip + CSV -> Arrow against the frozen schemas (real fragments) --------


def test_parse_trades_real_fragment_us_to_ms():
    table = TardisCsvClient.parse(
        "trades",
        _fx("hyperliquid_trades_2026-06-01_SOL.csv.gz"),
        market="SOL-PERP",
        venue="hyperliquid",
    )[Kind.TRADES]
    assert table.schema.equals(TRADES_SCHEMA)
    assert table.num_rows == 300
    # Raw first row: timestamp=1780272002861000 µs, id=442510352591939.
    assert table["ts"][0].as_py() == 1_780_272_002_861  # µs -> ms, floor
    assert table["trade_id"][0].as_py() == 442_510_352_591_939  # int64, exact
    assert table["side"][0].as_py() == "sell"
    assert table["price"][0].as_py() == 82.424
    assert table["market"][0].as_py() == "SOL-PERP"
    ts = table["ts"].to_pylist()
    assert ts == sorted(ts)


def test_parse_quotes_real_fragment():
    table = TardisCsvClient.parse(
        "quotes",
        _fx("hyperliquid_quotes_2026-06-01_SOL.csv.gz"),
        market="SOL-PERP",
        venue="hyperliquid",
    )[Kind.QUOTES]
    assert table.schema.equals(QUOTES_SCHEMA)
    assert table.num_rows == 300
    # Raw first row: ask 82.425 x 10.53, bid 82.424 x 157.72 @ 1780272000996000 µs.
    assert table["ts"][0].as_py() == 1_780_272_000_996
    assert table["bid_px"][0].as_py() == 82.424
    assert table["bid_sz"][0].as_py() == 157.72
    assert table["ask_px"][0].as_py() == 82.425
    assert table["ask_sz"][0].as_py() == 10.53


def test_parse_book_l2_real_fragment():
    table = TardisCsvClient.parse(
        "incremental_book_L2",
        _fx("hyperliquid_incremental_book_L2_2026-06-01_SOL.csv.gz"),
        market="SOL-PERP",
        venue="hyperliquid",
    )[Kind.BOOK_DELTA]
    assert table.schema.equals(BOOK_DELTA_SCHEMA)
    assert table.num_rows == 300
    # seq is the day-file row position — the ts tie-break of the total ordering.
    assert table["seq"].to_pylist() == list(range(300))
    assert table["ts"][0].as_py() == 1_780_272_000_996
    assert table["local_ts"][0].as_py() == 1_780_272_003_663
    assert table["is_snapshot"][0].as_py() is True
    assert table["side"][0].as_py() == "ask"


def test_parse_book_snapshot_real_fragment():
    table = TardisCsvClient.parse(
        "book_snapshot_25",
        _fx("hyperliquid_book_snapshot_25_2026-06-01_SOL.csv.gz"),
        market="SOL-PERP",
        venue="hyperliquid",
    )[Kind.DEPTH]
    assert table.schema.equals(DEPTH_SCHEMA)
    assert table.num_rows == 50
    bids = table["bids"][0].as_py()
    asks = table["asks"][0].as_py()
    assert bids[0] == [82.424, 157.72] and asks[0] == [82.425, 10.53]
    # Best-first: bids descending, asks ascending.
    assert all(bids[i][0] > bids[i + 1][0] for i in range(len(bids) - 1))
    assert all(asks[i][0] < asks[i + 1][0] for i in range(len(asks) - 1))


# --- derivative_ticker split: FUNDING (capture-ts) + OI ------------------------


def test_derivative_ticker_splits_into_funding_and_oi():
    out = TardisCsvClient.parse(
        "derivative_ticker",
        _fx("hyperliquid_derivative_ticker_2026-06-01_SOL.csv.gz"),
        market="SOL-PERP",
        venue="hyperliquid",
    )
    funding, oi = out[Kind.FUNDING], out[Kind.OI]
    assert funding.schema.equals(FUNDING_SCHEMA)
    assert oi.schema.equals(OI_SCHEMA)
    # Every ticker row folds into OI; FUNDING rows only where the rate changes.
    assert oi.num_rows == 300
    assert funding.num_rows == 40
    rates = funding["rate_hourly"].to_pylist()
    assert all(rates[i] != rates[i + 1] for i in range(len(rates) - 1))
    row = {c: funding[c][0].as_py() for c in funding.column_names}
    assert row["rate_hourly"] == 1.25e-05  # HL's native hourly rate, unscaled
    assert row["interval_s"] == 3600
    assert row["price_basis"] == "oracle"
    assert row["rate_type"] == "predicted"
    # HL day files ship funding_timestamp empty -> null, never fabricated (D26).
    assert row["settlement_ts"] is None
    oi_row = {c: oi[c][0].as_py() for c in oi.column_names}
    assert oi_row["oi"] == pytest.approx(3_962_820.88)
    assert oi_row["mark_price"] == 81.985
    assert oi_row["index_price"] == 82.005
    assert oi_row["funding_hourly"] == 1.25e-05


def test_derivative_ticker_capture_ts_and_settlement_ts():
    # Hand-authored fragment (unit input): local_timestamp differs from the
    # exchange timestamp, and funding_timestamp is populated.
    csv = (
        "exchange,symbol,timestamp,local_timestamp,funding_timestamp,funding_rate,"
        "predicted_funding_rate,open_interest,last_price,index_price,mark_price\n"
        "hyperliquid,SOL,1000000,2000000,7200000000,0.001,,100.0,,10.0,10.1\n"
        "hyperliquid,SOL,3000000,4000000,7200000000,0.001,,101.0,,10.0,10.1\n"
        "hyperliquid,SOL,5000000,6000000,10800000000,0.002,,102.0,,10.0,10.1\n"
    )
    out = TardisCsvClient.parse(
        "derivative_ticker", _gz(csv), market="SOL-PERP", venue="hyperliquid"
    )
    funding, oi = out[Kind.FUNDING], out[Kind.OI]
    # ts is local_timestamp (capture — the §6.4 "known at" moment), µs -> ms.
    assert funding["ts"].to_pylist() == [2_000, 6_000]  # rate change rows only
    assert funding["settlement_ts"].to_pylist() == [7_200_000, 10_800_000]
    assert oi["ts"].to_pylist() == [2_000, 4_000, 6_000]  # every row folds into OI


def test_zero_row_day_file_parses_to_empty_tables():
    header_only = _gz(
        "exchange,symbol,timestamp,local_timestamp,id,side,price,amount\n"
    )
    table = TardisCsvClient.parse(
        "trades", header_only, market="SOL-PERP", venue="hyperliquid"
    )[Kind.TRADES]
    assert table.schema.equals(TRADES_SCHEMA)
    assert table.num_rows == 0


def test_non_numeric_trade_id_fails_loudly():
    # trade_id decision (D2): ids parse strictly to int64 — a non-numeric id is
    # a loud failure, never a silent hash. Documented in vendors/tardis.py.
    csv = (
        "exchange,symbol,timestamp,local_timestamp,id,side,price,amount\n"
        "hyperliquid,SOL,1000000,2000000,not-a-number,buy,10.0,1.0\n"
    )
    with pytest.raises(ValueError, match="unparsable Tardis CSV"):
        TardisCsvClient.parse("trades", _gz(csv), market="SOL-PERP", venue="hyperliquid")


# --- fetcher: day iteration, ledger marking, key handling ----------------------


def test_fetch_marks_landed_days_covered_including_zero_row_day(tmp_path):
    sink = DurableCacheSource(tmp_path)
    day2_url = f"{DATASETS}/trades/2026/06/02/SOL.csv.gz"
    responses = {
        f"{DATASETS}/trades/2026/06/01/SOL.csv.gz": _fx(
            "hyperliquid_trades_2026-06-01_SOL.csv.gz"
        ),
        # Day 2 landed but had no prints: a header-only file — a quiet market.
        day2_url: _gz("exchange,symbol,timestamp,local_timestamp,id,side,price,amount\n"),
    }
    _, fetcher = _make_fetcher(
        tmp_path, responses, sink=sink, ledger_root=tmp_path
    )
    span = TimeRange(DAY1_MS, DAY1_MS + 2 * DAY_MS)
    table = fetcher.fetch("hyperliquid", "SOL-PERP", Kind.TRADES, span)
    assert table.num_rows == 300  # day 1's rows; day 2 was quiet

    ledger = CoverageLedger(tmp_path / "trades" / "hyperliquid" / "SOL-PERP")
    covered = ledger.covered()
    # Both whole UTC days asserted — the zero-row day is data, not a gap (§9.2).
    assert covered.covers(TimeRange(DAY1_MS, DAY1_MS + 2 * DAY_MS))
    assert all(e.source == "tardis" for e in ledger.entries)
    # And the durable cache now serves the covered-but-quiet day as empty rows.
    assert sink.available(
        "hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(DAY2_MS, DAY2_MS + DAY_MS)
    ).covers(TimeRange(DAY2_MS, DAY2_MS + DAY_MS))


def test_fetch_skips_days_already_covered_in_the_ledger(tmp_path):
    sink = DurableCacheSource(tmp_path)
    # Day 1 already landed in a previous run.
    CoverageLedger(tmp_path / "trades" / "hyperliquid" / "SOL-PERP").assert_covered(
        TimeRange(DAY1_MS, DAY2_MS), "tardis"
    )
    responses = {
        f"{DATASETS}/trades/2026/06/02/SOL.csv.gz": _fx(
            "hyperliquid_trades_2026-06-01_SOL.csv.gz"  # recorded bytes stand in
        ),
    }
    transport, fetcher = _make_fetcher(
        tmp_path, responses, sink=sink, ledger_root=tmp_path
    )
    fetcher.fetch(
        "hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(DAY1_MS, DAY1_MS + 2 * DAY_MS)
    )
    urls = transport.dataset_urls()
    # Incremental resume: only the uncovered day was downloaded.
    assert urls == [f"{DATASETS}/trades/2026/06/02/SOL.csv.gz"]


def test_fetch_404_day_is_skipped_and_not_covered(tmp_path):
    sink = DurableCacheSource(tmp_path)
    responses = {
        f"{DATASETS}/trades/2026/06/01/SOL.csv.gz": _fx(
            "hyperliquid_trades_2026-06-01_SOL.csv.gz"
        ),
        # No entry for June 2 -> transport returns None (vendor 404).
    }
    _, fetcher = _make_fetcher(tmp_path, responses, sink=sink, ledger_root=tmp_path)
    table = fetcher.fetch(
        "hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(DAY1_MS, DAY1_MS + 2 * DAY_MS)
    )
    assert table.num_rows == 300
    covered = CoverageLedger(tmp_path / "trades" / "hyperliquid" / "SOL-PERP").covered()
    assert covered.covers(TimeRange(DAY1_MS, DAY2_MS))
    assert not covered.covers(TimeRange(DAY2_MS, DAY2_MS + DAY_MS))  # absent != quiet


def test_fetch_slices_to_span_but_persists_the_whole_day(tmp_path):
    sink = DurableCacheSource(tmp_path)
    responses = {
        f"{DATASETS}/trades/2026/06/01/SOL.csv.gz": _fx(
            "hyperliquid_trades_2026-06-01_SOL.csv.gz"
        ),
    }
    _, fetcher = _make_fetcher(tmp_path, responses, sink=sink, ledger_root=tmp_path)
    # The fixture's first prints: 3 rows before +5s into the day.
    span = TimeRange(DAY1_MS, DAY1_MS + 5_000)
    table = fetcher.fetch("hyperliquid", "SOL-PERP", Kind.TRADES, span)
    assert table.num_rows == 3
    assert all(DAY1_MS <= t < DAY1_MS + 5_000 for t in table["ts"].to_pylist())
    # The whole day landed in the local cache, backing the whole-day assertion.
    whole_day = sink.fetch(
        "hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(DAY1_MS, DAY2_MS)
    )
    assert whole_day.num_rows == 300
    covered = CoverageLedger(tmp_path / "trades" / "hyperliquid" / "SOL-PERP").covered()
    assert covered.covers(TimeRange(DAY1_MS, DAY2_MS))


def test_first_of_month_is_free_without_a_key(tmp_path):
    responses = {
        f"{DATASETS}/trades/2026/06/01/SOL.csv.gz": _fx(
            "hyperliquid_trades_2026-06-01_SOL.csv.gz"
        ),
    }
    transport, fetcher = _make_fetcher(tmp_path, responses, key=None)
    table = fetcher.fetch(
        "hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(DAY1_MS, DAY2_MS)
    )
    assert table.num_rows == 300
    # No Authorization header was sent for the free day.
    day_calls = [h for u, h in transport.calls if u.startswith(DATASETS)]
    assert day_calls and all("Authorization" not in h for h in day_calls)


def test_key_is_sent_as_bearer_when_present(tmp_path):
    responses = {
        f"{DATASETS}/trades/2026/06/01/SOL.csv.gz": _fx(
            "hyperliquid_trades_2026-06-01_SOL.csv.gz"
        ),
    }
    transport, fetcher = _make_fetcher(tmp_path, responses, key="byo-key")
    fetcher.fetch("hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(DAY1_MS, DAY2_MS))
    day_calls = [h for u, h in transport.calls if u.startswith(DATASETS)]
    assert day_calls and all(h["Authorization"] == "Bearer byo-key" for h in day_calls)


def test_ledger_root_without_sink_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="ledger_root requires a sink"):
        TardisFetcher(
            FakeBytesTransport({}),
            FakeSecrets({}),
            _tenant(),
            ledger_root=tmp_path,
        )


def test_on_day_reports_day_level_progress(tmp_path):
    sink = DurableCacheSource(tmp_path)
    responses = {
        f"{DATASETS}/trades/2026/06/01/SOL.csv.gz": _fx(
            "hyperliquid_trades_2026-06-01_SOL.csv.gz"
        ),
        f"{DATASETS}/trades/2026/06/02/SOL.csv.gz": _gz(
            "exchange,symbol,timestamp,local_timestamp,id,side,price,amount\n"
        ),
    }
    seen: list[tuple[str, Kind, int]] = []
    _, fetcher = _make_fetcher(
        tmp_path,
        responses,
        sink=sink,
        ledger_root=tmp_path,
        on_day=lambda d, k, n: seen.append((d, k, n)),
    )
    fetcher.fetch(
        "hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(DAY1_MS, DAY1_MS + 2 * DAY_MS)
    )
    assert seen == [
        ("2026-06-01", Kind.TRADES, 300),
        ("2026-06-02", Kind.TRADES, 0),
    ]


# --- per-symbol availability + the entitlement gate ----------------------------


def test_available_is_empty_without_a_key(tmp_path):
    # Coverage reflects entitlement (D23): keyless -> nothing advertised, even
    # though the metadata says the symbol has history. (Free first-of-month
    # days are deliberately not advertised keyless — see TardisVendorSource.)
    _, fetcher = _make_fetcher(tmp_path, {}, key=None)
    source = TardisVendorSource(fetcher, now_ms=lambda: NOW_MS)
    want = TimeRange(0, NOW_MS)
    assert source.available("hyperliquid", "SOL-PERP", Kind.TRADES, want).is_empty


def test_available_reports_per_symbol_floor_intersected_with_want(tmp_path):
    _, fetcher = _make_fetcher(tmp_path, {})
    source = TardisVendorSource(fetcher, now_ms=lambda: NOW_MS)
    want = TimeRange(0, NOW_MS)
    got = source.available("hyperliquid", "SOL-PERP", Kind.TRADES, want)
    bounds = got.bounds()
    assert bounds is not None
    assert bounds.start_ms == SOL_SINCE_MS  # 2024-10-29, the SOL floor
    assert bounds.end_ms <= NOW_MS
    # Per-symbol floors: 0G listed 2025-09-22, never the exchange-level date.
    zero_g = source.available("hyperliquid", "0G-PERP", Kind.TRADES, want)
    zg_since = int(datetime(2025, 9, 22, tzinfo=UTC).timestamp() * 1000)
    assert zero_g.bounds() is not None
    assert zero_g.bounds().start_ms == zg_since
    # A symbol absent from the metadata advertises nothing.
    assert source.available("hyperliquid", "DOGE-PERP", Kind.TRADES, want).is_empty
    # Unsupported kind advertises nothing.
    assert source.available("hyperliquid", "SOL-PERP", Kind.CANDLES, want).is_empty


def test_exchange_metadata_is_disk_cached_for_24h(tmp_path):
    transport1, fetcher1 = _make_fetcher(tmp_path, {})
    assert fetcher1.availability("SOL-PERP", Kind.TRADES) is not None
    assert fetcher1.availability("SOL-PERP", Kind.FUNDING) is not None
    meta_calls = [u for u, _ in transport1.calls if u == META_URL]
    assert len(meta_calls) == 1  # memoised within the instance

    # A fresh fetcher over the same store dir reads the disk cache, not the wire.
    transport2, fetcher2 = _make_fetcher(tmp_path, {})
    assert fetcher2.availability("SOL-PERP", Kind.TRADES) is not None
    assert [u for u, _ in transport2.calls if u == META_URL] == []

    cache_file = tmp_path / "_tardis" / "exchange_hyperliquid.json"
    assert cache_file.exists()
    doc = json.loads(cache_file.read_text())
    assert doc["payload"]["id"] == "hyperliquid"


# --- transport backoff ----------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content


class _FakeHttpClient:
    def __init__(self, statuses: list[int], content: bytes = b"payload") -> None:
        self._statuses = list(statuses)
        self._content = content
        self.calls = 0

    def get(self, url, headers=None) -> _FakeResponse:
        self.calls += 1
        status = self._statuses.pop(0)
        return _FakeResponse(status, self._content if status == 200 else b"")


def test_backoff_retries_429_then_succeeds():
    sleeps: list[float] = []
    client = _FakeHttpClient([429, 429, 200])
    transport = HttpxBytesTransport(
        client=client, max_attempts=5, backoff_base_s=1.0, sleep=sleeps.append
    )
    assert transport.get_bytes("https://x/y") == b"payload"
    assert client.calls == 3
    assert sleeps == [1.0, 2.0]  # exponential: base * 2**attempt


def test_backoff_exhaustion_raises():
    sleeps: list[float] = []
    client = _FakeHttpClient([500, 502, 503])
    transport = HttpxBytesTransport(
        client=client, max_attempts=3, backoff_base_s=0.5, sleep=sleeps.append
    )
    with pytest.raises(TardisTransportError, match="after 3 attempts"):
        transport.get_bytes("https://x/y")
    assert sleeps == [0.5, 1.0]  # no sleep after the last attempt


def test_transport_maps_404_to_none_and_other_statuses_raise():
    assert (
        HttpxBytesTransport(client=_FakeHttpClient([404])).get_bytes("https://x/y")
        is None
    )
    with pytest.raises(TardisTransportError, match="HTTP 403"):
        HttpxBytesTransport(client=_FakeHttpClient([403])).get_bytes("https://x/y")


def test_fetch_without_key_on_non_free_day_raises(tmp_path):
    _, fetcher = _make_fetcher(tmp_path, {}, key=None)
    with pytest.raises(VendorKeyMissing):
        fetcher.fetch(
            "hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(DAY2_MS, DAY2_MS + DAY_MS)
        )


# --- the chain: TardisVendorSource behind the durable cache --------------------


def _chain_manager(tmp_path: Path, responses: dict[str, bytes], **kwargs):
    transport = FakeBytesTransport(
        {META_URL: _fx("exchange_hyperliquid.json"), **responses}
    )
    manager = local_data_manager(
        tmp_path,
        tardis=True,
        secrets=FakeSecrets({"TARDIS_API_KEY": "byo-key"}),
        tenant=_tenant(),
        tardis_transport=transport,
        **kwargs,
    )
    return transport, manager


def test_pull_data_drives_a_tardis_backfill_and_caches_it(tmp_path):
    responses = {
        f"{DATASETS}/trades/2026/06/01/SOL.csv.gz": _fx(
            "hyperliquid_trades_2026-06-01_SOL.csv.gz"
        ),
    }
    progress: list[tuple[str, Kind, int]] = []
    transport, manager = _chain_manager(
        tmp_path, responses, tardis_on_day=lambda d, k, n: progress.append((d, k, n))
    )

    # A multi-day backfill as a tracked job: pull_data through the JobRunner,
    # with day-level progress from the fetcher's on_day hook.
    runner = InProcessJobRunner()
    payload = runner.submit(
        _tenant(),
        lambda: pull_data(
            data=manager,
            market="SOL-PERP",
            venues=["hyperliquid"],
            kind=Kind.TRADES,
            start_ms=DAY1_MS,
            end_ms=DAY2_MS,
        ),
        ResourceQuota.default(),
    )
    assert payload["rows"] == {"hyperliquid/SOL-PERP/trades": 300}
    assert progress == [("2026-06-01", Kind.TRADES, 300)]

    # The day landed durably: a fresh manager over the same root serves it
    # entirely from the local cache — no dataset URL is fetched again.
    transport2, manager2 = _chain_manager(tmp_path, {})
    payload2 = pull_data(
        data=manager2,
        market="SOL-PERP",
        venues=["hyperliquid"],
        kind=Kind.TRADES,
        start_ms=DAY1_MS,
        end_ms=DAY2_MS,
    )
    assert payload2["rows"] == {"hyperliquid/SOL-PERP/trades": 300}
    assert transport2.dataset_urls() == []


def test_default_manager_construction_is_unchanged():
    # Opt-in only: a bare DataManager has no Tardis tier and no durable store.
    from flint.data import DataManager

    manager = DataManager()
    assert [type(s).__name__ for s in manager._sources] == [
        "InMemoryCacheSource",
        "FlintDataAPIClient",
        "FreeVenueProvider",
    ]


def test_tardis_chain_requires_secrets_and_tenant(tmp_path):
    with pytest.raises(ValueError, match="requires secrets= and tenant="):
        local_data_manager(tmp_path, tardis=True)
