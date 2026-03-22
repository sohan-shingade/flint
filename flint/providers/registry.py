"""Configurable provider registry.

Providers register themselves and can be enabled/disabled via flint.yaml or CLI.
"""
from __future__ import annotations

import abc
import logging
from typing import Dict, List, Optional, Type

logger = logging.getLogger(__name__)


class DataProvider(abc.ABC):
    """Base class for all data providers."""

    name: str = ""
    requires_api_key: bool = False

    def is_available(self) -> bool:
        """Return True if this provider can be used (key set, reachable, etc.).

        Default implementation returns True for providers that don't require
        API keys, and False otherwise (subclasses should override).
        """
        return not self.requires_api_key

    def supported_data_types(self) -> List[str]:
        """Return list of data types: 'candles', 'funding', 'orderbook', 'liquidations', etc.

        Default implementation returns the class-level list if one was defined
        (for backward compatibility with providers that set it as a ClassVar).
        """
        # Backward compat: some providers set supported_data_types as a ClassVar list
        cls_val = type(self).__dict__.get("supported_data_types")
        if isinstance(cls_val, list):
            return cls_val
        return []

    @abc.abstractmethod
    def close(self) -> None:
        """Clean up resources."""
        ...


class ProviderRegistry:
    """Manages provider lifecycle and configuration."""

    def __init__(self) -> None:
        self._providers: Dict[str, DataProvider] = {}
        self._enabled: set = set()
        self._config: Dict[str, dict] = {}

    def register(self, provider: DataProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> Optional[DataProvider]:
        return self._providers.get(name)

    def enable(self, name: str) -> None:
        self._enabled.add(name)

    def disable(self, name: str) -> None:
        self._enabled.discard(name)

    def is_enabled(self, name: str) -> bool:
        return name in self._enabled

    def list_providers(self) -> List[str]:
        return list(self._providers.keys())

    def list_enabled(self) -> List[str]:
        return [n for n in self._providers if n in self._enabled]

    def get_providers_for(self, data_type: str) -> List[DataProvider]:
        """Return enabled providers that support a given data type."""
        result = []
        for name in self._enabled:
            p = self._providers.get(name)
            if p and data_type in p.supported_data_types():
                result.append(p)
        return result

    def load_config(self, providers_config: dict) -> None:
        """Load enabled/disabled state from flint.yaml providers section."""
        self._config = providers_config
        for name, cfg in providers_config.items():
            if isinstance(cfg, dict) and cfg.get("enabled", False):
                self._enabled.add(name)
            elif isinstance(cfg, bool) and cfg:
                self._enabled.add(name)

    def get_provider_config(self, name: str) -> dict:
        return self._config.get(name, {})

    def status(self) -> List[dict]:
        """Return status of all registered providers."""
        result = []
        for name, p in self._providers.items():
            available = False
            try:
                available = p.is_available()
            except Exception:
                pass
            result.append({
                "name": name,
                "enabled": name in self._enabled,
                "available": available,
                "requires_api_key": p.requires_api_key,
                "data_types": p.supported_data_types() if callable(p.supported_data_types) else p.supported_data_types,
            })
        return result

    def close_all(self) -> None:
        for p in self._providers.values():
            try:
                p.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Backward-compatible module-level registry (decorator + lookup functions)
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, Type[DataProvider]] = {}


def register(cls: Type[DataProvider]) -> Type[DataProvider]:
    """Class decorator that adds a DataProvider subclass to the registry."""
    if cls.name:
        _REGISTRY[cls.name] = cls
    return cls


def get_provider_class(name: str) -> Type[DataProvider]:
    """Return the DataProvider subclass registered under *name*."""
    return _REGISTRY[name]


def list_providers() -> List[str]:
    """Return names of all registered providers."""
    return sorted(_REGISTRY.keys())
