"""Tests for FundingDislocationArbStrategy — Phase 6 T6.6."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


from flint.models import Side, Signal
from flint.strategy.funding_dislocation_arb import FundingDislocationArbStrategy


@dataclass
class _FakeRate:
    ts: int
    rate: float  # decimal


@dataclass
class _FakeCandle:
    ts: int
    open: float = 150.0
    high: float = 151.0
    low: float = 149.0
    close: float = 150.0
    volume: float = 1000.0
    market: str = "SOL-PERP"
    resolution_s: int = 3600
    venue: str = "default"


@dataclass
class _FakeAccount:
    cash: float = 10_000.0
    equity: float = 10_000.0


class _FakeContext:
    """Minimal ExecutionContext shim for strategy unit tests."""

    def __init__(self, rates_by_venue: Dict[str, List[_FakeRate]]):
        self._rates = rates_by_venue
        self.account = _FakeAccount()
        self.positions: list = []
        self.orders: list = []
        self.closed: list = []

    def get_funding_by_venue(self, market: str, lookback: int = 24):
        return {v: list(rs)[-lookback:] for v, rs in self._rates.items()}

    def market_order(self, market, side, size, venue="default"):
        self.orders.append({"market": market, "side": side,
                             "size": size, "venue": venue})
        # Simulate position opening so subsequent bars see it.
        self.positions.append(
            type("P", (), dict(market=market, side=side, size=size,
                                venue=venue))
        )
        return f"o{len(self.orders)}"

    def close_position(self, market, venue="default"):
        self.closed.append((market, venue))
        self.positions = [
            p for p in self.positions
            if not (p.market == market and p.venue == venue)
        ]
        return "c1"

    def cancel_all(self, market=None):
        return 0


class TestNoActionWithoutDualVenues:
    def test_hold_with_only_one_venue(self):
        rates = {"drift": [_FakeRate(1000, 0.0005)]}
        ctx = _FakeContext(rates)
        s = FundingDislocationArbStrategy()
        signal = s.on_candle(_FakeCandle(ts=1000), [], ctx)
        assert signal == Signal.HOLD
        assert not ctx.orders


class TestSpreadTooSmall:
    def test_hold_when_spread_under_threshold(self):
        rates = {
            "drift": [_FakeRate(1000, 0.00015)],        # 1.5 bps
            "hyperliquid": [_FakeRate(1000, 0.00010)],  # 1.0 bps
        }
        ctx = _FakeContext(rates)
        s = FundingDislocationArbStrategy(min_spread_bps=10.0)
        assert s.on_candle(_FakeCandle(ts=1000), [], ctx) == Signal.HOLD
        assert not ctx.orders


class TestEntrySignal:
    def test_opens_offsetting_legs_on_dislocation(self):
        """drift = 1bp, HL = 15bp → spread 14bp ≥ threshold.
        Need ≥ zscore_window / 2 bars of history plus z-score ≥ threshold;
        feed in a history of small spreads first to establish baseline, then
        a blowout. Use small window so setup stays tight."""
        s = FundingDislocationArbStrategy(
            min_spread_bps=5.0, entry_z_threshold=1.0,
            zscore_window=12,
        )

        # Bars 0..5: small spreads so baseline is low.
        rates = {"drift": [], "hyperliquid": []}
        for i in range(6):
            rates["drift"].append(_FakeRate(i * 3600, 0.00005))   # 0.5 bps
            rates["hyperliquid"].append(_FakeRate(i * 3600, 0.00007))  # 0.7 bps

        ctx = _FakeContext(rates)
        for i in range(6):
            s.on_candle(_FakeCandle(ts=i * 3600), [], ctx)

        # Bar 6: blowout — drift 0.1bps, HL 12bps → spread 11.9bps ≥ 5
        rates["drift"].append(_FakeRate(6 * 3600, 0.00001))        # 0.1 bps
        rates["hyperliquid"].append(_FakeRate(6 * 3600, 0.0012))   # 12 bps
        s.on_candle(_FakeCandle(ts=6 * 3600), [], ctx)

        # Two legs placed — long on cheap-funding side, short on expensive
        assert len(ctx.orders) == 2
        sides = {o["side"] for o in ctx.orders}
        venues = {o["venue"] for o in ctx.orders}
        assert Side.LONG in sides and Side.SHORT in sides
        assert "drift" in venues and "hyperliquid" in venues
        # LONG the lower-funding venue (drift), SHORT the higher (hyperliquid)
        long_order = next(o for o in ctx.orders if o["side"] == Side.LONG)
        short_order = next(o for o in ctx.orders if o["side"] == Side.SHORT)
        assert long_order["venue"] == "drift"
        assert short_order["venue"] == "hyperliquid"


class TestExit:
    def test_closes_on_spread_reversion(self):
        s = FundingDislocationArbStrategy(
            min_spread_bps=5.0, entry_z_threshold=0.5,
            exit_spread_bps=1.0, zscore_window=6,
        )
        rates = {"drift": [], "hyperliquid": []}
        # Establish baseline
        for i in range(4):
            rates["drift"].append(_FakeRate(i * 3600, 0.00005))
            rates["hyperliquid"].append(_FakeRate(i * 3600, 0.00006))
        ctx = _FakeContext(rates)
        for i in range(4):
            s.on_candle(_FakeCandle(ts=i * 3600), [], ctx)

        # Blow out to trigger entry — spread ≥ 5 bps
        rates["drift"].append(_FakeRate(4 * 3600, 0.00002))    # 0.2 bps
        rates["hyperliquid"].append(_FakeRate(4 * 3600, 0.0010))  # 10 bps → spread ~9.8
        s.on_candle(_FakeCandle(ts=4 * 3600), [], ctx)
        assert ctx.positions

        # Compress spread back below exit threshold (1 bp)
        rates["drift"].append(_FakeRate(5 * 3600, 0.00005))   # 0.5 bps
        rates["hyperliquid"].append(_FakeRate(5 * 3600, 0.00006))  # 0.6 bps → spread 0.1
        s.on_candle(_FakeCandle(ts=5 * 3600), [], ctx)
        assert not ctx.positions
        assert len(ctx.closed) == 2


class TestParametersSurface:
    def test_parameters_are_optuna_ready(self):
        params = FundingDislocationArbStrategy.parameters()
        for key in ("min_spread_bps", "entry_z_threshold",
                    "exit_spread_bps", "zscore_window", "max_position_usd"):
            assert key in params
            assert "low" in params[key]
            assert "high" in params[key]
            assert "default" in params[key]
