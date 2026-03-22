# Cross-Venue Perpetual Funding Dislocation Strategy

## 1. Strategy Summary

This is **not** a pure “funding mean reversion” trade.

The real trade is a **cross-venue relative-value strategy**:

- identify a perp venue whose contract is unusually rich versus a normalized cross-venue benchmark
- short that rich perp
- hedge the underlying elsewhere
- earn from:
  - **funding carry**
  - **basis compression**
- avoid relying on outright directional price moves

A good mental model is:

\[
\text{PnL}
=
\text{funding received}
+
\text{basis compression}
-
\text{fees}
-
\text{slippage}
-
\text{hedge carry}
-
\text{liquidation / venue risk}
\]

---

## 2. Core Thesis

Suppose venue \(A\) has funding materially above the rest of the market:

\[
f_A \gg \mu_{\text{venues}}
\]

That often means one or more of the following:

- the perp on venue \(A\) is rich versus spot or index
- longs are crowded on venue \(A\)
- the venue has a local order-flow imbalance
- capital is fragmented and the spread has not yet been arbitraged away
- the venue carries idiosyncratic risk that shows up in pricing

The tradable version of the idea is:

\[
\text{Short rich perp on venue } A
\quad+\quad
\text{Long hedge elsewhere}
\]

This is a **relative-value / basis trade**, not a direct bet on funding as an isolated variable.

---

## 3. Trade Construction

### Best v1 trade
- **Short perp on rich venue**
- **Long spot hedge**

Why this is best:
- simpler
- one funding process instead of two
- easier PnL attribution
- easier risk management

### Advanced v2 trade
- **Short rich perp on venue A**
- **Long cheaper perp on venue B**

Why this is harder:
- two funding processes
- two liquidation engines
- more basis mismatch
- more operational complexity

### What this is not
You are **not** “shorting funding” directly. Funding is a signal that tells you the perp may be rich and that carry may be attractive.

---

## 4. Signal Definition

### 4.1 Normalize funding
Do **not** compare raw venue funding prints directly.

Different venues use different:
- funding intervals
- mark/index formulas
- payout mechanics
- caps / constraints

Convert every venue to a common unit such as:

\[
f_{i,t}^{(1h)}
\]

### 4.2 Build a benchmark
Use a benchmark across venues:

\[
\mu_t = \sum_i w_{i,t} f_{i,t}^{(1h)}
\]

Possible weights:
- open interest weights
- deployable liquidity weights
- equal weight as a rough baseline

### 4.3 Compute dislocation
\[
d_t = f_{A,t}^{(1h)} - \mu_t
\]

### 4.4 Standardize
\[
z_t = \frac{d_t - \bar d}{s_d}
\]

where:
- \(\bar d\) is a rolling mean
- \(s_d\) is rolling standard deviation

### 4.5 Require basis confirmation
Do not enter on funding alone.

Also require the perp to look rich in price terms:

\[
basis_A = \frac{P_A^{perp} - P_A^{index}}{P_A^{index}}
\]

The best setups are when:
- funding spread is extreme
- perp premium is positive
- depth is sufficient
- expected net edge exceeds costs

---

## 5. Data Requirements

## 5.1 Mandatory data

### A. Funding data
Per venue, per market:
- historical realized funding
- current funding
- predicted next funding if available
- funding timestamp
- funding interval

### B. Price anchors
Per venue:
- mark price
- index price or oracle price
- best bid / ask
- mid price

You need these because this is partly a **premium compression trade**, not just a funding trade.

### C. Instrument metadata
Per instrument:
- contract multiplier / contract size
- quote currency
- settle currency
- funding interval
- margin rules
- tick size
- lot size

### D. Trading cost data
Per venue:
- taker fee
- maker fee / rebate
- borrow / financing cost if spot margin is used
- transfer / withdrawal frictions

---

## 5.2 Strongly recommended data

### E. Open interest
Used for:
- benchmark weighting
- crowding detection

### F. Orderbook depth / impact price
Used for:
- deployable size
- slippage estimates
- execution filters

### G. Realized fill data
Used for:
- slippage measurement
- queue quality
- execution diagnostics

### H. Position / collateral / liquidation data
Used for:
- free collateral tracking
- liquidation distance
- risk controls

---

## 5.3 Nice-to-have data
- long/short ratio
- liquidation heatmaps
- insurance fund / rebate pool condition
- venue incident / status feeds
- borrow utilization

---

## 6. Unified Internal Data Schema

### Market snapshot schema
```text
timestamp
venue
symbol
base_asset
quote_asset
settle_asset
market_type
funding_rate_raw
funding_interval_hours
funding_rate_hourly
predicted_funding_rate_hourly
funding_timestamp
mark_price
index_price
oracle_price
best_bid
best_ask
mid_price
spread_bps
impact_bid_px
impact_ask_px
open_interest_usd
taker_fee_bps
maker_fee_bps
contract_multiplier
tick_size
lot_size
```

### Account / position schema
```text
venue
account_id
equity_usd
free_collateral_usd
used_margin_usd
maintenance_margin_usd
position_size
avg_entry
liq_price
realized_pnl
unrealized_pnl
```

---

## 7. Venue Roles

A venue only needs to satisfy one of these roles.

### 7.1 Benchmark venue
Used only for signal generation.

Requirements:
- public funding history
- current mark / index data
- reliable public API
- enough liquidity that the funding is informative

### 7.2 Execution venue
Used for actual trading.

Requirements:
- strong API / SDK support
- private trading endpoints
- fill / account / position APIs
- good enough liquidity
- acceptable fee structure
- operational stability

Not every benchmark venue needs to be an execution venue.

---

