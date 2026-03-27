# Live Paper Trading — Design Spec

## Overview

Add a "Paper Trading" tab to the Flint UI for monitoring strategies running against live Drift perpetual market data with simulated execution. Strategies are deployed from BacktestLab after a successful backtest ("Deploy to Paper" button). The paper trading page is monitoring-only — no strategy editing or deployment happens there.

## Goals

1. Let users deploy backtested strategies to a simulated live environment with one click
2. Show real-time portfolio performance with 5-second price updates
3. Automatically stop strategies that breach configurable risk limits
4. Provide clear visual separation between "replay" (historical catch-up) and "live" periods
5. Persist session state across server restarts

## Non-Goals

- Real money trading (existing `LiveDriftContext` covers this separately)
- WebSocket streaming (HTTP polling at 5-10s is sufficient for hourly strategies)
- Strategy editing in the paper trading page (that's BacktestLab's job)
- Cross-strategy shared capital pools (sessions are independent)

---

## Architecture

### System Components

```
BacktestLab UI                Paper Trading UI
     |                              |
     | "Deploy to Paper"            | polling (2s)
     v                              v
POST /paper/deploy           GET /paper/portfolio
     |                       GET /paper/sessions
     v                       GET /paper/status/{id}
  PaperTradingEngine                |
     |                              |
     |-- ReplayForwardRunner -------+
     |     |
     |     |-- Phase 1: Backtest replay (historical candles)
     |     |-- Phase 2: Live polling (new candles every ~10s)
     |     |
     |     +-- RiskGuard (checks limits each candle)
     |
     +-- PriceTicker (5s DLOB mid-price polling for PnL)
     |
     +-- SessionStore (DuckDB persistence)
```

### Data Flow

1. **Deploy**: User runs backtest in BacktestLab, clicks "Deploy to Paper" on results → `POST /paper/deploy` with strategy code, params, market, capital, replay_start_ts, risk limits
2. **Replay**: Engine backtests from `replay_start_ts` to now using stored candles (same `BacktestEngine`). Fills, equity curve, and trades are recorded with a `replay=True` flag.
3. **Live transition**: After replay catches up to current time, switches to polling mode — checks store every 10s for new candles, processes them through the strategy.
4. **Price ticker**: Separate lightweight loop polls Drift DLOB mid-price every 5s, updates unrealized PnL on open positions. This is display-only — doesn't affect strategy logic.
5. **Risk guard**: After each candle is processed, checks drawdown, daily loss, and position size against configured limits. Breaches auto-stop the strategy.
6. **Portfolio view**: UI polls `/paper/portfolio` every 2s to get aggregated metrics across all sessions.

---

## Backend

### New/Modified Files

| File | Action | Purpose |
|------|--------|---------|
| `flint/paper/engine.py` | Modify | Add replay-forward logic, risk guards, price ticker, session persistence |
| `flint/paper/risk_guard.py` | Create | Risk limit checking (max DD, daily loss, position size) |
| `flint/paper/session_store.py` | Create | DuckDB persistence for paper sessions (survives restarts) |
| `flint/paper/price_ticker.py` | Create | 5s DLOB mid-price polling for unrealized PnL |
| `flint/api/routes/paper.py` | Modify | Add deploy, portfolio, and risk config endpoints |
| `flint/api/routes/backtest.py` | Modify | Add deploy-to-paper data in backtest results |
| `flint/store.py` | Modify | Add paper trading tables |

### DuckDB Tables (New)

**`paper_sessions`** — persists session metadata across restarts

| Column | Type | Description |
|--------|------|-------------|
| session_id | VARCHAR PK | UUID |
| strategy_name | VARCHAR | Display name |
| strategy_code | TEXT | Full strategy source |
| strategy_params | VARCHAR | JSON params |
| market | VARCHAR | e.g., "SOL-PERP" |
| initial_capital | DOUBLE | Starting equity |
| replay_start_ts | BIGINT | Where replay began |
| live_start_ts | BIGINT | When live trading started (after replay) |
| started_at | BIGINT | Session creation time |
| stopped_at | BIGINT NULL | When stopped (NULL if running) |
| status | VARCHAR | "replaying", "live", "stopped", "risk_stopped" |
| stop_reason | VARCHAR | Why stopped (risk limit name, manual, etc.) |
| risk_config | VARCHAR | JSON risk limits |

