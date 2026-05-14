"""DuckDB-backed store package.

All external code imports ``from flint.store import FlintStore``.
"""
from .store import FlintStore

__all__ = ["FlintStore"]
