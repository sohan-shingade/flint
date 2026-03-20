"""Tests for the MEV framework."""
from __future__ import annotations

import pytest

from flint.mev.arb import ArbDetector, _Edge
from flint.mev.liquidation import LiquidationScanner, compute_liquidation_price
from flint.mev.jit import JitDetector
from flint.mev.bundle import BundleBuilder, Bundle, Instruction
from flint.mev.simulator import MevSimulator
from flint.models import (
    ArbRoute, LiquidationOpportunity, OrderbookLevel, OrderbookSnapshot,
    PoolState, Side,
)


# -- Arb detection -------------------------------------------------------------

def _make_pools() -> list[PoolState]:
    """Create a triangle of pools: SOL → USDC → ETH → SOL with arb opportunity."""
    return [
        PoolState("pool_sol_usdc", "raydium", "SOL", "USDC",
                   1000.0, 100_000.0, 0.003),  # 1 SOL = 100 USDC
        PoolState("pool_usdc_eth", "orca", "USDC", "ETH",
                   200_000.0, 100.0, 0.003),    # 1 ETH = 2000 USDC
        PoolState("pool_eth_sol", "raydium", "ETH", "SOL",
                   50.0, 1050.0, 0.003),         # 1 ETH = 21 SOL (mispriced!)
    ]


def test_arb_detector_finds_route():
    detector = ArbDetector(min_profit_bps=1.0)
    detector.update_pools(_make_pools())

    routes = detector.find_arb_routes("SOL", amount=1.0, max_hops=4)
    assert len(routes) > 0

    best = routes[0]
    assert best.profit > 0
    assert best.profit_bps > 0
    assert best.hops >= 2
    assert best.tokens[0] == "SOL"
    assert best.tokens[-1] == "SOL"  # circular


def test_arb_detector_no_arb_when_balanced():
    pools = [
        PoolState("p1", "raydium", "A", "B", 1000.0, 1000.0, 0.003),
        PoolState("p2", "orca", "B", "C", 1000.0, 1000.0, 0.003),
        PoolState("p3", "raydium", "C", "A", 1000.0, 1000.0, 0.003),
    ]
    detector = ArbDetector(min_profit_bps=10.0)
    detector.update_pools(pools)

    routes = detector.find_arb_routes("A", amount=1.0)
    # With 0.3% fees on each hop, 3-hop arb loses ~0.9% → no profit
    assert len(routes) == 0


def test_arb_edge_output_amount():
    edge = _Edge("pool", "A", "B", 1000.0, 2000.0, 0.003)
    out = edge.output_amount(10.0)
    # x*y=k: effective_in = 10*(1-0.003) = 9.97
    # out = 2000 * 9.97 / (1000 + 9.97) ≈ 19.74
    assert out == pytest.approx(19.74, abs=0.1)


def test_arb_estimate_profit():
    detector = ArbDetector(min_profit_bps=1.0)
    detector.update_pools(_make_pools())

    routes = detector.find_arb_routes("SOL", amount=1.0)
    if routes:
        profit = detector.estimate_profit(routes[0], 2.0)
        # Should roughly double the profit
        assert profit > 0


# -- Liquidation scanning ------------------------------------------------------

def test_compute_liquidation_price_long():
    # Long 10 SOL @ $100, $200 collateral, 5% maintenance
    liq = compute_liquidation_price(100.0, 10.0, 200.0, 0.05, is_long=True)
    # liq = 100 - (200 - 0.05*1000) / 10 = 100 - 150/10 = 85
    assert liq == pytest.approx(85.0)


def test_compute_liquidation_price_short():
    # Short 10 @ $100, $200 collateral
    liq = compute_liquidation_price(100.0, -10.0, 200.0, 0.05, is_long=False)
    # liq = 100 + 150/10 = 115
    assert liq == pytest.approx(115.0)


def test_liquidation_scanner_finds_liquidatable():
    scanner = LiquidationScanner(maintenance_margin_ratio=0.05, liquidation_fee=0.01)

    positions = [
        {
            "user_account": "user1",
            "market": "SOL-PERP",
            "size": 10.0,
            "entry_price": 100.0,
            "collateral": 200.0,
            "protocol": "drift",
        }
    ]
    # Price dropped to $80 — below liquidation price of $85
    oracle_prices = {"SOL-PERP": 80.0}

    opps = scanner.scan_positions(positions, oracle_prices)
    assert len(opps) == 1
    assert opps[0].user_account == "user1"
    assert opps[0].market == "SOL-PERP"
    assert opps[0].estimated_profit == pytest.approx(80.0 * 10.0 * 0.01)


def test_liquidation_scanner_no_liquidation():
    scanner = LiquidationScanner()

    positions = [
        {
            "user_account": "user1",
            "market": "SOL-PERP",
            "size": 10.0,
            "entry_price": 100.0,
            "collateral": 200.0,
        }
    ]
    # Price is $95 — above liquidation price of $85
    oracle_prices = {"SOL-PERP": 95.0}

    opps = scanner.scan_positions(positions, oracle_prices)
    assert len(opps) == 0


