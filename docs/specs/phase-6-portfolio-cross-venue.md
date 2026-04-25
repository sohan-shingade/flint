# Phase 6 — Portfolio & Cross-Venue Live

**Owner:** TBD
**Duration:** 3+ months
**Hard gate:** §§3.1-3.5 green

Firms don't run one strategy — they run a book. And they run it on multiple venues simultaneously. Phase 6 is the end-state feature set that makes Flint a credible multi-strategy, multi-venue platform.

This is last for a reason: everything before is structurally required. Don't start until Phase 3 exit criteria are met.

---

## Items

- [6.1 Multi-strategy backtest](#61-multi-strategy-backtest)
- [6.2 Book-level risk limits](#62-book-level-risk-limits)
- [6.3 Correlation-aware optimization](#63-correlation-aware-optimization)
- [6.4 Portfolio replay](#64-portfolio-replay)
- [6.5 /api/v1/live/start — UI-driven live deployment](#65-apiv1livestart)
- [6.6 Funding dislocation arb reference implementation](#66-funding-dislocation-arb-reference)
- [6.7 Jito bundle integration](#67-jito-bundle-integration)

---

## 6.1 Multi-strategy backtest

**Goal:** N strategies × M markets on one capital pool. Shared equity, correlation-aware drawdown, per-strategy attribution.

### Architecture

```
PortfolioBacktestEngine
├── CapitalAllocator   (from flint/execution/capital.py, hardened)
├── StrategyRunner[]   (one per strategy)
└── PortfolioMarginEngine (from Phase 3.5)
```

Each `StrategyRunner` holds its own `ExecutionContext` (Phase 2.1) scoped to its allocated capital. They share a common `FlintStore` read view, a common clock, and a common `PortfolioMarginEngine`.

### Tasks

**T6.1.a — `PortfolioBacktestEngine`**
- New: `flint/portfolio/engine.py:PortfolioBacktestEngine`.
- Accepts `PortfolioConfig` (extends `BacktestConfig` from Phase 2.3):
  ```python
  @dataclass
  class PortfolioConfig:
      strategies: list[StrategyAllocation]  # (strategy, markets, capital_pct)
      total_capital: float
      margin: MarginConfig
      allocator: AllocatorConfig
      seed: int | None = None
  ```
- Per-bar loop: iterate strategies, collect orders, route through `PortfolioMarginEngine` for margin check, execute.

**T6.1.b — Per-strategy P&L attribution**
- `PortfolioBacktestResult.per_strategy: dict[str, StrategyResult]`.
- Each `StrategyResult` includes equity curve, trades, Sharpe, drawdown — same shape as single-strategy result.

**T6.1.c — CapitalAllocator fixes**
- Fix the sync bug noted in audit (`BacktestContext._cash` set once at init, never updated on fill).
- `CapitalAllocator.deduct(strategy_id, amount)` and `.credit(strategy_id, amount)` called on every fill.

**T6.1.d — UI**
- New: `ui/src/pages/PortfolioLab.tsx` — multi-strategy backtest runner.
- Shows: aggregate equity, per-strategy equity, capital utilization, correlation matrix.

### Acceptance

- Backtest 3 strategies × 2 markets × $100k total capital.
- `sum(per_strategy_pnl) == total_pnl` within 1e-6.
- Correlation matrix computed from per-strategy returns.

### Effort

~3-4 weeks.

---

## 6.2 Book-level risk limits

**Goal:** gross / net exposure caps, per-venue concentration limits, VaR / ES on combined book, kill switches that actually halt trading.

### Tasks

**T6.2.a — `PortfolioRiskEngine`**
- New: `flint/risk/portfolio.py`.
- Config:
  ```python
  @dataclass
  class RiskLimits:
      max_gross_exposure: float  # |long| + |short|, as multiple of equity
      max_net_exposure: float    # |long - short|, same units
      max_per_venue_pct: float   # no more than X% equity on one venue
      max_per_market_pct: float  # no more than X% equity on one market
      max_correlation_cluster_pct: float  # 0.8+ correlated markets count as one
      var_limit_95_1d: float | None
      var_limit_99_1d: float | None
  ```

**T6.2.b — Pre-trade check**
- Every order passes through `PortfolioRiskEngine.check(order, current_book) -> (approved, reason)`.
- Rejected orders logged with reason; strategy gets a cancellation event.

**T6.2.c — Kill switches**
- Hard cap: if drawdown > X%, all strategies flatten.
- Today: `EquityMonitor` exists but only for single-session. Extend to portfolio.

**T6.2.d — VaR/ES computation**
- Historical-simulation VaR over trailing N bars.
- Optional ES (expected shortfall) for tail risk.
- Computed per bar close, surfaced in portfolio result.

### Acceptance

- Strategy trying to place order that breaches gross-exposure limit gets rejected with clear reason.
- Kill switch at 20% drawdown flattens all positions within one bar.
- VaR values in portfolio result match hand-computed values on a fixture.

### Effort

~2 weeks.

---

## 6.3 Correlation-aware optimization

**Goal:** Optuna objective that supports portfolio Sharpe, not just single-strategy Sharpe. Penalize correlated winners.

### Tasks

**T6.3.a — `PortfolioObjective`**
- `flint/optimization/portfolio_objective.py`:
  ```python
  def portfolio_objective(trial, portfolio_config, candles, penalty_lambda=0.5) -> float:
      result = PortfolioBacktestEngine.run(...)
      portfolio_sharpe = result.sharpe
      avg_pairwise_correlation = result.correlation_matrix.mean()
      return portfolio_sharpe - penalty_lambda * max(0, avg_pairwise_correlation - 0.3)
  ```

**T6.3.b — Walk-forward portfolio optimization**
- Extend `flint/optimization/walk_forward.py` to accept portfolio configs.
- Per-fold: optimize portfolio Sharpe on in-sample, evaluate on out-of-sample.

**T6.3.c — UI**
- PortfolioLab: "Optimize" button with correlation-penalty slider.

### Acceptance

- Optimization with correlation penalty produces portfolios with lower avg pairwise correlation than without, at comparable Sharpe.
- Walk-forward portfolio optimization completes end-to-end on 3-strategy × 6-month fixture.

### Effort

~1-2 weeks.

---

## 6.4 Portfolio replay

**Goal:** from any timestamp, reconstruct the full book state (positions, margin, funding accrued, pending orders). Required for audit.

### Tasks

**T6.4.a — Event sourcing**
- Every state-changing event (order placement, fill, cancellation, funding payment, liquidation) gets a monotonic sequence number and stored in DuckDB.
- `PortfolioSnapshot` table: stores full state every N bars (compaction checkpoint).

**T6.4.b — `replay(session_id, target_ts) -> BookState`**
- Finds latest snapshot before `target_ts`.
- Replays events forward.
- Returns `BookState` (positions, margin, funding, pending orders).

**T6.4.c — UI: time-travel inspector**
- PortfolioLab and Paper pages: slider on equity curve; clicking shows book state at that moment.

### Acceptance

- Replay to arbitrary ts in a 30-day session completes in < 1 second.
- Replayed state matches live state at that ts (byte-identical for positions, within 1e-6 for equity).

### Effort

~2-3 weeks.

---

## 6.5 /api/v1/live/start

**Goal:** UI can deploy and stop live sessions, not just monitor them. Safe by design.

### Tasks

**T6.5.a — Route**
- `POST /api/v1/live/start`:
  ```python
  class LiveStartRequest(BaseModel):
      strategy: str
      markets: list[str]
      venues: list[str]
      capital: float
      network: Literal["devnet", "testnet", "mainnet"]
      dry_run: bool = True
      risk_config: RiskLimits
      confirmation_token: str  # must match a token generated by GET /api/v1/live/preview
  ```

**T6.5.b — Two-step confirmation**
- `POST /api/v1/live/preview` — returns dry-run summary + `confirmation_token` (expires in 60s).
- `POST /api/v1/live/start` requires token.
- Prevents CSRF / accidental clicks from starting real-money trades.

**T6.5.c — Mainnet gate**
- Mainnet requires a second env var `FLINT_MAINNET_ENABLED=1` or a CLI prompt when using `--mainnet`.
- UI shows red banner on mainnet sessions.

**T6.5.d — Kill switch endpoint**
- `POST /api/v1/live/{session_id}/kill` — immediate flatten all positions, cancel all orders.

**T6.5.e — UI**
- LiveMonitor gets a "Deploy Live" button. Multi-step modal: strategy → markets → venues → capital → risk limits → preview → confirm.

### Acceptance

- Happy path: deploy strategy on devnet via UI; orders visible in LiveMonitor within seconds.
- Mainnet path: requires two distinct confirmations; auto-rejects without them.
- Kill switch flattens within 5 seconds.
- CSRF attack attempt (POST /live/start with forged referrer) blocked by token requirement.

### Effort

~3 weeks.

---

## 6.6 Funding dislocation arb reference

**Goal:** reference implementation of the flagship wedge strategy, running on paper first, then with real capital behind a kill switch.

### Tasks

**T6.6.a — `funding_dislocation_arb` strategy**
- New: `flint/strategy/funding_dislocation_arb.py`.
- Reads `ctx.get_funding_by_venue()` across Drift + HL.
- Opens offsetting legs when spread > threshold.
- Closes when spread closes or reverses.

**T6.6.b — Proof notebook**
- `notebooks/funding_dislocation_arb.ipynb` — backtest + paper + reconciliation (after Phase 1.4).
- Shows: funding spread over time, per-leg P&L, total arb P&L, parity vs live.

**T6.6.c — Paper → mainnet checklist**
- Documented in `docs/how-to/deploy-funding-arb.md`:
  1. Run 30-day backtest on historical data.
  2. Run 7-day paper session on live data.
  3. Verify reconciliation report < 5bps.
  4. Deploy to mainnet with 0.1x intended capital.
  5. Monitor for 48h; verify risk guards engage on synthetic dislocations.
  6. Ramp capital.

### Acceptance

- Paper session shows positive expected value net of fees + funding costs.
- Reconciliation report passes thresholds.
- Checklist is reproducible.

### Effort

~2-3 weeks.

---

## 6.7 Jito bundle integration

**Goal:** real Jito bundle submission for MEV-sensitive Solana fills, not just a lamport fee model.

### State today

- `flint/execution/tx_costs.py:SolanaTxCostModel` — estimates Jito tips as flat lamport cost.
- No actual Jito block engine integration.

### Tasks

**T6.7.a — Jito client**
- New: `flint/execution/jito_client.py` — submits bundles to Jito Block Engine RPC.
- Handles bundle construction, tip placement, confirmation polling.

**T6.7.b — Integration with DriftLiveContext**
- Optional path: if `venue_config.drift.jito_enabled = True`, orders go through Jito bundle path.
- Falls back to standard RPC if bundle rejected.

**T6.7.c — Benchmark**
- Measure: fill latency, inclusion rate, priority fee savings vs standard RPC.
- Document in `docs/reference/jito-integration.md`.

### Acceptance

- Live order on Drift mainnet via Jito bundle confirms within expected slot range.
- Metrics surfaced: bundle landed, tip cost, fill price.
- Fallback to standard RPC tested.

### Effort

~2 weeks.

---

## Dependencies

```
6.1 (multi-strategy)     ──► 6.2, 6.3, 6.4
6.2 (risk)               ──► 6.5 (pre-trade check uses same engine)
6.3 (corr opt)           ── leaf
6.4 (replay)             ── leaf
6.5 (live deploy)        ──► 6.6 (funding arb runs through it)
6.6 (funding arb ref)    ── leaf (but needs 6.5 for live path)
6.7 (Jito)               ── independent
```

Start 6.1 + 6.7 on Day 1. 6.2 follows 6.1. 6.5 can start any time after Phase 3.5.

---

## Exit criteria (Phase 6 complete, Flint 2.0)

1. Multi-strategy × multi-market portfolio backtest with per-strategy attribution.
2. Book-level risk limits enforced pre-trade.
3. Portfolio optimization with correlation penalty.
4. Portfolio replay to any timestamp < 1s.
5. UI can deploy live sessions with two-step confirmation.
6. Funding dislocation arb reference runs end-to-end: backtest → paper → mainnet (small size) with positive P&L.
7. Jito bundle integration for Drift live.

At this point Flint has cleared the bar from "local perp-lab" to "credible multi-strategy, multi-venue trading platform" — without losing the local-first, single-user, no-telemetry discipline.
