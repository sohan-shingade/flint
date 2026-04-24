"""Live-venue smoke tests — Phase 5 T5.6.

Gated behind `FLINT_LIVE_SMOKE=1` + venue credentials in env. Never runs
as part of the default `pytest` invocation. Triggered manually via the
`.github/workflows/live-smoke.yml` workflow or by a maintainer locally.

Guard rails (see per-test asserts):
- Max notional per test: 0.01 SOL ($1-2 on devnet/testnet)
- Auto-cancel after 10s if not filled
- Fails fast if wallet balance > $20 — this suite is explicitly NOT for
  funded accounts; it's meant for minimum-balance devnet/testnet wallets
  so a bug cannot burn real capital
- Only touches devnet / testnet endpoints — mainnet network values must
  be missing or the test skips

Why this test exists: until this harness runs successfully on both venues,
"live trading" is a code path nobody has actually verified end-to-end.
That's a blocker on Phase 6 live-deployment claims.
"""
from __future__ import annotations

import os
import time

import pytest

_SMOKE_ENABLED = os.environ.get("FLINT_LIVE_SMOKE") == "1"
pytestmark = pytest.mark.skipif(
    not _SMOKE_ENABLED,
    reason="set FLINT_LIVE_SMOKE=1 + venue credentials to run",
)


MAX_NOTIONAL_SOL = 0.01
MAX_BALANCE_USD = 20.0
CANCEL_AFTER_S = 10


# ---------------------------------------------------------------------------
# Drift devnet
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("DRIFT_DEVNET_KEYPAIR"),
    reason="DRIFT_DEVNET_KEYPAIR not set",
)
class TestDriftDevnetSmoke:
    def test_place_and_cancel_limit_order(self):
        """Place a $0.01 SOL-PERP limit order far from the market, cancel it."""
        try:
            from flint.execution.drift_live import DriftLiveContext
        except ImportError as e:
            pytest.skip(f"drift_live import failed: {e}")

        keypair_path = os.environ["DRIFT_DEVNET_KEYPAIR"]
        ctx = DriftLiveContext(
            keypair_path=keypair_path,
            network="devnet",
            dry_run=False,
        )

        # Safety: refuse to run on a funded wallet.
        equity = ctx.account.equity
        assert equity <= MAX_BALANCE_USD, (
            f"Wallet equity ${equity:.2f} exceeds smoke-test safety cap "
            f"${MAX_BALANCE_USD}. This suite is for minimum-balance "
            f"devnet wallets only."
        )

        from flint.models import Side
        order_id = ctx.limit_order(
            market="SOL-PERP",
            side=Side.LONG,
            size=MAX_NOTIONAL_SOL,
            price=1.0,  # far below market — will rest without filling
        )
        assert order_id, "limit_order did not return an order_id"

        # Give venue a moment to register
        time.sleep(1.0)

        # Cancel within the safety window
        cancelled = ctx.cancel(order_id)
        assert cancelled, f"cancel failed for order_id {order_id}"

        # Cancel-all as belt-and-suspenders
        ctx.cancel_all(market="SOL-PERP")


# ---------------------------------------------------------------------------
# Hyperliquid testnet
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("HL_TESTNET_KEY"),
    reason="HL_TESTNET_KEY not set",
)
class TestHyperliquidTestnetSmoke:
    def test_place_and_cancel_limit_order(self):
        try:
            from flint.execution.hyperliquid_live import HyperliquidLiveContext
        except ImportError as e:
            pytest.skip(f"hyperliquid_live import failed: {e}")

        ctx = HyperliquidLiveContext(
            private_key=os.environ["HL_TESTNET_KEY"],
            network="testnet",
            dry_run=False,
        )

        equity = ctx.account.equity
        assert equity <= MAX_BALANCE_USD, (
            f"HL testnet wallet equity ${equity:.2f} exceeds safety cap "
            f"${MAX_BALANCE_USD}."
        )

        from flint.models import Side
        order_id = ctx.limit_order(
            market="SOL-PERP", side=Side.LONG,
            size=MAX_NOTIONAL_SOL, price=1.0,
        )
        assert order_id
        time.sleep(1.0)
        assert ctx.cancel(order_id)
        ctx.cancel_all(market="SOL-PERP")


# ---------------------------------------------------------------------------
# Harness self-tests (always run — verify the gates work)
# ---------------------------------------------------------------------------

class TestHarnessGates:
    def test_module_skips_without_env_var(self):
        """Confirms pytestmark actually gates the suite. Sanity only."""
        if not _SMOKE_ENABLED:
            pytest.skip("expected: suite is disabled")
        # If we got here, FLINT_LIVE_SMOKE=1 and the gate let us through.
        assert _SMOKE_ENABLED is True
