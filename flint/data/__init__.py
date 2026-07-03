"""data — on-demand data layer: DataManager source chain (local cache -> Flint Data API -> providers), UniverseResolver, livefeed, ingestion, store (§9, §17)."""

from .manager import (
    CoverageMode,
    DataManager,
    FidelityEntry,
    FidelitySummary,
    FundingCoverageError,
    Leg,
    PreparedData,
)
from .ranges import Kind, RangeSet, TimeRange
from .sources import (
    DataSource,
    FlintDataAPIClient,
    FreeVenueProvider,
    InMemoryCacheSource,
)

__all__ = [
    "CoverageMode",
    "DataManager",
    "FidelityEntry",
    "FidelitySummary",
    "FundingCoverageError",
    "Leg",
    "PreparedData",
    "Kind",
    "RangeSet",
    "TimeRange",
    "DataSource",
    "FlintDataAPIClient",
    "FreeVenueProvider",
    "InMemoryCacheSource",
]
