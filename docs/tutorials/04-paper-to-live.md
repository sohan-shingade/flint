# Tutorial 4 — Paper to Live

End state: your validated strategy runs on paper for 2–4 weeks with risk limits, passes a parity test, and you know the checklist before going live on Hyperliquid testnet → mainnet.

Prereq: Tutorials 2 and 3 finished — you have an optimized strategy with an overfitting ratio ≥ 0.5.

Time: the reading is ~30 min; the paper trade itself is weeks by design.

## Why paper before live

Paper trading catches what backtests cannot:

- **Timing.** Is your signal generated before the next bar, or is it late?
- **Data flow.** Does the strategy depend on data that arrives late, misses, or is rate-limited?
- **Regime.** Today's market may not match the window you backtested on.

Paper uses the same fill pipeline as backtest (intentionally — same modeling), but runs against live WebSocket candles. Divergence means your fill model is wrong, not that paper is "harder".

## Step 1 — Parity test

Before paper, verify backtest ↔ paper engines agree on the same data. If they don't, don't deploy.

**CLI:**

```bash
flint parity momentum --market SOL-PERP --start 2025-01-01 --end 2025-03-01
```

**API:**

```bash
curl -X POST localhost:8000/api/v1/backtest/parity \
  -H 'Content-Type: application/json' \
  -d '{"market":"SOL-PERP","strategy":"momentum","start_ts":...,"end_ts":...,"capital":10000,"fee_rate":0.0005}'
```

Pass: `<2%` PnL divergence. Fail: check [how-to/run-parity-test.md](../how-to/run-parity-test.md).

## Step 2 — Start paper trading

```bash
curl -X POST localhost:8000/api/v1/paper/start \
  -H 'Content-Type: application/json' \
  -d '{
    "strategy": "momentum",
    "code": "<your optimized source>",
    "market": "SOL-PERP",
    "initial_capital": 10000,
    "venue": "hyperliquid",
    "risk_config": {
      "max_drawdown_pct": 0.15,
      "daily_loss_limit": 500,
      "max_position_pct": 0.95,
      "liquidation_enabled": true
    }
  }'
```

Response: `{"session_id": "...", "status": "running"}`. Keep the session_id.

**UI:** any completed backtest → **Deploy to Paper**.

## Step 3 — Monitor

UI: Paper Trading page shows equity, positions, trade log. API:

```bash
curl -s localhost:8000/api/v1/paper/status/$SESSION_ID | jq .
```

MCP: ask your AI tool `get_paper_status($SESSION_ID)`.

## Step 4 — Decide

After **2–4 weeks**, compare paper vs backtest on the same window:

| Paper PnL vs backtest | Verdict |
|---|---|
| Within ±20% | Fill model + data flow are honest. Good to proceed. |
| 20–40% below | Fill model is optimistic. Calibrate and retest. |
| >40% below | Something is wrong. Go back to parity test and data checks. |
| Negative | Either regime changed or overfit slipped through walk-forward. Stop. |

Less than 2 weeks of paper isn't enough to draw conclusions. More than 8 weeks means the strategy is gathering dust — either deploy or archive it.

## Step 5 — Calibrate from paper fills

Paper fills are stored. After 2+ weeks of meaningful trading, run:

```bash
flint calibrate hyperliquid --market SOL-PERP --lookback 14
```

Writes a calibrated `impact_coefficient` back to `flint.yaml`. Re-run paper or a backtest on the calibrated config and compare PnL delta. Recipe: [how-to/calibrate-slippage.md](../how-to/calibrate-slippage.md).

## Step 6 — Testnet first

Hyperliquid has a testnet. This is the first real-money-shaped test.

**Hyperliquid testnet:**

```bash
export FLINT_PRIVATE_KEY=<testnet_wallet_key>
export FLINT_LIVE_NETWORK=testnet

flint live strategies/user/my_strat.py --market SOL-PERP --real
```

Fund the testnet wallet from the Hyperliquid testnet faucet. Run 3–5 days minimum. Verify that fills match expectations.

Set `live_hyperliquid_network: testnet` in `flint.yaml` to point the connector at the testnet endpoint.

Detailed flow: [validation/devnet-testing-guide.md](../validation/devnet-testing-guide.md).

## Step 7 — Dry run on mainnet

Before pulling the trigger on real money:

```yaml
# flint.yaml
live:
  network: mainnet
  dry_run: true
```

Dry-run mode submits nothing. Every intended order is logged with full metadata (size, price, venue, guard checks). Watch the log for one full trading cycle.

## Step 8 — Mainnet with safety on

Once dry-run looks right:

```yaml
live:
  network: mainnet
  dry_run: false
  kill_switch_drawdown_pct: 0.08     # START TIGHT
  max_orders_per_minute: 10
  per_market_position_limits: '{"SOL-PERP": 1000}'   # small!
```

**Start small.** First deployment: $500–$1000 capital, kill switch at 8% drawdown, per-market cap matching that size. Live for 1 week. If clean, double the capital; if not, kill it.

**Risk guards must be on.** Even if backtest/paper/devnet all passed, live has surprises (MEV, RPC flakes, venue downtime). The guards are the seatbelt. Full detail: [concepts/risk-model.md](../concepts/risk-model.md) and [validation/safety-rails.md](../validation/safety-rails.md).

## Step 9 — Monitor like you mean it

Set up at least one notification channel in `flint.yaml`:

```yaml
notifications:
  telegram_bot_token: "..."
  telegram_chat_id: "..."
  # or
  discord_webhook_url: "..."
```

You want to know within seconds when:

- Kill switch fires
- Drawdown warning triggers (7.5% default)
- Order rejected repeatedly
- Venue connection drops

Live UI has these in the Live Monitor page.

## When to pull the plug

Stop the strategy immediately if:

- Paper vs live PnL diverges by >50% after a week.
- Drawdown exceeds your paper drawdown in <1/2 the time.
- Multiple orders rejected in a row (venue / guard mismatch).
- The market regime you deployed into changes hard (e.g. funding flips sign persistently on a funding strategy).

Don't rationalize. Stop, investigate, restart when you understand what happened.

## What's next

- [Tutorial 5 — Cross-venue funding arb](05-cross-venue-funding-arb.md) — advanced multi-venue flow
- [concepts/risk-model.md](../concepts/risk-model.md) — everything the kill switch + guards do
- [concepts/backtests-vs-reality.md](../concepts/backtests-vs-reality.md) — the realism ceiling
