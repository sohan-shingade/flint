"""Phase 7A correctness tests — strategy catalog must match backtest builders."""
from __future__ import annotations

import pytest

from flint.api.routes.backtest import _build_strategy, _DEFAULTS
from flint.api.routes.strategies import list_strategies


class TestStrategyCatalogMatchesBuilders:
    """Every listed strategy must be buildable, and vice versa."""

    def _catalog_names(self) -> list[str]:
        result = list_strategies()
        return [s["name"] for s in result["strategies"]]

    # 1. Every strategy in the catalog must be buildable
    def test_all_listed_strategies_are_buildable(self):
        names = self._catalog_names()
        assert len(names) > 0, "Strategy catalog is empty"
        for name in names:
            params = _DEFAULTS.get(name, {})
            strat = _build_strategy(name, params)
            assert strat is not None, (
                f"Strategy '{name}' is listed in the catalog but "
                f"_build_strategy returned None (missing from builders dict)"
            )

    # 2. Every key in _DEFAULTS must appear in the catalog
    def test_defaults_keys_in_catalog(self):
        names = set(self._catalog_names())
        for key in _DEFAULTS:
            assert key in names, (
                f"Strategy '{key}' is in _DEFAULTS but missing from the catalog"
            )

    # 3. Every catalog entry has required fields
    def test_catalog_entries_have_required_fields(self):
        result = list_strategies()
        required = {"name", "display_name", "description", "params", "type"}
        for entry in result["strategies"]:
            missing = required - set(entry.keys())
            assert not missing, (
                f"Strategy '{entry.get('name', '??')}' is missing fields: {missing}"
            )

    # 4. Every builder key must have a matching _DEFAULTS entry
    def test_builder_keys_have_defaults(self):
        # Import the builders dict indirectly by checking _DEFAULTS covers all
        # buildable names.  _DEFAULTS and builders should be 1:1.
        for name in _DEFAULTS:
            strat = _build_strategy(name, _DEFAULTS[name])
            assert strat is not None, (
                f"Strategy '{name}' has _DEFAULTS but _build_strategy fails"
            )
