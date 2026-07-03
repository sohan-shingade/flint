"""data.ingest.vendors — paid backfill lane (Tardis, Crypto Lake, Kaiko, Amberdata).

BYO-license only (D23): each fetcher runs on the *user's own* vendor key and its
output is written to the *user's local cache only* — vendor bytes never enter the
shared lake. v1 ships the interface (``VendorFetcher`` / ``VendorBackfiller``) and
the Tardis CSV-datasets adapter (§9.2); the other vendors slot in behind the same
interface.
"""

from .base import (
    LocalCacheSink,
    VendorBackfiller,
    VendorFetcher,
    VendorKeyMissing,
)
from .tardis import (
    DATASET_BY_KIND,
    TARDIS_API_BASE,
    TARDIS_DATASETS_BASE,
    HttpxBytesTransport,
    TardisBytesTransport,
    TardisCsvClient,
    TardisFetcher,
    TardisTransportError,
    TardisVendorSource,
)

__all__ = [
    "LocalCacheSink",
    "VendorBackfiller",
    "VendorFetcher",
    "VendorKeyMissing",
    "DATASET_BY_KIND",
    "TARDIS_API_BASE",
    "TARDIS_DATASETS_BASE",
    "HttpxBytesTransport",
    "TardisBytesTransport",
    "TardisCsvClient",
    "TardisFetcher",
    "TardisTransportError",
    "TardisVendorSource",
]
