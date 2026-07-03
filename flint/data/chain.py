"""Opt-in local source-chain assembly — the D2 chain-integration point (§9.2).

The default ``DataManager()`` (bare ``Lab()``, bare API app) is **unchanged**:
in-memory cache → Flint Data API stub → inert free-venue tier. This module is
the explicit, config-driven step up: a *durable* Parquet cache plus, when the
caller opts in, the Tardis vendor tier appended **after** the free-venue
provider (§9.2 chain order — the free tier is always preferred; the paid
vendor only serves what nothing free can).

D23 wiring is structural here: the Tardis fetcher's ``sink`` *is* the durable
local cache and its coverage ledgers live in the same store root, so vendor
bytes land day-by-day in the user's local cache only, each day asserted in the
``CoverageLedger`` only after its rows persisted.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from ..ports.secrets import SecretsPort
from ..ports.tenant import TenantContext
from .ingest.vendors.tardis import (
    HttpxBytesTransport,
    TardisBytesTransport,
    TardisFetcher,
    TardisVendorSource,
)
from .manager import DataManager
from .sources import FlintDataAPIClient, FreeVenueProvider, VenueProvider
from .ranges import Kind
from .store.durable_cache import DurableCacheSource
from .store.layout import MigrationRegistry


def local_data_manager(
    root: str | Path,
    *,
    providers: Sequence[VenueProvider] = (),
    tardis: bool = False,
    secrets: SecretsPort | None = None,
    tenant: TenantContext | None = None,
    tardis_transport: TardisBytesTransport | None = None,
    tardis_on_day: Callable[[str, Kind, int], None] | None = None,
    registry: MigrationRegistry | None = None,
) -> DataManager:
    """A DataManager over a durable local store at ``root``, Tardis optional.

    Chain: durable cache → Flint Data API (stub) → free venue providers
    [→ Tardis vendor tier]. Enabling ``tardis=True`` requires ``secrets`` +
    ``tenant`` (the BYO ``TARDIS_API_KEY`` is a per-tenant secret, D23);
    ``tardis_transport`` is injectable for tests (recorded gzip bytes, D26).

    ``tardis_on_day(date_iso, kind, rows)`` fires once per landed vendor day —
    the day-level progress hook: submit ``services.data.pull_data`` over this
    manager through a ``JobRunnerPort`` and wire this callback to the job's
    progress, and a multi-day Tardis backfill is a tracked job with day-level
    progress (the ledger makes re-runs resume instead of re-downloading).
    """
    root = Path(root)
    cache = DurableCacheSource(root, registry=registry)
    sources = [cache, FlintDataAPIClient(), FreeVenueProvider(providers)]
    if tardis:
        if secrets is None or tenant is None:
            raise ValueError(
                "tardis=True requires secrets= and tenant= — the TARDIS_API_KEY "
                "is a per-tenant BYO secret (D23)"
            )
        fetcher = TardisFetcher(
            tardis_transport or HttpxBytesTransport(),
            secrets,
            tenant,
            meta_cache_dir=root / "_tardis",
            sink=cache,
            ledger_root=root,
            on_day=tardis_on_day,
        )
        sources.append(TardisVendorSource(fetcher))
    return DataManager(sources)
