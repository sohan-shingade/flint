"""Tests for the DataProvider base class and ProviderRegistry."""
from __future__ import annotations

from typing import List


from flint.providers.registry import DataProvider, ProviderRegistry


# ---------------------------------------------------------------------------
# Mock provider for testing
# ---------------------------------------------------------------------------

class MockProvider(DataProvider):
    name = "mock"
    requires_api_key = False

    def __init__(self, available: bool = True, data_types: List[str] | None = None):
        self._available = available
        self._data_types = data_types or ["candles"]
        self.closed = False

    def is_available(self) -> bool:
        return self._available

    def supported_data_types(self) -> List[str]:
        return self._data_types

    def close(self) -> None:
        self.closed = True


class MockProvider2(DataProvider):
    name = "mock2"
    requires_api_key = True

    def is_available(self) -> bool:
        return False

    def supported_data_types(self) -> List[str]:
        return ["candles", "funding"]

    def close(self) -> None:
        pass


class BrokenProvider(DataProvider):
    """Provider that raises on is_available — for testing error handling."""
    name = "broken"
    requires_api_key = False

    def is_available(self) -> bool:
        raise RuntimeError("boom")

    def supported_data_types(self) -> List[str]:
        return ["candles"]

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------

def test_register_and_get_provider():
    reg = ProviderRegistry()
    provider = MockProvider()
    reg.register(provider)
    assert reg.get("mock") is provider
    assert "mock" in reg.list_providers()


def test_get_unknown_provider_returns_none():
    reg = ProviderRegistry()
    assert reg.get("nonexistent") is None


def test_register_overwrites():
    reg = ProviderRegistry()
    p1 = MockProvider()
    p2 = MockProvider()
    reg.register(p1)
    reg.register(p2)
    assert reg.get("mock") is p2


# ---------------------------------------------------------------------------
# Enable / disable tests
# ---------------------------------------------------------------------------

def test_enable_and_disable():
    reg = ProviderRegistry()
    p = MockProvider()
    reg.register(p)
    reg.enable("mock")
    assert reg.is_enabled("mock")
    assert "mock" in reg.list_enabled()

    reg.disable("mock")
    assert not reg.is_enabled("mock")
    assert "mock" not in reg.list_enabled()


def test_disable_nonexistent_is_safe():
    reg = ProviderRegistry()
    reg.disable("nope")  # should not raise


def test_list_enabled_only_returns_registered():
    reg = ProviderRegistry()
    p = MockProvider()
    reg.register(p)
    reg.enable("mock")
    reg.enable("ghost")  # enabled but not registered
    enabled = reg.list_enabled()
    assert "mock" in enabled
    assert "ghost" not in enabled


# ---------------------------------------------------------------------------
# get_providers_for tests
# ---------------------------------------------------------------------------

def test_get_providers_for_data_type():
    reg = ProviderRegistry()
    p = MockProvider()
    reg.register(p)
    reg.enable("mock")
    providers = reg.get_providers_for("candles")
    assert len(providers) == 1
    assert providers[0] is p


def test_get_providers_for_no_match():
    reg = ProviderRegistry()
    p = MockProvider(data_types=["funding"])
    reg.register(p)
    reg.enable("mock")
    assert reg.get_providers_for("candles") == []


def test_get_providers_for_skips_disabled():
    reg = ProviderRegistry()
    p = MockProvider()
    reg.register(p)
    # not enabled
    assert reg.get_providers_for("candles") == []


def test_get_providers_for_multiple():
    reg = ProviderRegistry()
    p1 = MockProvider()
    p2 = MockProvider2()
    reg.register(p1)
    reg.register(p2)
    reg.enable("mock")
    reg.enable("mock2")
    providers = reg.get_providers_for("candles")
    assert len(providers) == 2


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------

def test_load_config_dict_enabled():
    config = {
        "drift": {"enabled": True},
        "birdeye": {"enabled": False},
    }
    reg = ProviderRegistry()
    reg.load_config(config)
    assert reg.is_enabled("drift")
    assert not reg.is_enabled("birdeye")


def test_load_config_bool_shorthand():
    config = {
        "drift": True,
        "birdeye": False,
    }
    reg = ProviderRegistry()
    reg.load_config(config)
    assert reg.is_enabled("drift")
    assert not reg.is_enabled("birdeye")


def test_get_provider_config():
    config = {
        "birdeye": {"enabled": True, "api_key": "test-key"},
    }
    reg = ProviderRegistry()
    reg.load_config(config)
    assert reg.get_provider_config("birdeye") == config["birdeye"]
    assert reg.get_provider_config("nonexistent") == {}


# ---------------------------------------------------------------------------
# Status tests
# ---------------------------------------------------------------------------

def test_provider_status():
    reg = ProviderRegistry()
    p = MockProvider()
    reg.register(p)
    reg.enable("mock")
    status = reg.status()
    assert len(status) == 1
    s = status[0]
    assert s["name"] == "mock"
    assert s["enabled"] is True
    assert s["available"] is True
    assert s["requires_api_key"] is False
    assert s["data_types"] == ["candles"]


def test_status_unavailable_provider():
    reg = ProviderRegistry()
    p = MockProvider(available=False)
    reg.register(p)
    status = reg.status()
    assert status[0]["available"] is False


def test_status_broken_provider():
    reg = ProviderRegistry()
    p = BrokenProvider()
    reg.register(p)
    status = reg.status()
    assert status[0]["available"] is False  # exception caught, defaults to False


def test_status_multiple_providers():
    reg = ProviderRegistry()
    reg.register(MockProvider())
    reg.register(MockProvider2())
    reg.enable("mock")
    status = reg.status()
    assert len(status) == 2
    names = [s["name"] for s in status]
    assert "mock" in names
    assert "mock2" in names


# ---------------------------------------------------------------------------
# Close all
# ---------------------------------------------------------------------------

def test_close_all():
    reg = ProviderRegistry()
    p1 = MockProvider()
    p2 = MockProvider()
    p2.name = "mock_b"
    reg.register(p1)
    reg.register(p2)
    reg.close_all()
    assert p1.closed
    assert p2.closed


def test_close_all_handles_errors():
    """close_all should not raise even if a provider's close() throws."""
    reg = ProviderRegistry()

    class FailClose(DataProvider):
        name = "fail_close"
        requires_api_key = False
        def is_available(self) -> bool: return True
        def supported_data_types(self) -> list: return []
        def close(self) -> None: raise RuntimeError("close failed")

    reg.register(FailClose())
    reg.register(MockProvider())
    reg.close_all()  # should not raise