**`paper_equity_history`** — equity snapshots for charting

| Column | Type | Description |
|--------|------|-------------|
| session_id | VARCHAR | FK to paper_sessions |
| ts | BIGINT | Unix timestamp |
| equity | DOUBLE | Account equity at this point |
| cash | DOUBLE | Cash balance |
| unrealized_pnl | DOUBLE | Open position PnL |
| is_replay | BOOLEAN | True if from replay phase |

**`paper_trades`** — closed trade history

| Column | Type | Description |
|--------|------|-------------|
| session_id | VARCHAR | FK to paper_sessions |
| trade_id | VARCHAR | Unique trade ID |
| market | VARCHAR | Market traded |
| side | VARCHAR | "long" or "short" |
| size | DOUBLE | Position size |
| entry_price | DOUBLE | Entry fill price |
| exit_price | DOUBLE | Exit fill price |
| entry_ts | BIGINT | Entry time |
| exit_ts | BIGINT | Exit time |
| pnl | DOUBLE | Realized P&L |
| fees | DOUBLE | Fees paid |
| is_replay | BOOLEAN | True if trade occurred during replay |

**`paper_positions`** — current open positions (for restart recovery)

| Column | Type | Description |
|--------|------|-------------|
| session_id | VARCHAR | FK to paper_sessions |
| market | VARCHAR | Market |
| side | VARCHAR | "long" or "short" |
| size | DOUBLE | Current size |
| entry_price | DOUBLE | Average entry |
| entry_ts | BIGINT | When opened |
| unrealized_pnl | DOUBLE | Last computed PnL |

### API Endpoints (New/Modified)

**`POST /api/v1/paper/deploy`** — Deploy strategy from backtest results

Request:
```json
{
  "strategy_code": "...",
  "strategy_name": "AdaptiveAlpha",
  "strategy_params": {"rsi_ob": 76, "rsi_os": 25},
  "market": "SOL-PERP",
  "initial_capital": 10000,
  "replay_start_ts": 1740000000,
  "risk_config": {
    "max_drawdown_pct": 0.15,
    "daily_loss_limit": 500,
    "max_position_pct": 0.95,
    "liquidation_enabled": true
  }
}
```

Response:
```json
{
  "session_id": "abc123",
  "status": "replaying",
  "replay_candles": 672
}
```

**`GET /api/v1/paper/portfolio`** — Aggregate portfolio view

Response:
```json
{
  "total_equity": 24832.50,
  "total_pnl": 4832.50,
  "total_initial_capital": 20000,
  "total_return_pct": 24.16,
  "sharpe_ratio": 2.34,
  "max_drawdown": 0.082,
  "sortino_ratio": 3.12,
  "win_rate": 0.58,
  "active_sessions": 2,
  "total_sessions": 3,
  "combined_equity_curve": [
    {"ts": 1740000000, "equity": 20000, "is_replay": true},
    ...
  ],
  "per_strategy": [
    {
      "session_id": "abc123",
      "strategy_name": "AdaptiveAlpha",
      "market": "SOL-PERP",
      "equity": 12831,
      "pnl": 2831,
      "sharpe": 2.12,
      "status": "live",
      "weight": 0.5
    },
    ...
  ],
  "allocation_weights": {"abc123": 0.5, "def456": 0.5}
}
```

**`POST /api/v1/paper/portfolio/weights`** — Set virtual allocation weights

Request:
```json
{
  "weights": {"abc123": 0.6, "def456": 0.4}
}
```

**`GET /api/v1/paper/status/{session_id}`** — Enhanced session status (existing, extended)

