"""BYO-license vendor lane — Tardis stub + local-only backfiller (2.7, §9.1, D23).

The vendor transport is a fake replaying recorded Tardis-shaped fragments (D26);
the key is resolved through a fake ``SecretsPort``. The D23 invariant under test:
a fetch requires the user's own key, and output lands only in the local cache.
"""

from __future__ import annotations

from typing import Any

import pytest

from flint.data import InMemoryCacheSource, Kind, TimeRange
from flint.data.ingest.vendors import (
    TardisFetcher,
    VendorBackfiller,
    VendorKeyMissing,
)
from flint.ports.tenant import TenantContext


class FakeSecrets:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get_secret(self, tenant: TenantContext, name: str) -> str | None:
        return self._secrets.get(name)


class FakeVendorTransport:
    def __init__(self, page: list[dict[str, Any]]) -> None:
        self._page = page
        self.calls: list[dict[str, Any]] = []

    def get_json(self, url, *, params=None, headers=None) -> Any:
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self._page


def _tenant() -> TenantContext:
    return TenantContext(tenant_id="t1")


def _trade(ts_us: int, price: float, amount: float, side: str, tid: int) -> dict[str, Any]:
    # Tardis timestamps are microseconds.
    return {"timestamp": ts_us, "price": price, "amount": amount, "side": side, "id": tid}


def _fetcher(page, secrets):
    return TardisFetcher(FakeVendorTransport(page), FakeSecrets(secrets), _tenant())


def test_fetch_without_byo_key_raises():
    fetcher = _fetcher([], secrets={})  # no TARDIS_API_KEY
    with pytest.raises(VendorKeyMissing):
        fetcher.fetch("hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(0, 10_000))


def test_fetch_normalizes_trades_to_the_canonical_schema():
    page = [
        _trade(1_000, 100.0, 2.0, "buy", 1),  # ts 1ms
        _trade(2_000, 101.0, 1.0, "sell", 2),  # ts 2ms
        _trade(9_000, 102.0, 1.0, "buy", 3),  # ts 9ms — outside [0,3)
    ]
    fetcher = _fetcher(page, secrets={"TARDIS_API_KEY": "byo-key"})
    table = fetcher.fetch("hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(0, 3))
    assert table.schema.names == ["ts", "market", "venue", "price", "size", "side", "trade_id"]
    assert table.column("ts").to_pylist() == [1, 2]  # microseconds -> ms, half-open
    assert table.column("side").to_pylist() == ["buy", "sell"]
    assert table.column("size").to_pylist() == [2.0, 1.0]


def test_fetcher_only_supports_trades():
    fetcher = _fetcher([], secrets={"TARDIS_API_KEY": "k"})
    assert fetcher.supports("hyperliquid", "SOL-PERP", Kind.TRADES) is True
    assert fetcher.supports("hyperliquid", "SOL-PERP", Kind.FUNDING) is False


def test_backfiller_writes_only_to_the_local_cache():
    page = [_trade(1_000, 100.0, 2.0, "buy", 1), _trade(2_000, 101.0, 1.0, "sell", 2)]
    fetcher = _fetcher(page, secrets={"TARDIS_API_KEY": "byo-key"})
    local = InMemoryCacheSource()
    backfiller = VendorBackfiller(fetcher, local)

    result = backfiller.backfill("hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(0, 10))
    assert result.rows_written == 2
    assert result.quality.ok
    # The rows landed in the user's local cache — the D23 local-only destination.
    held = local.fetch("hyperliquid", "SOL-PERP", Kind.TRADES, TimeRange(0, 10))
    assert held.column("ts").to_pylist() == [1, 2]


def test_backfiller_skips_unsupported_kind_without_fetching():
    fetcher = _fetcher([], secrets={"TARDIS_API_KEY": "k"})
    local = InMemoryCacheSource()
    result = VendorBackfiller(fetcher, local).backfill(
        "hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(0, 10)
    )
    assert result.rows_written == 0
    assert local.available("hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(0, 10)).is_empty
