# Strategy Templates — Design Spec

> Sub-project 5.1 of Phase 5 (ROADMAP.md §5.1)
> Date: 2026-04-02

## Overview

Add 4 new strategy templates (plus README for existing FundingArbStrategy) with comprehensive documentation, reproducible backtest examples, and tests. Each strategy is battle-tested with reference results and known limitations documented.

### Scope

**In scope:**
- 4 new strategy files + tests
- 5 strategy READMEs in `docs/strategies/`
- Brief docstrings in strategy .py files, detailed docs in `docs/strategies/`
- Reproducible backtest commands + static reference results
- Parameter ranges for Optuna optimization
- Venue-specific examples where applicable

**Out of scope:**
- Existing 15 strategy templates (no changes needed)
- Strategy marketplace or submission process (§5.4)
- UI strategy deployment panel (§5.3)

---

## 1. Strategy List

### 1.1 FundingArbStrategy (existing)

**File:** `flint/strategy/funding_arb.py` (already exists from Phase 3)
**README:** `docs/strategies/funding_arb.md` (new)

Cross-venue funding rate arbitrage. Long on low-funding venue, short on high-funding venue. Delta-neutral.

**Venue showcase:** Drift + Hyperliquid cross-venue backtest.

### 1.2 MomentumBreakoutStrategy (new)

**File:** `flint/strategy/momentum_breakout.py`
**README:** `docs/strategies/momentum_breakout.md`

Breakout strategy that enters when price exceeds N-bar high/low, with optional Pyth oracle confirmation.

**Signal logic:**
- Entry long: candle close > highest high of last N bars AND (if oracle_confirmation enabled) oracle price > candle close
- Entry short: candle close < lowest low of last N bars AND oracle price < candle close
- Exit: trailing stop at `trailing_stop_pct` from peak, or price crosses opposite N-bar extreme

**Parameters:**
```python
{
    "breakout_lookback": {"type": "int", "low": 10, "high": 50, "default": 20},
    "trailing_stop_pct": {"type": "float", "low": 0.01, "high": 0.05, "default": 0.02},
    "oracle_confirmation": {"type": "int", "low": 0, "high": 1, "default": 1},
    "candle_resolution_s": {"type": "int", "low": 60, "high": 3600, "default": 3600},
}
```

**Venue showcase:** Drift with Pyth oracle confirmation. Note: oracle confirmation only available on Drift (Pyth feed).

### 1.3 FundingMeanReversionStrategy (new)

**File:** `flint/strategy/funding_mean_reversion.py`
**README:** `docs/strategies/funding_mean_reversion.md`

Bollinger bands on hourly funding rate. Enters when funding rate touches a band, exits when it reverts to the mean.

**Signal logic:**
- Compute Bollinger bands on trailing funding rates (N hours lookback)
- Entry long: funding rate < lower band (rate is abnormally low = being paid, expect reversion up)
- Entry short: funding rate > upper band (rate abnormally high, expect reversion down)
- Exit: funding rate crosses the middle band, or max hold time exceeded

**Parameters:**
```python
{
    "bb_lookback": {"type": "int", "low": 12, "high": 48, "default": 24},
    "bb_std": {"type": "float", "low": 1.5, "high": 3.0, "default": 2.0},
    "max_hold_hours": {"type": "int", "low": 4, "high": 24, "default": 12},
    "position_size_pct": {"type": "float", "low": 0.1, "high": 0.9, "default": 0.5},
    "candle_resolution_s": {"type": "int", "low": 60, "high": 3600, "default": 3600},
}
```

**Venue showcase:** Hyperliquid (hourly funding, tighter spreads). Note: Hyperliquid backtests use candle-level fill modeling (no historical orderbook data available).

### 1.4 MevArbMonitor (new)

**File:** `flint/strategy/mev_arb_monitor.py`
**README:** `docs/strategies/mev_arb_monitor.md`

