# Solana MEV: A Complete Technical Guide

## What Is MEV, Really?

**Maximal Extractable Value (MEV)** is the profit that can be extracted by controlling **transaction ordering** within a block. Whoever decides what order transactions execute in can insert their own trades before, after, or around yours to capture value.

On Ethereum, this plays out in a public mempool — everyone can see pending transactions and front-run them. **Solana is fundamentally different:**

| | Ethereum | Solana |
|---|---|---|
| Mempool | Public, global | **None** — txs go directly to the leader validator |
| Block time | 12 seconds | **400ms** slots |
| Execution | Sequential (EVM) | **Parallel** (Sealevel — multiple CPU cores) |
| Ordering game | Gas price auctions | **Latency races** — who reaches the leader first |
| MEV infrastructure | Flashbots (proposer-builder separation) | **Jito** (bundles + block engine, 92% stake weight) |

The result: Solana MEV is an **infrastructure arms race**, not a strategy competition. The winners have the lowest latency, not the smartest algorithms.

---

## Types of MEV on Solana

### 1. Arbitrage (the "good" MEV)

Price differs between DEXs? Buy low on Raydium, sell high on Orca. Atomic, single-transaction, risk-free.

```
Raydium SOL/USDC: $150.10
Orca SOL/USDC:    $150.35
→ Buy on Raydium, sell on Orca → $0.25/SOL profit
```

This is **beneficial** — it aligns prices across venues. Most Solana MEV revenue comes from this.

**CEX-DEX arb** is the higher-value variant: monitor Binance prices, exploit divergences with on-chain DEXs. Requires capital on both sides and ultra-low latency.

### 2. Liquidations

Lending protocols (Marginfi, Kamino, Solend) let liquidators repay underwater loans and claim collateral at a 5-10% discount. Bots watch oracle prices and account health factors in real-time. When a position becomes liquidatable, it's a race to submit first.

### 3. Sandwich Attacks

The predatory form of MEV. A bot sees your large swap, places a trade *before* yours (pushing the price against you), then trades *after* yours (profiting from the inflated price).

On Solana, these happened through **Jito's mempool** — a ~200ms window where pending transactions were visible. **Jito shut down the public mempool in March 2024** specifically because sandwich attacks were rampant. This reduced sandwich profitability by ~60-70%.

They haven't disappeared entirely — private mempools and validator relationships still enable them — but the scale has dramatically decreased. Marinade Finance blacklisted 50+ validators participating in sandwich attacks, affecting over $2 billion in delegated stake.

### 4. JIT Liquidity (Drift-specific)

Drift runs a **Dutch auction** on every market order:
1. Order enters a ~5-second auction window
2. Price starts better than the vAMM, deteriorates linearly toward the vAMM price
3. Market makers race to fill at the best price
4. User gets better execution than the vAMM alone

This is **structured MEV** — designed to be beneficial. Drift's three-layer system (JIT auctions -> DLOB -> vAMM) is unique in DeFi.

### 5. Backrunning

The most common benign MEV. After a large trade creates a price imbalance:
1. Detect the trade via Geyser/gRPC
2. Calculate the optimal arb route
3. Submit a trade positioned *after* the triggering trade

A Solana MEV bot earned **$1.8 million from a single backrun** on a $9M WIF trade.

### 6. Token Launch Sniping

The hottest MEV niche in 2025-2026:
- **Pump.fun bonding curve** — bots buy in the first milliseconds of a launch
- **Raydium migration** — when a pump.fun token hits its cap, liquidity migrates to Raydium. Bots snipe the initial pool at the lowest price
- **Bundler bots** automate the entire pipeline: launch + initial buys + market making across multiple wallets

---

## Infrastructure Required

This is where most people underestimate MEV. **The code is the easy part. The infrastructure is everything.**

### Tier 1: Entry Level (Monitoring, Research)
- Helius or QuickNode paid RPC (~$50-200/month)
- Python or TypeScript
- Can monitor opportunities but can't compete for them

### Tier 2: Competitive (Active Bot)
- Dedicated RPC node (256GB+ RAM, NVMe SSD)
- Yellowstone gRPC subscription for real-time streaming
- Jito bundle submission
- Rust codebase
- **$2,000-10,000/month infrastructure costs**

