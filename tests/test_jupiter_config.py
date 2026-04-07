def test_jupiter_perps_config_defaults():
    from flint.config import load_config
    cfg = load_config()
    assert cfg.jupiter_perps_enabled is False
    assert cfg.jupiter_perps_sidecar_port == 8401
    assert cfg.jupiter_perps_rpc_url == ""
    assert cfg.jupiter_perps_wallet_path == ""


def test_jupiter_perps_config_from_env(monkeypatch):
    monkeypatch.setenv("FLINT_JUPITER_PERPS_ENABLED", "true")
    monkeypatch.setenv("FLINT_JUPITER_PERPS_SIDECAR_PORT", "9000")
    monkeypatch.setenv("FLINT_DUNE_API_KEY", "dune_test_key")
    from flint.config import load_config
    cfg = load_config()
    assert cfg.jupiter_perps_enabled is True
    assert cfg.jupiter_perps_sidecar_port == 9000
    assert cfg.dune_api_key == "dune_test_key"
