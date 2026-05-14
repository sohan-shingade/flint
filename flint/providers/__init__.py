# Eager imports — core infrastructure needed everywhere.
from .base import CandleProvider
from .registry import DataProvider, ProviderRegistry, register, get_provider_class, list_providers

# Lazy imports — deferred so missing optional deps (ccxt, eth_account, …)
# don't break the whole package on import.
_LAZY_IMPORTS = {
    "BirdeyeProvider": (".birdeye", "BirdeyeProvider"),
    "CCXTMarketMapper": (".ccxt_markets", "CCXTMarketMapper"),
    "CCXTProvider": (".ccxt_provider", "CCXTProvider"),
    "DriftCandleProvider": (".drift_candles", "DriftCandleProvider"),
    "HyperliquidCandleProvider": (".hyperliquid_candles", "HyperliquidCandleProvider"),
    "DriftS3Provider": (".drift_s3", "DriftS3Provider"),
    "DriftDataProvider": (".drift_api", "DriftDataProvider"),
    "BinanceFundingProvider": (".funding_rates", "BinanceFundingProvider"),
    "BybitFundingProvider": (".funding_rates", "BybitFundingProvider"),
    "CrossVenueFunding": (".funding_rates", "CrossVenueFunding"),
    "DriftFundingProvider": (".funding_rates", "DriftFundingProvider"),
    "HyperliquidFundingProvider": (".funding_rates", "HyperliquidFundingProvider"),
    "OKXFundingProvider": (".funding_rates", "OKXFundingProvider"),
    "CoinGeckoProvider": (".coingecko", "CoinGeckoProvider"),
    "GeckoProvider": (".gecko", "GeckoProvider"),
    "HeliusProvider": (".helius", "HeliusProvider"),
    "JupiterProvider": (".jupiter", "JupiterProvider"),
    "OrcaProvider": (".orca", "OrcaProvider"),
    "DriftOpenInterestProvider": (".open_interest", "DriftOpenInterestProvider"),
    "PythProvider": (".pyth", "PythProvider"),
    "PythCandleProvider": (".pyth_candles", "PythCandleProvider"),
    "RaydiumProvider": (".raydium", "RaydiumProvider"),
    "JupiterBorrowCollector": (".jupiter_borrow", "JupiterBorrowCollector"),
    "DuneBorrowBackfill": (".jupiter_borrow", "DuneBorrowBackfill"),
    "RpcBorrowBackfill": (".jupiter_borrow", "RpcBorrowBackfill"),
    "JupiterBorrowProvider": (".jupiter_borrow", "JupiterBorrowProvider"),
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        mod_path, cls_name = _LAZY_IMPORTS[name]
        from importlib import import_module
        mod = import_module(mod_path, __package__)
        val = getattr(mod, cls_name)
        globals()[name] = val  # cache for subsequent access
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BinanceFundingProvider",
    "BirdeyeProvider",
    "BybitFundingProvider",
    "CandleProvider",
    "CCXTMarketMapper",
    "CCXTProvider",
    "CoinGeckoProvider",
    "CrossVenueFunding",
    "DataProvider",
    "DriftCandleProvider",
    "DriftDataProvider",
    "DriftFundingProvider",
    "DriftOpenInterestProvider",
    "DriftS3Provider",
    "DuneBorrowBackfill",
    "GeckoProvider",
    "HeliusProvider",
    "JupiterBorrowCollector",
    "JupiterBorrowProvider",
    "HyperliquidCandleProvider",
    "HyperliquidFundingProvider",
    "JupiterProvider",
    "OKXFundingProvider",
    "OrcaProvider",
    "ProviderRegistry",
    "PythCandleProvider",
    "PythProvider",
    "RaydiumProvider",
    "RpcBorrowBackfill",
    "get_provider_class",
    "list_providers",
    "register",
]
