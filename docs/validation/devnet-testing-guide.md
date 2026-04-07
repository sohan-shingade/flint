# Devnet Testing Guide

How to validate Flint's execution pipeline against Drift devnet before going live.

## 1. Getting a Devnet Wallet

Generate a new Solana keypair dedicated to devnet testing:

```bash
solana-keygen new --outfile ~/.config/solana/devnet.json
solana config set --url devnet --keypair ~/.config/solana/devnet.json
```

Fund it with devnet SOL:

```bash
solana airdrop 2 --url devnet
```

Deposit devnet USDC into your Drift account via the Drift devnet UI:
<https://app.drift.trade/?env=devnet>

You need at least 100 USDC deposited as collateral to run the validation script.

## 2. Setting Up Environment

Export the required environment variables:

```bash
# Required: your base58-encoded private key
export FLINT_PRIVATE_KEY=$(solana-keygen pubkey ~/.config/solana/devnet.json --outfile /dev/stdout 2>/dev/null)
# For the private key specifically, extract from your keypair JSON:
export FLINT_PRIVATE_KEY=$(python3 -c "
import json, base58
with open('$HOME/.config/solana/devnet.json') as f:
    key_bytes = bytes(json.load(f))
print(base58.b58encode(key_bytes).decode())
")

# Required: RPC endpoint
export FLINT_RPC_URL=https://api.devnet.solana.com
```

Optional overrides (with their defaults):

| Variable | Default | Description |
|---|---|---|
| `FLINT_MARKET` | `SOL-PERP` | Drift market to trade |
| `FLINT_NUM_BARS` | `30` | Number of ticks to run |
| `FLINT_CAPITAL` | `100.0` | Starting capital (USDC) |
| `FLINT_TICK_INTERVAL` | `60` | Seconds between ticks |
| `FLINT_MIN_FILLS` | `10` | Minimum fills for calibration |

Install dependencies if you haven't already:

```bash
pip install -e .
pip install driftpy
```

## 3. Running the Validation

```bash
python examples/devnet_validation.py
```

The script runs six steps:

1. **Initialize** -- Creates a DuckDB store and configures the strategy (momentum breakout).
2. **Connect** -- Connects to Drift devnet via driftpy, checks collateral balance.
3. **Execute** -- Runs the strategy tick loop for `NUM_BARS` ticks, submitting real orders to devnet.
4. **Collect** -- Queries all fill data from the store (prices, sizes, fees, tx signatures).
5. **Calibrate** -- Fits a market impact model (`impact_bps = a * sigma * (Q/ADV)^b`) from the fills, if enough data.
6. **Parity test** -- Replays the same candle data through the backtest engine and paper broker, comparing PnL and fill prices against the live results.

Progress is logged to stdout. The full report is saved to `reports/devnet_validation_<timestamp>.json`.

Estimated runtime: `NUM_BARS * TICK_INTERVAL` seconds (default: ~30 minutes).

## 4. Reading the Report

The JSON report contains a `steps` object. Key metrics to check:

**Calibration** (`steps.calibration.report`):

| Metric | Good | Concerning |
|---|---|---|
| `r_squared` | > 0.5 | < 0.3 |
| `mae_bps` | < 5 bps | > 15 bps |
| `cv_r_squared` | > 0.4 | < 0.2 |
| `recommended_impact_coeff` | Within 2x of current | > 5x divergence |

**Parity test** (`steps.parity.report`):

| Metric | Good | Concerning |
|---|---|---|
| `pnl_divergence_pct` | < 2% | > 5% |
| `equity_correlation` | > 0.95 | < 0.85 |
| `fill_price_mae` | < $0.10 | > $1.00 |
| `trade_count_match` | `true` | `false` |

## 5. Calibrating from Results

If the calibration step produced a `recommended_impact_coeff`, apply it:

```bash
# View the recommendation
python -c "
import json
with open('reports/devnet_validation_<timestamp>.json') as f:
    r = json.load(f)
cal = r['steps']['calibration']['report']
print(f'Current:     {cal[\"current_impact_coeff\"]:.6f}')
print(f'Recommended: {cal[\"recommended_impact_coeff\"]:.6f}')
"
```

Then update your `flint.yaml` or use the CLI:

```bash
flint calibrate --venue drift --market SOL-PERP --lookback-days 30
```

Re-run backtests with the updated coefficient to confirm improvement. Compare backtest results before and after via `/api/v1/backtest/compare`.

## 6. When to Move to Mainnet

Complete this checklist before deploying with real funds:

- [ ] Parity test passes (< 2% PnL divergence)
- [ ] Kill switch tested (triggered and recovered on devnet)
- [ ] Dry-run on mainnet shows expected behavior (`dry_run=True` in LiveDriftContext)
- [ ] Impact coefficients calibrated from devnet fills (not just defaults)
- [ ] At least 50 fills collected for statistical significance
- [ ] Risk guards configured and tested (max drawdown, position limits, daily loss cap)
- [ ] Notification channels working (fills, errors, risk events)
- [ ] Wallet funded with only the intended capital (no excess)

To do a mainnet dry-run (logs orders but does not submit them):

```bash
export FLINT_RPC_URL=https://api.mainnet-beta.solana.com
export FLINT_ALLOW_MAINNET=true
# Then in your strategy runner, pass dry_run=True to LiveDriftContext
```

Only proceed to live mainnet execution after all checklist items are confirmed.
