from .base import BaseConnector

__all__ = ["BaseConnector", "HyperliquidClient"]


def __getattr__(name: str):
    if name == "HyperliquidClient":
        from .hyperliquid import HyperliquidClient
        return HyperliquidClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