### Tier 3: Professional
- Co-located hardware in the same data center as validators
- Custom Geyser plugin deployment
- Direct validator relationships
- Sub-millisecond latency
- **$10,000-50,000+/month**

### The Key Infrastructure Components

#### Jito (92% of Solana validators)

- **Bundles**: Up to 5 transactions executed atomically. All-or-nothing. Tip in the last tx.
- **Block Engine**: Receives bundles, simulates them, selects the highest-value set.
- **Relayer**: Holds transactions for ~200ms, creating a window for bundle formation.
- **BAM (Block Assembly Marketplace)**: Launched July 2025 — uses TEE-encrypted mempools, open-source, reduces extractive MEV. Supports Application-Controlled Execution (ACE) where apps can define custom ordering logic.
- **DontFront**: Protective feature guaranteeing transactions with a specific prefix are placed at index 0 in any bundle, preventing front-running within bundles.

#### Yellowstone gRPC (Geyser Plugins)

- Streams data directly from validator memory — faster than RPC
- Subscribe to specific account changes, program events, slot updates
- Sub-50ms latency for state changes
- Built on Protocol Buffers + HTTP/2 transport
- Providers: Helius (LaserStream), Triton, QuickNode, Chainstack
- **This is how every serious bot sees opportunities**

#### Priority Fees and Compute Units

```
Fee = ceil(compute_unit_price x compute_unit_limit / 1,000,000) lamports
```

- Default CU limit: 200,000 per transaction (max: 1,400,000)
- General use: 1,000-5,000 micro-lamports/CU
- MEV-competitive: 10,000-100,000+ micro-lamports/CU
- You're charged on the **limit** you set, not actual usage — tight CU estimation matters
- `SetComputeUnitLimit` and `SetComputeUnitPrice` must be the first instructions

#### Stake-Weighted Quality of Service (SWQoS)

Validators prioritize transactions routed through staked connections. Using Helius or other RPC providers that route through staked validators gives a delivery advantage that doesn't exist on Ethereum.

---

## Architecture of a Solana MEV Bot

```
+-----------------------------------------------------------+
|                    EVENT DETECTION                         |
|  Yellowstone gRPC / Geyser -> Pool accounts, oracles,     |
|  program events. Every account update = price recalc      |
+-----------------------------+-----------------------------+
                              |
+-----------------------------v-----------------------------+
|                 OPPORTUNITY EVALUATION                    |
|  Recalculate prices across all DEXs -> Find profitable    |
|  routes (2-hop, 3-hop) -> Calculate optimal trade size    |
|  -> Estimate costs (fees + tip + slippage) -> Profit?     |
+-----------------------------+-----------------------------+
                              |
+-----------------------------v-----------------------------+
|              TRANSACTION CONSTRUCTION                     |
|  1. SetComputeUnitLimit  2. SetComputeUnitPrice           |
|  3. [Flash loan borrow]  4. Swap instructions             |
|  5. [Flash loan repay]   6. Jito tip (last tx)            |
|  -> Simulate via RPC -> Verify success                    |
+-----------------------------+-----------------------------+
                              |
+-----------------------------v-----------------------------+
|               TRANSACTION SUBMISSION                      |
|  Jito Bundle (atomic, 5 tx max) -- OR -- Direct RPC      |
|  (staked connection for SWQoS priority)                   |
+-----------------------------------------------------------+
```

### Event Detection

For **arb bots**: subscribe to all pool accounts on target DEXs via Geyser. Every account update triggers a price recalculation across all venues.

For **liquidation bots**: subscribe to lending protocol user accounts + oracle price feed accounts. Recalculate health factors on every update.

For **sniping bots**: subscribe to program events for pump.fun `create` instructions or Raydium `initialize` instructions.

### Opportunity Evaluation

1. Receive account update from Geyser stream
2. Recalculate all affected prices using AMM math
3. Identify profitable routes (2-hop or 3-hop across DEXs)
4. Calculate optimal trade size (binary search or analytical)
5. Estimate costs: priority fee + Jito tip + slippage
6. `Net Profit = Revenue - (Priority Fee + Jito Tip + Slippage)`
7. If profit > threshold -> execute

### Transaction Construction

Flash loans from protocols like Solend enable **capital-free arbitrage** — borrow, arb, repay in one atomic transaction.

