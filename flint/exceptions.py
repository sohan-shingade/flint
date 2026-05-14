"""Flint exception hierarchy.

All Flint-specific exceptions inherit from :class:`FlintError` so
callers can ``except FlintError`` as a catch-all.
"""


class FlintError(Exception):
    """Base exception for all Flint errors."""


class StoreError(FlintError):
    """Database/store operation failed."""


class ProviderError(FlintError):
    """Data provider operation failed."""


class StrategyError(FlintError):
    """Strategy loading or execution error."""


class ConfigError(FlintError):
    """Configuration validation error."""


class BacktestError(FlintError):
    """Backtest execution error."""
