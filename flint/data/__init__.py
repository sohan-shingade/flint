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
    VenueProvider,
)
from .universe import (
    UNIVERSE_SNAPSHOT,
    DynamicUniverse,
    ExitBehavior,
    InMemoryRankingData,
    Membership,
    RankingData,
    StaticUniverse,
    UniverseResolver,
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
    "VenueProvider",
    "UNIVERSE_SNAPSHOT",
    "DynamicUniverse",
    "ExitBehavior",
    "InMemoryRankingData",
    "Membership",
    "RankingData",
    "StaticUniverse",
    "UniverseResolver",
]