### Risk Management

- **Failed transactions still cost fees**: Unlike Ethereum, Solana charges compute units even on failure. Simulate before submitting.
- **Stale state**: Between simulation and execution, accounts can change. Must handle gracefully.
- **Competition**: Multiple bots targeting the same opportunity = only one winner, rest pay fees for nothing.
- **Tip calibration**: Overpaying erodes profit. Underpaying means bundles aren't included. Dynamic estimation is necessary.
- **Transaction expiry**: `recentBlockhash` limits validity to ~150 blocks (~60 seconds).

---

## Key Protocols and Tools

| Tool | Role |
|---|---|
| **Jito Labs** | Bundle infrastructure, block engine, BAM, 92% validator share |
| **Yellowstone gRPC** | Real-time validator data streaming (Geyser plugins) |
| **Helius** | Enhanced APIs, webhooks, LaserStream gRPC, priority fee API |
| **Jupiter** | DEX aggregator, limit order keepers, route calculation |
| **Drift Protocol** | JIT auctions, DLOB, vAMM, keeper bot network |
| **Raydium** | AMM + CLOB integration, pump.fun migration target |
| **Orca** | Whirlpool concentrated liquidity |
| **Phoenix (Ellipsis Labs)** | Fully on-chain CLOB, crankless design |
| **Triton/rpcpool** | Maintains Yellowstone gRPC |

---

## Language Choice

| | Rust | TypeScript | Python |
|---|---|---|---|
| **Use for** | Production MEV bots | Prototyping, dashboards | Research, backtesting |
| **Latency** | Best (no GC pauses) | Good | Not competitive |
| **Solana SDK** | First-class (`solana-sdk`) | Good (`@solana/web3.js`) | Decent (`solana-py`, `solders`) |
| **Jito SDK** | Best (`jito-sdk` crate) | Good (`jito-ts`) | Minimal (community) |
| **Reality** | **What winners use** | What tutorials use | What researchers use |

The overwhelming majority of competitive Solana MEV bots are written in **Rust**. Typical architecture:

- **Core bot logic**: Rust (event detection, route calculation, tx construction)
- **Strategy research/backtesting**: Python (data analysis, simulation, optimization)
- **Monitoring/dashboards**: TypeScript (web UI, alerting, position tracking)
- **On-chain programs**: Rust via Anchor or native Solana BPF

---

## The Numbers (2025-2026)

| Metric | Value |
|---|---|
| **2025 Solana MEV revenue** | **$720 million** |
| Jito tips (trailing year) | 3.75M SOL |
| Jito bundles processed | 3+ billion |
| Jito tips as % of Solana revenue | 42-66% |
| JitoSOL TVL | ~$2.9B |
| Jito validator stake share | 92% |
| Peak month (Nov 2024) | $210M in tips |
| Sandwich attack reduction (post-mempool shutdown) | 60-70% |
| BAM launch | July 21, 2025 |

MEV has overtaken priority fees as the **largest component** of Solana's economic value.

### Is It Accessible to Solo Developers?

**Partially.**

- **Accessible**: Long-tail strategies (obscure token pairs, less-competitive liquidation markets, keeper operations on Drift/Jupiter)
- **Accessible**: Token launch sniping on pump.fun/Raydium (high-risk but low barrier)
- **Not accessible**: Core SOL/USDC arb on Raydium/Orca (dominated by professional operations)
- **Not accessible**: Sandwich attacks (require private infrastructure and validator relationships)
- **Minimum viable infrastructure**: ~$2,000-10,000/month for competitive setups

---

## The Gaps — Where Tools Are Missing

### 1. MEV Strategy Backtesting

**The single biggest gap in the ecosystem.** No production-quality framework exists for backtesting MEV strategies against historical Solana state. You can't easily answer "what would my arb bot have earned last month?" without building significant custom infrastructure.

A backtest engine with realistic fills could be extended to simulate DEX pool states, arb routes, and competition dynamics.

### 2. Unified DEX Simulation Library

Each DEX (Raydium, Orca, Phoenix, Meteora) has different AMM math for price calculation. No single library provides accurate, up-to-date simulation for all of them. Jupiter's routing SDK comes closest but is designed for routing, not strategy simulation.

### 3. MEV Opportunity Monitoring

