# Testnet Testing Guide

How to validate Flint's execution pipeline against **Hyperliquid testnet** before going live.

> Drift devnet validation is deprecated — Drift is dropped as a supported venue post-hack. The legacy `examples/devnet_validation.py` script (Solana keypair + `driftpy` + `app.drift.trade`) is kept only for historical reference and is not maintained. Hyperliquid testnet is the live-shaped validation path today.

## 1. Getting a Testnet Wallet

Hyperliquid uses an Ethereum-style key (hex string), not a Solana keypair. Generate a dedicated wallet for testnet, then fund it from the Hyperliquid testnet faucet:

<https://app.hyperliquid-testnet.xyz/>

You need testnet USDC deposited as collateral to run the validation. Use a fresh key reserved for testing — never reuse a mainnet key.

## 2. Setting Up Environment

Export the required environment variables:

```bash
# Required: your hex-encoded Ethereum private key (testnet wallet)
export FLINT_HYPERLIQUID_PRIVATE_KEY=0x<your_testnet_key>

# Point the connector at testnet
export FLINT_LIVE_NETWORK=testnet
```

`LiveHyperliquidContext` defaults to `network="testnet"` and the `https://api.hyperliquid-testnet.xyz` endpoint, so testnet is the safe default. Set `FLINT_LIVE_NETWORK=mainnet` (and configure `live_network: mainnet` in `flint.yaml`) only when you are ready for real funds.

Optional overrides (with their defaults):

| Variable | Default | Description |
|---|---|---|
| `FLINT_MARKET` | `SOL-PERP` | Hyperliquid market to trade |
| `FLINT_NUM_BARS` | `30` | Number of ticks to run |
| `FLINT_CAPITAL` | `100.0` | Starting capital (USDC) |
| `FLINT_TICK_INTERVAL` | `60` | Seconds between ticks |
| `FLINT_MIN_FILLS` | `10` | Minimum fills for calibration |

Install dependencies if you haven't already:

```bash
pip install -e .
```

## 3. Running the Validation

Run your strategy against testnet with `--real` (which submits real testnet orders) and `FLINT_LIVE_NETWORK=testnet`:

```bash
flint live strategies/user/my_strat.py --market SOL-PERP --real
```

The validation flow has the same shape as before:

1. **Initialize** -- Creates a DuckDB store and configures the strategy (e.g. momentum breakout).
2. **Connect** -- `LiveHyperliquidContext` connects to Hyperliquid testnet, checks collateral balance.
3. **Execute** -- Runs the strategy tick loop for `NUM_BARS` ticks, submitting real orders to testnet.
4. **Collect** -- Queries all fill data from the store (prices, sizes, fees, order IDs).
5. **Calibrate** -- Fits a market impact model (`impact_bps = a * sigma * (Q/ADV)^b`) from the fills, if enough data.
6. **Parity test** -- Replays the same candle data through the backtest engine and paper broker, comparing PnL and fill prices against the live results.

Progress is logged to stdout. Estimated runtime: `NUM_BARS * TICK_INTERVAL` seconds (default: ~30 minutes).

## 4. Reading the Report

Key metrics to check:

**Calibration:**

| Metric | Good | Concerning |
|---|---|---|
| `r_squared` | > 0.5 | < 0.3 |
| `mae_bps` | < 5 bps | > 15 bps |
| `cv_r_squared` | > 0.4 | < 0.2 |
| `recommended_impact_coeff` | Within 2x of current | > 5x divergence |

**Parity test:**

| Metric | Good | Concerning |
|---|---|---|
| `pnl_divergence_pct` | < 2% | > 5% |
| `equity_correlation` | > 0.95 | < 0.85 |
| `fill_price_mae` | < $0.10 | > $1.00 |
| `trade_count_match` | `true` | `false` |

## 5. Calibrating from Results

Once you have testnet fills, calibrate the impact coefficient for Hyperliquid:

```bash
flint calibrate hyperliquid --market SOL-PERP --lookback-days 30
```

Re-run backtests with the updated coefficient to confirm improvement. Compare backtest results before and after via `/api/v1/backtest/compare`.

## 6. When to Move to Mainnet

Complete this checklist before deploying with real funds:

- [ ] Parity test passes (< 2% PnL divergence)
- [ ] Kill switch tested (triggered and recovered on testnet)
- [ ] Dry-run on mainnet shows expected behavior (`dry_run=True` on `LiveHyperliquidContext`)
- [ ] Impact coefficients calibrated from testnet fills (not just defaults)
- [ ] At least 50 fills collected for statistical significance
- [ ] Risk guards configured and tested (max drawdown, position limits, daily loss cap)
- [ ] Notification channels working (fills, errors, risk events)
- [ ] Wallet funded with only the intended capital (no excess)

To do a mainnet dry-run (logs orders but does not submit them), set `live.network: mainnet` and `live.dry_run: true` in `flint.yaml`, or pass `dry_run=True` to `LiveHyperliquidContext` in your strategy runner.

Only proceed to live mainnet execution after all checklist items are confirmed.