Additional fields in response:
```json
{
  "session_id": "abc123",
  "phase": "live",
  "replay_progress_pct": 100,
  "live_since_ts": 1742000000,
  "equity": 12831,
  "cash": 5200,
  "unrealized_pnl": 231,
  "positions": [...],
  "pending_orders": [...],
  "total_trades": 25,
  "total_fees": 12.50,
  "risk_config": {
    "max_drawdown_pct": 0.15,
    "daily_loss_limit": 500,
    "max_position_pct": 0.95,
    "liquidation_enabled": true
  },
  "risk_status": {
    "current_drawdown": 0.042,
    "daily_loss": -120,
    "max_position_used": 0.87,
    "margin_ratio": 0.12,
    "liquidation_distance_pct": 0.38,
    "any_breached": false
  },
  "equity_curve": [...],
  "metrics": {
    "sharpe_ratio": 2.12,
    "sortino_ratio": 3.47,
    "max_drawdown": 0.082,
    "win_rate": 0.52,
    "profit_factor": 1.75,
    "total_pnl": 2831,
    "total_return_pct": 28.3
  }
}
```

**`POST /api/v1/paper/{session_id}/risk`** — Update risk config for running session

Request:
```json
{
  "max_drawdown_pct": 0.20,
  "daily_loss_limit": 1000
}
```

### Replay-Forward Runner

The core execution logic for a paper session:

```
1. REPLAY PHASE
   - Load candles from store: query_candles(market, resolution, replay_start_ts, now)
   - Create BacktestEngine with strategy + params
   - Run engine.run(candles) to get replay results
   - Extract: equity curve, trades, final positions, final equity
   - Store all with is_replay=True
   - Set broker/context state to match end-of-replay positions

2. LIVE TRANSITION
   - Record live_start_ts = now
   - Set status = "live"
   - Initialize PaperBroker with replay-end state (cash, positions)
   - Start candle polling loop (every 10s)

3. LIVE LOOP (every 10s)
   - Query store for candles with ts > last_candle_ts
   - For each new candle:
     a. broker.process_candle(candle) — fill pending orders
     b. strategy.on_candle(candle, history, ctx)
     c. broker.process_candle(candle) — fill new orders
     d. risk_guard.check(broker) — check limits
     e. Record equity snapshot (is_replay=False)
     f. Persist state to DuckDB
   - If risk_guard.breached: auto-stop session

4. PRICE TICKER (every 5s, parallel)
   - Fetch mid-price from Drift DLOB
   - Update unrealized PnL on all open positions
   - This is display-only — doesn't trigger strategy logic
```

### Risk Guard

Checks run after each candle in the live loop:

