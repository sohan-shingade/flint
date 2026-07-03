"""BYO-license vendor lane — D23 invariants on the Tardis CSV fetcher (§9.1, §9.2).

The transport is a fake replaying **recorded** Tardis day files (truncated real
first-of-month samples under ``tests/fixtures/tardis/``, D26); the key is
resolved through a fake ``SecretsPort``. The D23 invariants under test: a fetch
requires the user's own key (free first-of-month days excepted), and output
lands only in the local cache.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flint.data import InMemoryCacheSource, Kind, TimeRange
from flint.data.ingest.vendors import (
    TardisFetcher,
    VendorBackfiller,
    VendorKeyMissing,
)
from flint.ports.tenant import TenantContext

FIXTURES = Path(__file__).parent / "fixtures" / "tardis"
META_URL = "https://api.tardis.dev/v1/exchanges/hyperliquid"
TRADES_URL = "https://datasets.tardis.dev/v1/hyperliquid/trades/2026/06/01/SOL.csv.gz"

DAY1_MS = 1_780_272_000_000  # 2026-06-01T00:00:00Z — the fixtures' (free) day
DAY_MS = 86_400_000


class FakeSecrets:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get_secret(self, tenant: TenantContext, name: str) -> str | None:
        return self._secrets.get(name)


class FakeBytesTransport:
    """Recorded-bytes transport: unknown URL == vendor 404 (no file that day)."""

    def __init__(self, responses: dict[str, bytes]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_bytes(self, url, *, headers=None) -> bytes | None:
        self.calls.append((url, dict(headers or {})))
        return self._responses.get(url)


def _fetcher(secrets: dict[str, str], tmp_path: Path) -> TardisFetcher:
    responses = {
        META_URL: (FIXTURES / "exchange_hyperliquid.json").read_bytes(),
        TRADES_URL: (FIXTURES / "hyperliquid_trades_2026-06-01_SOL.csv.gz").read_bytes(),
    }
    return TardisFetcher(
        FakeBytesTransport(responses),
        FakeSecrets(secrets),
        TenantContext(tenant_id="t1"),
        meta_cache_dir=tmp_path / "_tardis",
    )


def test_fetch_without_byo_key_raises(tmp_path):
    # June 2 is not a free first-of-month file, so no key -> VendorKeyMissing.
    fetcher = _fetcher({}, tmp_path)
    with pytest.raises(VendorKeyMissing):
        fetcher.fetch(
            "hyperliquid",
            "SOL-PERP",
            Kind.TRADES,
            TimeRange(DAY1_MS + DAY_MS, DAY1_MS + 2 * DAY_MS),
        )


def test_fetcher_supports_the_six_tick_lane_kinds(tmp_path):
    fetcher = _fetcher({"TARDIS_API_KEY": "k"}, tmp_path)
    for kind in (
        Kind.TRADES,
        Kind.QUOTES,
        Kind.DEPTH,
        Kind.BOOK_DELTA,
        Kind.FUNDING,
        Kind.OI,
    ):
        assert fetcher.supports("hyperliquid", "SOL-PERP", kind) is True
    assert fetcher.supports("hyperliquid", "SOL-PERP", Kind.CANDLES) is False
    assert fetcher.supports("binance", "SOL-PERP", Kind.TRADES) is False


def test_backfiller_writes_only_to_the_local_cache(tmp_path):
    fetcher = _fetcher({"TARDIS_API_KEY": "byo-key"}, tmp_path)
    local = InMemoryCacheSource()
    backfiller = VendorBackfiller(fetcher, local)

    span = TimeRange(DAY1_MS, DAY1_MS + DAY_MS)
    result = backfiller.backfill("hyperliquid", "SOL-PERP", Kind.TRADES, span)
    assert result.rows_written == 300  # the truncated real day fragment
    assert result.quality.ok
    # The rows landed in the user's local cache — the D23 local-only destination.
    held = local.fetch("hyperliquid", "SOL-PERP", Kind.TRADES, span)
    assert held.num_rows > 0
    assert held.column("ts").to_pylist()[0] == 1_780_272_002_861  # µs -> ms


def test_backfiller_skips_unsupported_kind_without_fetching(tmp_path):
    fetcher = _fetcher({"TARDIS_API_KEY": "k"}, tmp_path)
    local = InMemoryCacheSource()
    result = VendorBackfiller(fetcher, local).backfill(
        "hyperliquid", "SOL-PERP", Kind.CANDLES, TimeRange(DAY1_MS, DAY1_MS + DAY_MS)
    )
    assert result.rows_written == 0
    assert local.available(
        "hyperliquid", "SOL-PERP", Kind.CANDLES, TimeRange(DAY1_MS, DAY1_MS + DAY_MS)
    ).is_empty