Monitoring strategy that scans for Raydium/Orca pool arb opportunities each tick. Logs profitable routes and optionally fires alerts. Does NOT execute arbs — surfaces them for analysis.

**Signal logic:**
- Each tick: build pool graph from stored pool snapshots
- Run ArbDetector.find_arb_routes() from SOL mint
- Log routes exceeding min_profit_bps
- If alert_enabled: fire notification via ctx (if NotificationManager configured)
- Always returns Signal.HOLD (monitoring only)

**Parameters:**
```python
{
    "min_profit_bps": {"type": "float", "low": 5.0, "high": 50.0, "default": 10.0},
    "max_hops": {"type": "int", "low": 2, "high": 4, "default": 3},
    "alert_enabled": {"type": "int", "low": 0, "high": 1, "default": 0},
    "candle_resolution_s": {"type": "int", "low": 60, "high": 3600, "default": 60},
}
```

**Venue showcase:** N/A (DEX monitoring, not venue-specific trading).

### 1.5 BasisTradeStrategy (new)

**File:** `flint/strategy/basis_trade.py`
**README:** `docs/strategies/basis_trade.md`

Exploits price difference between the same asset on different venues. Enters when basis exceeds threshold, exits on convergence.

**Signal logic:**
- Compute basis: `(venue_a_price - venue_b_price) / venue_b_price * 10000` (in bps)
- Entry: |basis| > entry_basis_bps → long on cheaper venue, short on expensive venue
- Exit: |basis| < exit_basis_bps, or max hold time exceeded
- Delta-neutral: equal USD notional on both legs

**Parameters:**
```python
{
    "entry_basis_bps": {"type": "float", "low": 10.0, "high": 100.0, "default": 30.0},
    "exit_basis_bps": {"type": "float", "low": 2.0, "high": 20.0, "default": 5.0},
    "max_hold_hours": {"type": "int", "low": 1, "high": 48, "default": 12},
    "position_size_usd": {"type": "float", "low": 100, "high": 10000, "default": 1000},
    "candle_resolution_s": {"type": "int", "low": 60, "high": 3600, "default": 3600},
}
```

**Venue showcase:** Drift vs Hyperliquid cross-venue backtest with `"venue:market"` composite keys.

---

## 2. README Template

Each README in `docs/strategies/` follows this structure:

```markdown
# Strategy Name

One-sentence description.

## How It Works
- Signal logic (2-3 bullet points)
- Entry/exit conditions

## Parameters
| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|

## Backtest Example

### Single-Venue
```bash
flint backtest --strategy <name> --market SOL-PERP --start 2026-01-01 --end 2026-03-01
```

### Reference Results (SOL-PERP, Jan-Mar 2026)
- PnL: $X
- Trades: N
- Win Rate: X%
- Sharpe: X.XX
- Max Drawdown: X%

## Known Limitations
- Bullet list of caveats

## Venues
- Which venues supported and why
- Data availability notes (e.g., no Hyperliquid orderbook history)
```

---

## 3. Docstring Pattern

Each strategy .py file gets a module-level docstring:

```python
"""MomentumBreakoutStrategy — breakout entry with oracle confirmation.

Enters when price exceeds N-bar high/low. Uses Pyth oracle for confirmation
on Drift. See docs/strategies/momentum_breakout.md for full documentation.
"""
```

Brief, points to the detailed README.

---

## 4. Testing Strategy

Each strategy gets tests in `tests/test_strategy_<name>.py`:

- `test_name`: Returns correct string
- `test_parameters`: Returns expected keys with valid ranges
- `test_entry_signal`: With mock context, triggers entry under right conditions
- `test_no_entry`: No entry when conditions aren't met
- `test_exit_signal`: Exits on exit conditions
- `test_reset`: Clears state

All tests use mock context (no network calls, no real data).

---

## 5. Dependencies

No new dependencies. Uses existing strategies, indicators, and execution infrastructure.

---

## 6. ROADMAP Update

After implementation, update ROADMAP.md §5.1 with "Implemented" checkboxes.
