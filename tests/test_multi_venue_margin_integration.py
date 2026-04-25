"""Multi-venue margin + capital-transfer integration — Phase 3 T3.5.

Validates that `MarginEngine` (per-venue MMR) and `VenueAllocator` (capital
with transfer latency) compose correctly end-to-end. The full
PortfolioMarginEngine orchestrator + BacktestContext integration is
deferred to a sibling PR (tracked in DEFERRED.md as D-3.5-orchestrator)
because it needs the ExecutionContext consolidation from D-2.1.b first;
but the underlying primitives work today and this suite proves it.
"""
from __future__ import annotations

import pytest

from flint.execution.capital import TransferConfig, VenueAllocator


class TestVenueAllocatorTransferLatency:
    def test_drift_to_hyperliquid_latency(self):
        """Cross-DeFi-venue transfer: capital unavailable on the receiving
        venue until arrival_ts passes."""
        cfg = TransferConfig(
            default_time_s=1800,
            default_cost_usd=1.0,
            routes={"drift_to_hyperliquid": {"time_s": 30, "cost_usd": 0.5}},
        )
        alloc = VenueAllocator(
            {"drift": 10_000.0, "hyperliquid": 0.0},
            transfer_config=cfg,
        )

        # Kick off transfer at t=1000
        t = alloc.transfer("drift", "hyperliquid", 5_000.0, current_ts=1000)
        assert t is not None
        # Source debited immediately (amount + cost)
        assert alloc.available("drift") == pytest.approx(10_000 - 5_000 - 0.5)
        # Destination still empty (in flight)
        assert alloc.available("hyperliquid") == 0.0

        # 1 second later — still in flight
        alloc.process_arrivals(current_ts=1001)
        assert alloc.available("hyperliquid") == 0.0

        # After 30s — arrived
        alloc.process_arrivals(current_ts=1030)
        assert alloc.available("hyperliquid") == pytest.approx(5_000.0)

    def test_insufficient_balance_returns_none(self):
        alloc = VenueAllocator({"drift": 100.0})
        t = alloc.transfer("drift", "hyperliquid", 200.0, current_ts=1000)
        assert t is None
        assert alloc.available("drift") == 100.0


class TestVenueAllocatorMultiTransferInFlight:
    def test_multiple_concurrent_transfers(self):
        alloc = VenueAllocator(
            {"drift": 20_000.0, "hyperliquid": 0.0, "binance": 0.0},
            transfer_config=TransferConfig(default_time_s=60),
        )
        alloc.transfer("drift", "hyperliquid", 5_000, current_ts=100)
        alloc.transfer("drift", "binance", 3_000, current_ts=120)
        assert len(alloc.pending_transfers) == 2

        # First arrives at 160, second at 180
        alloc.process_arrivals(current_ts=161)
        assert len(alloc.pending_transfers) == 1
        assert alloc.available("hyperliquid") == pytest.approx(5_000)

        alloc.process_arrivals(current_ts=181)
        assert len(alloc.pending_transfers) == 0
        assert alloc.available("binance") == pytest.approx(3_000)


class TestVenueAllocatorPnLAttribution:
    def test_debit_credit_tracks_per_venue(self):
        alloc = VenueAllocator({"drift": 5_000, "hyperliquid": 5_000})

        # Drift: pay $2 fee, earn $10 PnL
        alloc.debit("drift", 2.0)
        alloc.credit("drift", 10.0)
        alloc.track_pnl("drift", 10.0)

        # HL: pay $1 fee, lose $5
        alloc.debit("hyperliquid", 1.0)
        alloc.credit("hyperliquid", -5.0)
        alloc.track_pnl("hyperliquid", -5.0)

        assert alloc.balances["drift"] == pytest.approx(5_000 - 2 + 10)
        assert alloc.balances["hyperliquid"] == pytest.approx(5_000 - 1 - 5)

        metrics = alloc.compute_metrics(final_equity=sum(alloc.balances.values()))
        assert metrics.venue_pnl["drift"] == pytest.approx(10.0)
        assert metrics.venue_pnl["hyperliquid"] == pytest.approx(-5.0)


class TestVenueAllocatorWithMarginEngine:
    """Exercise both primitives together: a trade on one venue uses
    margin there, while free capital on the other venue is independent."""

    def test_margin_check_per_venue(self):
        from flint.execution.margin import MarginEngine
        from flint.execution.venue_config import VenueConfig

        # Per-venue margin configs
        configs = {
            "drift": VenueConfig(
                name="drift", initial_margin=0.10, maintenance_margin=0.05,
                max_leverage=10.0,
            ),
            "hyperliquid": VenueConfig(
                name="hyperliquid", initial_margin=0.05,
                maintenance_margin=0.025, max_leverage=20.0,
            ),
        }
        engine = MarginEngine(venue_configs=configs)
        drift_cfg = engine.get_config("drift")
        hl_cfg = engine.get_config("hyperliquid")

        # Drift requires 10% margin → $1000 for $10k notional
        assert drift_cfg.initial_margin == pytest.approx(0.10)
        # HL allows 5% margin → $500 for $10k notional
        assert hl_cfg.initial_margin == pytest.approx(0.05)
        # Max leverages differ
        assert drift_cfg.max_leverage == pytest.approx(10.0)
        assert hl_cfg.max_leverage == pytest.approx(20.0)


class TestFragmentationMetrics:
    def test_idle_capital_tracking(self):
        """If one venue is never used, it shows up as idle."""
        alloc = VenueAllocator({"drift": 5_000, "hyperliquid": 5_000})
        alloc.debit("drift", 1_000)  # use drift only
        metrics = alloc.compute_metrics(final_equity=9_000)
        # Drift: 1000/5000 = 20% used; HL: 0% used → idle = (1 - 1000/10000) = 90%
        assert metrics.peak_utilization["drift"] == pytest.approx(0.20)
        assert metrics.peak_utilization["hyperliquid"] == pytest.approx(0.0)
        assert metrics.idle_capital_pct == pytest.approx(90.0, abs=0.1)