Real-time dashboards showing arb spreads across DEXs, positions approaching liquidation, JIT auction statistics, and funding rate dislocations. Building this currently requires custom infrastructure.

### 4. Paper Trading for MEV

No tool lets you simulate bundle submission against live data without risking capital. Tracking theoretical P&L for MEV strategies requires building everything from scratch.

### 5. Priority Fee / Tip Analytics

Dynamic tip estimation is ad-hoc across the ecosystem. No standard library or service provides reliable "what tip should I pay right now?" guidance based on current block competition and historical success rates.

### 6. Python MEV SDK

The Rust Jito SDK is mature. TypeScript is decent. Python has almost nothing for bundle submission. A Python SDK for MEV research and prototyping (backed by Rust via `solders`) would fill a real gap for researchers and strategy developers.

### 7. Instruction-Level CPI Debuggers

Debugging complex cross-program invocations common in MEV transactions is painful. No good tool exists for visualizing CPI call trees and account diffs across multi-DEX transactions.

### 8. Drift JIT Participation Tools

Drift's JIT auctions create structured MEV opportunities for market makers, but there's no easy way to monitor auction statistics, analyze fill rates, or participate without building custom keeper bot infrastructure.

---

## Bottom Line

MEV on Solana is a **$720M/year industry** dominated by infrastructure. The code is less important than the pipes.

The gap isn't "how do I build an arb bot?" — that's well-documented. The gap is **"how do I research, backtest, and iterate on MEV strategies without spending $10K/month on infrastructure?"**

The highest-value additions for a Solana trading platform:

1. **MEV backtesting** — replay historical state, simulate arb/liquidation strategies against real pool data
2. **DEX pool simulation** — accurate AMM math for Raydium, Orca, Phoenix, Meteora route profitability
3. **Drift JIT tools** — monitor auctions, analyze fill rates, participate as a market maker
4. **MEV monitoring dashboard** — real-time spreads, liquidation proximity, auction stats
5. **Python MEV SDK** — bundle submission, simulation, and tip estimation in Python
6. **Paper trading for MEV** — simulate bundle execution against live data, track theoretical P&L

The infrastructure layer (Jito, Geyser, co-location) is well-served. The **research and development layer** — backtesting, simulation, iteration — is where the ecosystem needs tooling.

---

## References

- [Solana Trading Infrastructure 2026 - Chainstack](https://chainstack.com/solana-trading-infrastructure-2026/)
- [Solana Ecosystem Report H1 2025 - Helius](https://www.helius.dev/blog/solana-ecosystem-report-h1-2025)
- [How Jito-Solana Works - Deep Dive](https://thogiti.github.io/2025/01/01/How-Jito-Solana-Works.html)
- [Jito Bundles Guide - QuickNode](https://www.quicknode.com/guides/solana-development/transactions/jito-bundles)
- [Jito Labs Documentation](https://docs.jito.wtf/)
- [Solana MEV Report - Helius](https://www.helius.dev/blog/solana-mev-report)
- [Solana MEV Introduction - Helius](https://www.helius.dev/blog/solana-mev-an-introduction)
- [Yellowstone gRPC - Helius](https://www.helius.dev/docs/grpc)
- [Yellowstone gRPC - GitHub](https://github.com/rpcpool/yellowstone-grpc)
- [Jito MEV Bot Reference - GitHub](https://github.com/jito-labs/mev-bot)
- [Drift JIT Liquidity Mechanism](https://www.drift.trade/updates/jit-liquidity-mechanism)
- [Introducing BAM - bam.dev](https://bam.dev/blog/introducing-bam/)
- [BAM: Solana's Block Builder Era - Helius](https://www.helius.dev/blog/block-assembly-marketplace-bam)
- [Solana Fees Documentation](https://solana.com/docs/core/fees)
- [Priority Fees - Helius](https://www.helius.dev/blog/priority-fees-understanding-solanas-transaction-fee-mechanics)
- [MEV on Solana - QuickNode](https://www.quicknode.com/guides/solana-development/defi/mev-on-solana)
- [Solana MEV Bot $1.8M Backrun - The Block](https://www.theblock.co/post/272079/solana-based-mev-bot-earns-1-8-million-after-back-running-memecoin-trader-in-seconds)
