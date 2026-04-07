from flint.execution.venue_config import get_venue_config


def test_jupiter_venue_config_exists():
    cfg = get_venue_config("jupiter")
    assert cfg.name == "jupiter"

def test_jupiter_fee_is_flat_6bps():
    cfg = get_venue_config("jupiter")
    assert cfg.taker_fee_bps == 6.0
    assert cfg.maker_fee_bps == 6.0

def test_jupiter_leverage_100x():
    cfg = get_venue_config("jupiter")
    assert cfg.max_leverage == 100.0
    assert cfg.initial_margin == 0.01

def test_jupiter_high_latency():
    cfg = get_venue_config("jupiter")
    assert cfg.base_latency_s >= 10.0