```python
class RiskGuard:
    def __init__(self, config: RiskConfig):
        self.max_drawdown_pct = config.max_drawdown_pct    # e.g., 0.15
        self.daily_loss_limit = config.daily_loss_limit      # e.g., 500.0
        self.max_position_pct = config.max_position_pct      # e.g., 0.95
        self.liquidation_enabled = config.liquidation_enabled  # default True

    def check(self, broker, initial_capital, mark_prices: dict) -> Optional[str]:
        """Returns breach reason string, or None if OK."""
        # Max drawdown
        peak = max(broker.equity_history) if broker.equity_history else initial_capital
        dd = (peak - broker.equity) / peak
        if dd > self.max_drawdown_pct:
            return f"max_drawdown ({dd:.1%} > {self.max_drawdown_pct:.1%})"

        # Daily loss
        today_start_equity = ...  # equity at start of current UTC day
        daily_loss = today_start_equity - broker.equity
        if daily_loss > self.daily_loss_limit:
            return f"daily_loss (${daily_loss:.0f} > ${self.daily_loss_limit:.0f})"

        # Position size
        if broker.equity > 0:
            for pos in broker.positions.values():
                pos_value = pos["size"] * pos.get("mark_price", pos["entry_price"])
                pos_pct = pos_value / broker.equity
                if pos_pct > self.max_position_pct:
                    return f"max_position ({pos_pct:.0%} > {self.max_position_pct:.0%})"

        # Liquidation check (perps)
        if self.liquidation_enabled:
            result = self.check_liquidation(broker, mark_prices)
            if result:
                return result

        return None

    def check_liquidation(self, broker, mark_prices: dict) -> Optional[str]:
        """Simulate perp liquidation using Drift-like margin rules.

        Drift uses a maintenance margin of ~5% for most perps.
        If account equity falls below maintenance margin requirement,
        the position is liquidated — we close it at mark price and
        apply a liquidation penalty fee (0.5% of position notional).
        """
        MAINTENANCE_MARGIN_RATIO = 0.05   # 5% maintenance margin
        LIQUIDATION_FEE_PCT = 0.005       # 0.5% liquidation penalty

        total_margin_required = 0.0
        for market, pos in broker.positions.items():
            mark = mark_prices.get(market, pos["entry_price"])
            notional = pos["size"] * mark
            total_margin_required += notional * MAINTENANCE_MARGIN_RATIO

        if total_margin_required > 0 and broker.equity <= total_margin_required:
            # Liquidation triggered — close all positions at mark price with penalty
            for market, pos in list(broker.positions.items()):
                mark = mark_prices.get(market, pos["entry_price"])
                penalty = pos["size"] * mark * LIQUIDATION_FEE_PCT
                broker.cash -= penalty  # apply liquidation fee
            broker.close_all_positions(mark_prices)
            return f"liquidation (equity ${broker.equity:.0f} < margin req ${total_margin_required:.0f})"

        return None

    def margin_ratio(self, broker, mark_prices: dict) -> float:
        """Current margin ratio: equity / margin_required. <1.0 = liquidation."""
        total_req = 0.0
        for market, pos in broker.positions.items():
            mark = mark_prices.get(market, pos["entry_price"])
            total_req += pos["size"] * mark * 0.05
        return broker.equity / total_req if total_req > 0 else float("inf")

    def liquidation_distance_pct(self, broker, mark_prices: dict) -> float:
        """How far price can move against you before liquidation (as %)."""
        ratio = self.margin_ratio(broker, mark_prices)
        if ratio == float("inf"):
            return 1.0  # no positions, infinite distance
        return max(0, 1 - 1/ratio) if ratio > 0 else 0.0
```

### Price Ticker

Lightweight polling loop for display PnL:

```python
class PriceTicker:
    """Polls Drift DLOB every 5s for mid-prices. Updates unrealized PnL on open positions."""

    def __init__(self, markets: list[str], interval_s: float = 5.0):
        self.markets = markets
        self.interval_s = interval_s
        self.prices: dict[str, float] = {}

    async def run(self):
        while True:
            for market in self.markets:
                try:
                    price = fetch_mid_price(market)  # HTTP GET to dlob.drift.trade
                    self.prices[market] = price
                except Exception:
                    pass
            await asyncio.sleep(self.interval_s)

    def get_price(self, market: str) -> Optional[float]:
        return self.prices.get(market)
```

### Session Persistence

Sessions are saved to DuckDB so they survive server restarts:

- **On every candle**: upsert equity snapshot, update positions, update session status
- **On restart**: load all sessions with `status in ("live", "replaying")`, reconstruct broker state from `paper_positions` and `paper_trades`, resume candle polling from `last_candle_ts`
- **Batch writes**: equity snapshots are batched (write every 10 candles or 60 seconds) to avoid DB thrashing

---

## Frontend

### New Files

| File | Purpose |
|------|---------|
| `ui/src/pages/PaperTrading.tsx` | Main paper trading page |
| `ui/src/components/StrategyCard.tsx` | Sidebar strategy card with status/PnL |
| `ui/src/components/PortfolioMetrics.tsx` | Top metrics bar (equity, PnL, Sharpe, DD) |
| `ui/src/components/PortfolioChart.tsx` | Combined equity curve with replay/live marker |
| `ui/src/components/PositionsTable.tsx` | Open positions with live PnL |
| `ui/src/components/RiskStatus.tsx` | Risk limit indicators (gauges/bars) |
| `ui/src/hooks/usePaperTrading.ts` | Hook for polling paper trading API |

