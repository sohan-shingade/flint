"""Tests for BacktestConfig — Phase 2 T2.3."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from flint.backtest.config import (
    AllocatorConfig,
    BacktestConfig,
    FillConfig,
    MarginConfig,
    VenueConfig,
)


class TestDefaults:
    def test_builds_with_no_args(self):
        cfg = BacktestConfig()
        assert cfg.market == "SOL-PERP"
        assert cfg.initial_capital == 10_000.0
        assert cfg.seed is None
        assert isinstance(cfg.fill, FillConfig)
        assert isinstance(cfg.margin, MarginConfig)
        assert cfg.allocator is None


class TestRoundTrip:
    def test_dict_roundtrip_equals_original(self):
        cfg = BacktestConfig(
            market="BTC-PERP",
            start_ts=1_700_000_000,
            end_ts=1_700_086_400,
            initial_capital=50_000.0,
            fee_rate=0.001,
            seed=99,
            fill=FillConfig(slippage_bps=7.5, impact_coefficient=0.01),
            margin=MarginConfig(enabled=True, max_leverage=20.0),
        )
        as_dict = cfg.to_json()
        back = BacktestConfig.from_dict(as_dict)
        assert back == cfg

    def test_yaml_roundtrip(self, tmp_path):
        cfg = BacktestConfig(
            market="ETH-PERP", seed=42,
            venues={"drift": VenueConfig(taker_fee_bps=10.0)},
        )
        p = tmp_path / "cfg.yaml"
        import yaml
        p.write_text(yaml.safe_dump(cfg.to_json()), encoding="utf-8")
        back = BacktestConfig.from_yaml(p)
        assert back.market == "ETH-PERP"
        assert back.seed == 42
        assert back.venues["drift"].taker_fee_bps == 10.0


class TestChecksum:
    def test_checksum_stable_across_runs(self):
        cfg = BacktestConfig(market="SOL-PERP", seed=1)
        assert cfg.checksum() == cfg.checksum()

    def test_checksum_differs_on_meaningful_change(self):
        a = BacktestConfig(market="SOL-PERP", seed=1)
        b = BacktestConfig(market="SOL-PERP", seed=2)
        assert a.checksum() != b.checksum()

    def test_checksum_is_64_hex(self):
        h = BacktestConfig().checksum()
        assert len(h) == 64
        int(h, 16)  # must be valid hex


class TestForwardCompatibility:
    def test_unknown_keys_ignored(self):
        """from_dict must not explode on fields added in a later version."""
        cfg = BacktestConfig.from_dict({
            "market": "SOL-PERP",
            "seed": 1,
            "some_future_field": "ignored",
        })
        assert cfg.market == "SOL-PERP"


class TestLegacyKwargs:
    def test_legacy_kwargs_build_config(self):
        cfg = BacktestConfig.from_legacy_kwargs(
            market="SOL-PERP",
            initial_capital=20_000.0,
            fee_rate=0.0008,
            seed=7,
        )
        assert cfg.initial_capital == 20_000.0
        assert cfg.fee_rate == 0.0008
        assert cfg.seed == 7


class TestBacktestEngineFromConfig:
    def test_from_config_runs_backtest(self):
        from flint.backtest.engine import BacktestEngine
        from flint.execution.fill_models import ClosePriceFill
        from flint.models import Candle
        from flint.strategy.ma_crossover import MACrossoverStrategy

        cfg = BacktestConfig(
            market="SOL-PERP", initial_capital=10_000.0,
            fee_rate=0.0005, seed=42,
        )
        candles = [
            Candle(ts=i * 3600, open=100, high=101, low=99,
                   close=100.0 + (i % 5), volume=1000,
                   market="SOL-PERP", resolution_s=3600)
            for i in range(30)
        ]
        engine = BacktestEngine.from_config(
            cfg, MACrossoverStrategy(3, 5),
            fill_model=ClosePriceFill(),
        )
        result = engine.run(candles)
        # Seed flowed through — two runs with same config are identical.
        engine2 = BacktestEngine.from_config(
            cfg, MACrossoverStrategy(3, 5),
            fill_model=ClosePriceFill(),
        )
        result2 = engine2.run(candles)
        assert result.total_pnl == result2.total_pnl
        assert result.equity_curve == result2.equity_curve

    def test_from_config_rejects_non_config(self):
        from flint.backtest.engine import BacktestEngine
        from flint.strategy.ma_crossover import MACrossoverStrategy

        with pytest.raises(TypeError):
            BacktestEngine.from_config(
                {"not": "a config"},
                MACrossoverStrategy(3, 5),
            )