def test_liquidation_scanner_short():
    scanner = LiquidationScanner(maintenance_margin_ratio=0.05, liquidation_fee=0.01)

    positions = [
        {
            "user_account": "user2",
            "market": "ETH-PERP",
            "size": -5.0,
            "entry_price": 2000.0,
            "collateral": 2000.0,
        }
    ]
    # Short from 2000, liq price = 2000 + (2000 - 500)/5 = 2300
    # Price went to 2400 — liquidated
    oracle_prices = {"ETH-PERP": 2400.0}

    opps = scanner.scan_positions(positions, oracle_prices)
    assert len(opps) == 1
    assert opps[0].side == Side.SHORT


# -- JIT detection --------------------------------------------------------------

def test_jit_detector_finds_opportunity():
    detector = JitDetector(maker_rebate=0.0002, min_spread_bps=5.0, min_size=0.1)

    snapshot = OrderbookSnapshot(
        market="SOL-PERP",
        ts=1700000000,
        bids=(OrderbookLevel(price=90.0, size=10.0),),
        asks=(OrderbookLevel(price=89.0, size=10.0),),
    )
    vamm_price = 89.0  # vAMM bid is 89, DLOB bid is 90 → spread exists

    opps = detector.scan_orderbook(snapshot, vamm_price)
    # Bid at 90 is higher than vAMM at 89 → JIT opportunity on bid side
    bid_opps = [o for o in opps if o.taker_side == Side.SHORT]
    assert len(bid_opps) >= 1
    assert bid_opps[0].estimated_rebate > 0


def test_jit_detector_no_opportunity_when_tight():
    detector = JitDetector(maker_rebate=0.0002, min_spread_bps=5.0, min_size=0.1)

    snapshot = OrderbookSnapshot(
        market="SOL-PERP",
        ts=1700000000,
        bids=(OrderbookLevel(price=89.99, size=10.0),),
        asks=(OrderbookLevel(price=90.01, size=10.0),),
    )
    vamm_price = 90.0  # very tight spread

    opps = detector.scan_orderbook(snapshot, vamm_price)
    assert len(opps) == 0


# -- Bundle builder -------------------------------------------------------------

def test_bundle_builder():
    builder = BundleBuilder(signer="MyWallet", default_tip=1000)
    ix1 = Instruction("program1", ["acc1", "acc2"], b"\x01\x02")
    ix2 = Instruction("program2", ["acc3"], b"\x03")

    builder.add_instruction(ix1).add_instruction(ix2)
    bundle = builder.build()

    assert bundle.num_instructions == 2
    assert bundle.tip_lamports == 1000
    assert bundle.signer == "MyWallet"
    assert bundle.tip_sol == pytest.approx(0.000001)


def test_bundle_validation_empty():
    builder = BundleBuilder()
    bundle = builder.build()
    errors = BundleBuilder.validate(bundle)
    assert "Bundle has no instructions" in errors


def test_bundle_validation_tip_too_high():
    builder = BundleBuilder()
    ix = Instruction("prog", ["acc"], b"\x00")
    builder.add_instruction(ix)
    bundle = builder.build(tip_lamports=999_999)
    errors = BundleBuilder.validate(bundle)
    assert any("exceeds max" in e for e in errors)


def test_bundle_validation_valid():
    builder = BundleBuilder()
    ix = Instruction("prog", ["acc"], b"\x00")
    builder.add_instruction(ix)
    bundle = builder.build(tip_lamports=5000)
    errors = BundleBuilder.validate(bundle)
    assert errors == []


def test_bundle_estimate_tip():
    tip = BundleBuilder.estimate_tip(100_000, tip_fraction=0.5)
    assert tip == 50_000


# -- MEV simulator --------------------------------------------------------------

def test_mev_simulator_runs():
    pools_t1 = _make_pools()
    pool_snapshots = {1000: pools_t1}
    position_snapshots = {
        1000: [
            {
                "user_account": "user1",
                "market": "SOL-PERP",
                "size": 10.0,
                "entry_price": 100.0,
                "collateral": 200.0,
                "protocol": "drift",
            }
        ]
    }
    oracle_prices = {1000: {"SOL-PERP": 80.0}}

    sim = MevSimulator(arb_min_profit_bps=1.0, arb_amount=1.0)
    result = sim.run(pool_snapshots, position_snapshots, oracle_prices, "SOL")

    assert result.periods_scanned == 1
    assert result.total_arb_opportunities > 0
    assert result.total_arb_profit > 0
    assert result.total_liq_opportunities == 1
    assert result.total_liq_profit > 0
    assert result.total_profit > 0


def test_mev_simulator_empty():
    sim = MevSimulator()
    result = sim.run({}, {}, {}, "SOL")
    assert result.periods_scanned == 0
    assert result.total_profit == 0
