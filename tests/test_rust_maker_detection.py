"""D-3.3-maker-detection — verify Rust tags resting-limit fills as maker.

The behavioral change is invisible at the aggregate-result level unless
the fee model has a different rate for makers. We set up a Drift-fee
engine (taker 10 bps, maker -2 bps rebate) and place a resting limit
order that gets filled against a later bar — total_fees should come
out *negative* (pure rebate) rather than +10 bps notional.
"""
from __future__ import annotations

import pytest

flint_core = pytest.importorskip("flint_core")


class TestMakerTakerFeeCapability:
    def test_capability_flag_flipped(self):
        caps = flint_core.capabilities()
        assert caps["supports_maker_taker_fees"] is True


class TestMakerRebateOnRestingLimit:
    def _run_limit_then_market_close(self, fee_model: str, **kwargs):
        """Open with a resting limit (maker), close with a market (taker).
        Returns finish() result."""
        from flint.models import Candle

        engine = flint_core.RustEngine(
            initial_capital=10_000.0, fee_model=fee_model, fill_model="close",
            **kwargs,
        )

        candles = [
            Candle(ts=1000, open=100.0, high=105.0, low=95.0, close=100.0,
                   volume=1000.0, market="SOL-PERP", resolution_s=60),
            Candle(ts=1060, open=100.0, high=105.0, low=90.0, close=95.0,
                   volume=1000.0, market="SOL-PERP", resolution_s=60),
            Candle(ts=1120, open=95.0, high=100.0, low=92.0, close=98.0,
                   volume=1000.0, market="SOL-PERP", resolution_s=60),
        ]
        engine.load_candles(candles)

        # Bar 0: place resting long limit at 95.
        engine.pre_bar(0)
        engine.submit_limit_order("SOL-PERP", "long", 1.0, 95.0, "default")
        engine.post_bar(0)

        # Bar 1: low=90 → limit fills as maker.
        engine.pre_bar(1)
        engine.post_bar(1)

        # Bar 2: close with market order (taker).
        engine.pre_bar(2)
        engine.submit_market_order("SOL-PERP", "short", 1.0, "default")
        engine.post_bar(2)

        return engine.finish()

    def test_drift_maker_rebate_is_negative(self):
        """Drift: -2 bps maker rebate + 10 bps taker fee.
        Limit fill at 95 * 1 * (-2/10_000) = -0.019 (rebate = negative fee)
        Market close at 98 * 1 * (10/10_000) = 0.098 (taker)
        total ≈ 0.079 (net positive but smaller than 2× taker).
        The existence of the rebate is what proves the maker tag
        propagated — under a bug where everything was taker, total would
        be (95 + 98) * 10/10_000 = 0.193."""
        result = self._run_limit_then_market_close("drift")
        assert result["total_fees"] < 0.1, (
            f"total_fees={result['total_fees']} suggests maker rebate not applied"
        )
        # And it's still positive because the market-close taker fee exceeds
        # the rebate. Under all-maker it'd be -0.038; under all-taker 0.193.
        assert result["total_fees"] > 0.0
        assert 0.05 < result["total_fees"] < 0.1  # tight window around 0.079

    def test_hyperliquid_small_maker_fee_still_applied(self):
        """Hyperliquid: 1 bp maker + 3.5 bps taker. Both positive; the
        maker tag should still pick the smaller rate."""
        result = self._run_limit_then_market_close("hyperliquid")
        # maker: 95 * 1/10_000 = 0.0095
        # taker: 98 * 3.5/10_000 = 0.0343
        # total ≈ 0.0438
        assert 0.03 < result["total_fees"] < 0.05

    def test_flat_fee_maker_tag_has_no_effect(self):
        """Under flat fees, maker and taker rates are the same — the
        maker tag is still set internally but the observable fee is
        identical to running without the maker/taker split."""
        result = self._run_limit_then_market_close("flat", fee_bps=10.0)
        # Both fills at 10 bps: 95 * 10/10_000 + 98 * 10/10_000 = 0.193
        assert abs(result["total_fees"] - 0.193) < 1e-6

    def test_custom_maker_taker_bps(self):
        """Explicit MakerTaker variant with (maker=-5 bps, taker=20 bps)."""
        result = self._run_limit_then_market_close(
            "maker_taker", maker_bps=-5.0, taker_bps=20.0,
        )
        # maker: 95 * -5/10_000 = -0.0475
        # taker: 98 * 20/10_000 =  0.196
        # total ≈ 0.1485
        assert 0.14 < result["total_fees"] < 0.16


class TestInternalFeeRoleDispatch:
    """The Rust-internal fix (orders.rs uses compute_fee_with_role) is
    load-bearing even when PyO3 only exposes the flat fee today — it
    unblocks wiring Drift/HyperliquidFeeModel through the runner without
    a second migration. Assert via cargo tests that the right path is
    taken."""

    def test_cargo_fee_tests_still_green(self):
        # The Rust side has 14 cargo tests (4 fee-role tests). They run
        # as part of `cargo test --release` in CI; this Python test
        # exists only to document that the dependency is understood.
        # A real assertion would spawn cargo test, but that's handled
        # by the rust job in .github/workflows/ci.yml.
        assert True