## 8. Recommended Venue Stack

## 8.1 Core benchmark basket
Use these first:

- **Drift**
- **Hyperliquid**
- **Binance Futures**
- **OKX**
- **Bybit**

This gives:
- one strong Solana-native perp venue
- one strong high-speed perp venue
- several mature centralized derivatives venues

## 8.2 Good second-tier additions
- **dYdX**
- **Kraken Derivatives**
- **Coinbase International Exchange**
- **Aevo**

## 8.3 Solana-specific notes
- **Drift** should be the first Solana perp venue
- **Jupiter spot** is useful as the hedge path
- **Jupiter Perps** should not be your first production dependency
- **Zeta** should not be used in a new build

---

## 9. Recommended Execution Design

### Best v1 design
- **Signal from many venues**
- **Execute short perp on Drift**
- **Hedge with spot on Solana**

Why:
- lowest complexity
- easier debugging
- one funding leg
- cleaner attribution

### Best v2 design
- short richest perp venue
- long cheapest viable hedge venue
- dynamic venue selection by expected net edge

---

## 10. Entry Logic

Enter only if all of the following are true:

1. normalized funding spread is extreme
2. perp premium versus index/oracle is positive
3. expected carry exceeds round-trip costs by a healthy margin
4. orderbook depth can absorb your size
5. post-trade margin remains conservative

A simple score model is:

\[
Score
=
a \cdot z_{\text{funding}}
+
b \cdot z_{\text{basis}}
-
c \cdot \text{costs}
-
d \cdot \text{liq risk}
\]

Trade only when:

\[
Score > \text{threshold}
\]

---

## 11. Exit Logic

Exit when any of the following happens:

- funding spread compresses below threshold
- basis compresses back toward fair value
- expected future carry no longer covers costs
- stop-loss on basis widening triggers
- max holding time is reached
- venue health deteriorates
- hedge quality degrades materially

---

## 12. Backtest Requirements

### Layer 1: signal backtest
Needed:
- historical funding
- mark/index series
- instrument metadata
- fee assumptions

Goal:
- test whether the signal exists

### Layer 2: execution-aware backtest
Add:
- bid/ask snapshots
- depth / impact estimates
- realistic latency
- partial fill assumptions

Goal:
- test whether the signal survives costs

### Layer 3: portfolio / risk backtest
Add:
- collateral fragmentation
- liquidation rules
- venue leverage constraints
- transfer delays
- margin calls

Goal:
- test whether the strategy is operationally survivable

---

## 13. Live System Requirements

### Services
- **collector**: funding, mark, index, OI, books
- **normalizer**: convert venue-specific funding into common hourly terms
- **signal engine**: compute dislocations and trade decisions
- **execution engine**: place orders and hedges
- **risk engine**: enforce leverage, delta, and liquidation controls
- **storage**: persist all market and account state
- **monitoring**: alert on stale feeds, hedge mismatch, and venue failures

### Minimum safeguards
- stale-feed kill switch
- per-venue notional limits
- max basis-widening stop
- max holding-time stop
- hedge-completion timeout
- minimum margin buffer
- manual circuit breaker

---

## 14. Main Failure Modes

### A. Funding stays dislocated
High funding can remain elevated much longer than expected.

### B. Basis widens before it compresses
You may collect funding but still lose mark-to-market first.

### C. Hedge is imperfect
Spot and perp, or perp and perp, may not track perfectly.

### D. Realized funding differs from quoted funding
Venue mechanics may make realized funding different from displayed funding.

### E. Capital fragmentation reduces real returns
Gross edge may look good but return on total capital may be poor.

### F. The premium is structural
Sometimes the rich venue is not “wrong”; it may simply embed a genuine venue-specific risk premium.

---

## 15. Practical Build Plan

### Phase 1: research / paper
- venues: Drift, Hyperliquid, Binance, OKX, Bybit
- market: one asset first, such as SOL or BTC
- trade model: short rich perp + long spot hedge
- output: decomposed PnL
  - funding
  - basis compression
  - slippage
  - fees

### Phase 2: live paper bot
- compute signal continuously
- simulate fills using live books
- no real orders

### Phase 3: tiny live deployment
- execute only on Drift first
- hedge with Solana spot
- strict notional cap
- strict risk limits

Only after that should you add a second live perp venue.

---

## 16. Bottom Line

### What the strategy needs
- multiple venues for the **signal**
- at least one strong perp venue for **execution**
- one reliable hedge path
- normalized funding math
- basis confirmation
- real execution and risk infrastructure

### Best starting setup
- **Benchmark**: Drift, Hyperliquid, Binance, OKX, Bybit
- **Execution v1**: Drift short perp + Solana spot hedge
- **Later additions**: dYdX, Kraken Derivatives, Coinbase International, Aevo

### What not to do
- do not average raw funding prints
- do not start with many execution venues
- do not assume every funding outlier mean-reverts quickly

The correct framing is:

> a cross-venue relative-value strategy where funding is the carry signal and perp premium is the confirmation signal

---

## 17. Implementation Checklist

### Research checklist
- [ ] choose one asset to start
- [ ] normalize funding into hourly units
- [ ] build cross-venue benchmark
- [ ] compute funding spread z-score
- [ ] compute perp premium versus index/oracle
- [ ] estimate fees and slippage
- [ ] simulate short-perp / long-spot trades
- [ ] attribute PnL into funding, basis, costs

### Live-readiness checklist
- [ ] connect all venue data adapters
- [ ] store market snapshots
- [ ] build risk engine
- [ ] build execution adapter
- [ ] add hedge-completion logic
- [ ] add kill switches
- [ ] add monitoring and alerting
- [ ] run live paper mode before real capital