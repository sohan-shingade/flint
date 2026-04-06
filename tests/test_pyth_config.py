"""Tests for price_source and tardis config fields."""


def test_price_source_default():
    from flint.config import load_config
    cfg = load_config()
    assert cfg.price_source == "pyth"


def test_tardis_config_defaults():
    from flint.config import load_config
    cfg = load_config()
    assert cfg.tardis_api_key == ""
    assert cfg.tardis_max_gb_per_request == 1.0


def test_tardis_config_from_env(monkeypatch):
    monkeypatch.setenv("FLINT_TARDIS_API_KEY", "td_test123")
    monkeypatch.setenv("FLINT_TARDIS_MAX_GB_PER_REQUEST", "2.5")
    from flint.config import load_config
    cfg = load_config()
    assert cfg.tardis_api_key == "td_test123"
    assert cfg.tardis_max_gb_per_request == 2.5
