"""data — on-demand data layer: DataManager source chain (local cache -> Flint Data API -> providers), UniverseResolver, livefeed, ingestion, store (§9, §17)."""

from .api_client import HttpxLakeHttp, LakeAPIClient, LakeHttp
from .chain import local_data_manager
from .manager import (
    CoverageMode,
    DataManager,
    FidelityEntry,
    FidelitySummary,
    FundingCoverageError,
    GranularityUnavailableError,
    Leg,
    PreparedData,
)
from .tiers import GRANULARITIES, TIERS, Tier
from .migrate import MigrationReport, MigrationSink, migrate_legacy_duckdb
from .normalize import AssetContext, TradePrint
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
    "GranularityUnavailableError",
    "Leg",
    "PreparedData",
    "GRANULARITIES",
    "TIERS",
    "Tier",
    "MigrationReport",
    "MigrationSink",
    "migrate_legacy_duckdb",
    "AssetContext",
    "TradePrint",
    "Kind",
    "RangeSet",
    "TimeRange",
    "DataSource",
    "FlintDataAPIClient",
    "FreeVenueProvider",
    "InMemoryCacheSource",
    "VenueProvider",
    "HttpxLakeHttp",
    "LakeAPIClient",
    "LakeHttp",
    "local_data_manager",
    "UNIVERSE_SNAPSHOT",
    "DynamicUniverse",
    "ExitBehavior",
    "InMemoryRankingData",
    "Membership",
    "RankingData",
    "StaticUniverse",
    "UniverseResolver",
]
