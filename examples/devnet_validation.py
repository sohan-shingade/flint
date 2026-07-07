"""
Devnet Validation Script
========================
Collects live fill data from Drift devnet, calibrates impact models,
and generates a validation report comparing backtest vs live behavior.

Prerequisites:
  - Funded Drift devnet wallet (get devnet SOL from faucet)
  - FLINT_PRIVATE_KEY env var set to your Solana private key (base58)
  - FLINT_RPC_URL env var set to a Solana devnet RPC endpoint

Usage:
    python examples/devnet_validation.py

    # With explicit settings:
    FLINT_PRIVATE_KEY=... FLINT_RPC_URL=https://api.devnet.solana.com python examples/devnet_validation.py

    # Override defaults:
    FLINT_MARKET=ETH-PERP FLINT_NUM_BARS=50 python examples/devnet_validation.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MARKET = os.environ.get("FLINT_MARKET", "SOL-PERP")
NUM_BARS = int(os.environ.get("FLINT_NUM_BARS", "30"))
INITIAL_CAPITAL = float(os.environ.get("FLINT_CAPITAL", "100.0"))  # devnet USDC
TICK_INTERVAL_S = int(os.environ.get("FLINT_TICK_INTERVAL", "60"))
CANDLE_RESOLUTION_S = int(os.environ.get("FLINT_CANDLE_RESOLUTION", "3600"))
MIN_FILLS_FOR_CALIBRATION = int(os.environ.get("FLINT_MIN_FILLS", "10"))  # low for devnet

# Safety: must be devnet unless explicitly overridden
ALLOW_MAINNET = os.environ.get("FLINT_ALLOW_MAINNET", "").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("devnet_validation")

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------


def _check_env_vars() -> bool:
    """Return True if all required env vars are present, else print instructions."""
    private_key = os.environ.get("FLINT_PRIVATE_KEY", "")
    rpc_url = os.environ.get("FLINT_RPC_URL", "")

    missing = []
    if not private_key:
        missing.append("FLINT_PRIVATE_KEY")
    if not rpc_url:
        missing.append("FLINT_RPC_URL")

    if missing:
        print("=" * 60)
        print("MISSING ENVIRONMENT VARIABLES")
        print("=" * 60)
        print()
        for var in missing:
            print(f"  - {var}")
        print()
        print("Setup instructions:")
        print()
        print("  1. Generate a Solana keypair (if you don't have one):")
        print("       solana-keygen new --outfile ~/.config/solana/devnet.json")
        print()
        print("  2. Get devnet SOL from a faucet:")
        print("       solana airdrop 2 --url devnet")
        print()
        print("  3. Deposit devnet USDC into your Drift account")
        print("       (use the Drift devnet UI at https://app.drift.trade/?env=devnet)")
        print()
        print("  4. Export your private key as base58:")
        print("       export FLINT_PRIVATE_KEY=<your-base58-private-key>")
        print()
        print("  5. Set the RPC endpoint:")
        print("       export FLINT_RPC_URL=https://api.devnet.solana.com")
        print()
        print("  6. Run this script again:")
        print("       python examples/devnet_validation.py")
        print()
        print("See docs/validation/devnet-testing-guide.md for the full guide.")
        print("=" * 60)
        return False

    return True


def _check_network_safety(rpc_url: str) -> bool:
    """Block mainnet connections unless explicitly opted in."""
    mainnet_indicators = ["mainnet", "mainnet-beta"]
    is_mainnet = any(indicator in rpc_url.lower() for indicator in mainnet_indicators)

    if is_mainnet and not ALLOW_MAINNET:
        print("=" * 60)
        print("MAINNET BLOCKED")
        print("=" * 60)
        print()
        print(f"  RPC URL appears to be mainnet: {rpc_url}")
        print()
        print("  This script is designed for devnet testing. Running on mainnet")
        print("  with real funds is dangerous and should only be done after the")
        print("  devnet validation checklist passes.")
        print()
        print("  If you really want to run on mainnet, set:")
        print("       export FLINT_ALLOW_MAINNET=true")
        print()
        print("=" * 60)
        return False

    return True


def _check_dependencies() -> bool:
    """Check that driftpy and other required packages are installed."""
    missing = []

    try:
        import driftpy  # noqa: F401
    except ImportError:
        missing.append("driftpy")

    try:
        import numpy  # noqa: F401
    except ImportError:
        missing.append("numpy")

    try:
        import duckdb  # noqa: F401
    except ImportError:
        missing.append("duckdb")

    if missing:
        print("=" * 60)
        print("MISSING DEPENDENCIES")
        print("=" * 60)
        print()
        for pkg in missing:
            print(f"  - {pkg}")
        print()
        print("  Install with:")
        print(f"       pip install {' '.join(missing)}")
        print()
        if "driftpy" in missing:
            print("  Note: driftpy requires Python 3.10+ and Solana CLI tools.")
            print("       pip install driftpy")
        print()
        print("=" * 60)
        return False

    return True


# ---------------------------------------------------------------------------
# Core validation pipeline
# ---------------------------------------------------------------------------


async def run_validation() -> dict:
    """Execute the full devnet validation pipeline.

    Steps:
        1. Connect to Drift devnet
        2. Run momentum breakout strategy for NUM_BARS ticks
        3. Collect fill data from the store
        4. Run CalibrationEngine on collected fills
        5. Run ParityTest (backtest vs live)
        6. Build and return the validation report
    """
    from flint.store import FlintStore
    from flint.execution.drift_live import LiveDriftContext
    from flint.strategy.momentum_breakout import MomentumBreakoutStrategy
    from flint.backtest.calibration import CalibrationEngine
    from flint.backtest.parity import ParityTest

    report: dict = {
        "timestamp": int(time.time()),
        "market": MARKET,
        "num_bars": NUM_BARS,
        "initial_capital": INITIAL_CAPITAL,
        "network": "devnet",
        "rpc_url": os.environ.get("FLINT_RPC_URL", ""),
        "steps": {},
    }

    # ---------------------------------------------------------------
    # Step 1: Initialize store and context
    # ---------------------------------------------------------------
    logger.info("Step 1/6: Initializing store and Drift connection...")

    db_path = Path("data") / "devnet_validation.duckdb"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = FlintStore(str(db_path))

    strategy = MomentumBreakoutStrategy(
        breakout_lookback=20,
        trailing_stop_pct=0.02,
        oracle_confirmation=0,  # disable oracle check for devnet simplicity
        candle_resolution_s=CANDLE_RESOLUTION_S,
    )

    session_id = f"devnet-{int(time.time())}"

    ctx = LiveDriftContext(
        network="devnet",
        initial_capital=INITIAL_CAPITAL,
        store=store,
        session_id=session_id,
        max_retries=3,
        on_failure="drop",
    )

    report["steps"]["init"] = {"status": "ok", "session_id": session_id}
    logger.info("  Session ID: %s", session_id)

    # ---------------------------------------------------------------
    # Step 2: Connect to Drift devnet
    # ---------------------------------------------------------------
    logger.info("Step 2/6: Connecting to Drift devnet...")

    try:
        await ctx.connect()
        balance = ctx.account.cash
        report["steps"]["connect"] = {"status": "ok", "balance_usdc": balance}
        logger.info("  Connected. Balance: %.2f USDC", balance)

        if balance < 1.0:
            logger.warning("  Low balance (%.2f USDC). Deposit devnet USDC via Drift UI.", balance)
    except Exception as e:
        report["steps"]["connect"] = {"status": "error", "error": str(e)}
        logger.error("  Connection failed: %s", e)
        _save_report(report)
        return report

    # ---------------------------------------------------------------
    # Step 3: Run strategy for NUM_BARS ticks
    # ---------------------------------------------------------------
    logger.info("Step 3/6: Running momentum breakout for %d bars on %s...", NUM_BARS, MARKET)
    logger.info("  Tick interval: %ds. Estimated runtime: ~%d minutes.",
                TICK_INTERVAL_S, (NUM_BARS * TICK_INTERVAL_S) // 60)

    tick_count = 0

    async def _fetch_candle():
        """Fetch the latest candle from the store or return None."""
        candles = store.query_candles(MARKET, CANDLE_RESOLUTION_S)
        return candles[-1] if candles else None

    # Run the strategy tick loop with a bar limit
    async def _run_limited():
        nonlocal tick_count
        ctx._running = True
        ctx._tick_count = 0

        try:
            while ctx._running and ctx._tick_count < NUM_BARS:
                ctx._tick_count += 1
                tick_count = ctx._tick_count

                if tick_count % 5 == 0 or tick_count == 1:
                    logger.info("  Tick %d/%d", tick_count, NUM_BARS)

                try:
                    await ctx._tick(strategy, MARKET, fetch_candle=_fetch_candle)
                except Exception as e:
                    logger.error("  Tick %d failed: %s", tick_count, e)

                if tick_count < NUM_BARS:
                    await asyncio.sleep(TICK_INTERVAL_S)
        finally:
            ctx._running = False

    try:
        await _run_limited()
        fills_collected = len(ctx._fills)
        report["steps"]["execution"] = {
            "status": "ok",
            "ticks_completed": tick_count,
            "fills_collected": fills_collected,
        }
        logger.info("  Completed %d ticks. Collected %d fills.", tick_count, fills_collected)
    except Exception as e:
        report["steps"]["execution"] = {
            "status": "error",
            "ticks_completed": tick_count,
            "error": str(e),
        }
        logger.error("  Execution failed at tick %d: %s", tick_count, e)

    # ---------------------------------------------------------------
    # Step 4: Collect fill data from the store
    # ---------------------------------------------------------------
    logger.info("Step 4/6: Querying fill data from store...")

    fills = store.query_live_fills_by_venue("drift", MARKET)
    report["steps"]["data_collection"] = {
        "status": "ok",
        "total_fills_in_store": len(fills),
        "session_fills": len(ctx._fills),
    }

    if fills:
        prices = [f["price"] for f in fills]
        sizes = [f["size"] for f in fills]
        report["steps"]["data_collection"]["fill_summary"] = {
            "count": len(fills),
            "avg_price": sum(prices) / len(prices),
            "avg_size": sum(sizes) / len(sizes),
            "min_price": min(prices),
            "max_price": max(prices),
        }
        logger.info("  Found %d total fills for %s on Drift.", len(fills), MARKET)
    else:
        logger.info("  No fills found. Strategy may not have generated signals.")
        logger.info("  This is expected if the market was range-bound during the test.")

    # ---------------------------------------------------------------
    # Step 5: Run CalibrationEngine (if enough fills)
    # ---------------------------------------------------------------
    logger.info("Step 5/6: Running calibration engine...")

    calibration_report = None
    if len(fills) >= MIN_FILLS_FOR_CALIBRATION:
        try:
            engine = CalibrationEngine(store)
            calibration_report = engine.calibrate(
                venue="drift",
                market=MARKET,
                lookback_days=30,
                min_fills=MIN_FILLS_FOR_CALIBRATION,
            )
            report["steps"]["calibration"] = {
                "status": "ok",
                "report": calibration_report.to_dict(),
            }
            logger.info("  Calibration complete:")
            logger.info("    Model: %s", calibration_report.model_type)
            logger.info("    R-squared: %.3f", calibration_report.r_squared)
            logger.info("    MAE: %.1f bps", calibration_report.mae_bps)
            logger.info("    Current impact coeff: %.4f", calibration_report.current_impact_coeff)
            logger.info("    Recommended impact coeff: %.4f", calibration_report.recommended_impact_coeff)
        except ValueError as e:
            report["steps"]["calibration"] = {"status": "skipped", "reason": str(e)}
            logger.info("  Calibration skipped: %s", e)
        except Exception as e:
            report["steps"]["calibration"] = {"status": "error", "error": str(e)}
            logger.error("  Calibration failed: %s", e)
    else:
        report["steps"]["calibration"] = {
            "status": "skipped",
            "reason": f"Not enough fills ({len(fills)} < {MIN_FILLS_FOR_CALIBRATION})",
        }
        logger.info("  Skipping calibration: only %d fills (need %d).",
                     len(fills), MIN_FILLS_FOR_CALIBRATION)

    # ---------------------------------------------------------------
    # Step 6: Run ParityTest (backtest vs live)
    # ---------------------------------------------------------------
    logger.info("Step 6/6: Running parity test (backtest vs live fills)...")

    candles = store.query_candles(MARKET, CANDLE_RESOLUTION_S)
    if candles and len(candles) >= 30:
        try:
            parity = ParityTest(
                strategy=MomentumBreakoutStrategy(
                    breakout_lookback=20,
                    trailing_stop_pct=0.02,
                    oracle_confirmation=0,
                ),
                market=MARKET,
                candles=candles,
                initial_capital=INITIAL_CAPITAL,
                fee_rate=0.0005,
                threshold_pct=2.0,
            )
            parity_report = parity.run()
            report["steps"]["parity"] = {
                "status": "ok",
                "report": parity_report.to_dict(),
            }
            logger.info("  Parity test result: %s", "PASS" if parity_report.passed else "FAIL")
            logger.info("    Backtest PnL: $%.2f (%d trades)", parity_report.backtest_pnl, parity_report.backtest_trades)
            logger.info("    Paper PnL:    $%.2f (%d trades)", parity_report.paper_pnl, parity_report.paper_trades)
            logger.info("    PnL divergence: %.2f%%", parity_report.pnl_divergence_pct)
            logger.info("    Fill price MAE: $%.4f", parity_report.fill_price_mae)
            logger.info("    Equity correlation: %.3f", parity_report.equity_correlation)
        except Exception as e:
            report["steps"]["parity"] = {"status": "error", "error": str(e)}
            logger.error("  Parity test failed: %s", e)
    else:
        report["steps"]["parity"] = {
            "status": "skipped",
            "reason": f"Not enough candle data ({len(candles) if candles else 0} candles, need 30+)",
        }
        logger.info("  Skipping parity test: not enough candle data.")

    # ---------------------------------------------------------------
    # Disconnect
    # ---------------------------------------------------------------
    try:
        await ctx.disconnect()
    except Exception as e:
        logger.warning("Disconnect error (non-fatal): %s", e)

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    report["completed_at"] = int(time.time())
    report["duration_s"] = report["completed_at"] - report["timestamp"]

    _print_summary(report)
    _save_report(report)

    return report


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _print_summary(report: dict) -> None:
    """Print a human-readable summary of the validation report."""
    print()
    print("=" * 60)
    print("DEVNET VALIDATION REPORT")
    print("=" * 60)
    print(f"  Market:           {report['market']}")
    print(f"  Bars:             {report['num_bars']}")
    print(f"  Capital:          ${report['initial_capital']:.2f}")
    print(f"  Duration:         {report.get('duration_s', 0)}s")
    print()

    for step_name, step_data in report.get("steps", {}).items():
        status = step_data.get("status", "unknown")
        marker = "[OK]" if status == "ok" else "[SKIP]" if status == "skipped" else "[ERR]"
        print(f"  {marker} {step_name}")
        if status == "skipped":
            print(f"        Reason: {step_data.get('reason', 'N/A')}")
        elif status == "error":
            print(f"        Error: {step_data.get('error', 'N/A')}")

    # Highlight key results
    calibration = report.get("steps", {}).get("calibration", {})
    if calibration.get("status") == "ok":
        cal = calibration["report"]
        print()
        print("  Calibration Results:")
        print(f"    Model type:          {cal['model_type']}")
        print(f"    R-squared:           {cal['r_squared']:.3f}")
        print(f"    MAE:                 {cal['mae_bps']:.1f} bps")
        print(f"    Recommended coeff:   {cal['recommended_impact_coeff']:.6f}")

    parity = report.get("steps", {}).get("parity", {})
    if parity.get("status") == "ok":
        par = parity["report"]
        verdict = "PASS" if par["passed"] else "FAIL"
        print()
        print(f"  Parity Test: {verdict}")
        print(f"    PnL divergence:      {par['pnl_divergence_pct']:.2f}%")
        print(f"    Equity correlation:  {par['equity_correlation']:.3f}")

    print()
    print("=" * 60)


def _save_report(report: dict) -> None:
    """Save the report to reports/devnet_validation_<timestamp>.json."""
    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    timestamp = report.get("timestamp", int(time.time()))
    filename = f"devnet_validation_{timestamp}.json"
    filepath = reports_dir / filename

    with open(filepath, "w") as f:
        json.dump(report, f, indent=2, default=str)

    logger.info("Report saved to %s", filepath)
    print(f"  Report saved to: {filepath}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    print()
    print("Flint Devnet Validation Pipeline")
    print("-" * 40)
    print(f"  Market:    {MARKET}")
    print(f"  Bars:      {NUM_BARS}")
    print(f"  Capital:   ${INITIAL_CAPITAL:.2f}")
    print(f"  Tick:      {TICK_INTERVAL_S}s")
    print()

    # Check 1: Environment variables
    if not _check_env_vars():
        sys.exit(1)

    # Check 2: Network safety
    rpc_url = os.environ.get("FLINT_RPC_URL", "")
    if not _check_network_safety(rpc_url):
        sys.exit(1)

    # Check 3: Dependencies
    if not _check_dependencies():
        sys.exit(1)

    logger.info("All prerequisites met. Starting validation...")
    print()

    # Run the async validation pipeline
    try:
        report = asyncio.run(run_validation())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(130)
    except Exception as e:
        logger.error("Validation failed with unexpected error: %s", e)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Exit code based on results
    steps = report.get("steps", {})
    has_errors = any(s.get("status") == "error" for s in steps.values())
    if has_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
