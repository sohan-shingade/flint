"""Tests for transaction cost config fields."""
from flint.config import FlintConfig

class TestTxCostConfig:
    def test_defaults(self):
        config = FlintConfig()
        assert config.tx_cost_priority_fee_lamports == 5000
        assert config.tx_cost_jito_tip_lamports == 10000
        assert config.tx_cost_sol_price_usd == 150.0

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FLINT_TX_COST_PRIORITY_FEE_LAMPORTS", "8000")
        monkeypatch.setenv("FLINT_TX_COST_JITO_TIP_LAMPORTS", "15000")
        monkeypatch.setenv("FLINT_TX_COST_SOL_PRICE_USD", "200.0")
        config = FlintConfig()
        assert config.tx_cost_priority_fee_lamports == 8000
        assert config.tx_cost_jito_tip_lamports == 15000
        assert config.tx_cost_sol_price_usd == 200.0