### Page Layout: Sidebar + Main Panel

```
+------------------+--------------------------------------------+
| STRATEGIES       | PORTFOLIO METRICS                          |
|                  | Equity  P&L   Sharpe  MaxDD  WinRate Active|
| [AdaptiveAlpha]  +--------------------------------------------+
|  SOL +$2,431     | COMBINED EQUITY CURVE                      |
|  LIVE 3d 14h     |                                            |
|                  |  ~~~~/\~~~/\~~~~~ | ~~~~~/\~~~/\~~~         |
| [RSI-MACD]       |  replay          | live                    |
|  BTC +$1,204     |  (dashed)        | (solid)                 |
|  LIVE 1d 6h      +--------------------------------------------+
|                  | OPEN POSITIONS              RISK STATUS    |
| [MeanRevert]     | SOL SHORT 45.2 +$187  | DD:  ████░░ 4.2%  |
|  ETH -$312       | BTC LONG 0.12  +$45   | Day: ███░░░ $120  |
|  STOPPED (risk)  |                       | Pos: ████░░ 87%   |
|                  |                       | Liq: ██████░ 38%  |
|                  +--------------------------------------------+
| [+ Deploy from   | RECENT TRADES                              |
|  BacktestLab]    | BUY 12.1 SOL @$128 +$234  3h ago          |
+------------------+--------------------------------------------+
```

### Sidebar Strategy Cards

Each card shows:
- Strategy name + market
- Status indicator: green dot (live), yellow (replaying), red (stopped), red with icon (risk stopped)
- Current P&L (green/red)
- Uptime or time since stopped
- Click → main panel switches to individual strategy view

### Strategy Detail View (when card is clicked)

Main panel switches to show:
- Individual equity curve with replay/live marker
- Per-strategy metrics (Sharpe, Sortino, win rate, profit factor, max DD)
- Trade history table (entry/exit prices, PnL, duration, replay/live flag)
- Risk config editor (sliders for DD limit, daily loss, position size)
- Stop/Kill buttons

### BacktestLab Integration

Add to backtest results view (`BacktestLab.tsx`):
- "Deploy to Paper" button appears when backtest completes successfully
- Opens a confirmation dialog with:
  - Capital amount (pre-filled from backtest)
  - Replay start date picker (defaults to backtest start date, max 30 days back)
  - Risk limits (DD, daily loss, position size) with sensible defaults
  - Market (pre-filled from backtest)
- On confirm: `POST /paper/deploy` and redirect to Paper Trading page

### Polling Strategy

- Portfolio view: poll `GET /paper/portfolio` every 2 seconds
- Individual strategy: poll `GET /paper/status/{id}` every 2 seconds
- Price ticker updates are included in the status response (server-side)
- Use `setInterval` with cleanup on unmount

---

## Testing

### Backend Tests

- **Replay-forward runner**: Mock store with candles, verify replay produces correct equity → transitions to live → processes new candles
- **Risk guard**: Test each limit (DD, daily loss, position size) triggers correctly
- **Session persistence**: Create session → stop server → restart → verify session resumes
- **Price ticker**: Mock DLOB response, verify PnL updates
- **API endpoints**: TestClient tests for deploy, portfolio, status, risk config

### Frontend Tests

- Strategy card renders correct status/PnL
- Portfolio metrics update on poll
- Equity chart shows replay/live boundary
- Deploy dialog validates inputs
- Risk gauges reflect current vs limit values

---

## Migration / Backwards Compatibility

- Existing `POST /paper/start` and `GET /paper/status` endpoints remain unchanged
- New `/paper/deploy` is a superset of `/paper/start` (adds replay, risk config, persistence)
- New DuckDB tables are created on startup (additive migration, no schema changes to existing tables)
- Existing paper sessions (started via old API) won't appear in the new portfolio view (they don't have persistence records) — this is acceptable since paper sessions are ephemeral by nature
