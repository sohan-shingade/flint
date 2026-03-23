# MEV Testing Infrastructure: Cost, Opportunity, and Market Analysis

## Executive Summary

No production-grade MEV backtesting platform exists for Solana. Ethereum has fragmented tools (Flashbots deprecated `mev-inspect-py`), but Solana has **zero**. This is a $720M/year MEV market with 17,700+ developers and no research tooling. Building MEV testing infrastructure for Flint would make it first-to-market in a clear gap.

---

## Part 1: Infrastructure Costs

### What You Need to Build MEV Testing

Building production-grade MEV backtesting requires four layers: data ingestion, storage, simulation, and analysis. Here's what each costs.

### Layer 1: Real-Time Data Ingestion

You need real-time access to Solana state changes to capture MEV opportunities as they happen (for live monitoring) and to record them for replay (backtesting).

| Component | Option | Monthly Cost | Notes |
|---|---|---|---|
| **RPC Provider** | Helius Free | $0 | 1M credits, 10 req/s — prototyping only |
| | Helius Developer | $49 | 50 req/s, chat support |
| | Helius Business | $499 | 200 req/s, enhanced WebSockets |
| | Helius Professional | $999 | 500 req/s, LaserStream gRPC |
| | QuickNode Build | $49 | 1.5x Solana multiplier |
| | QuickNode Scale | $299 | Higher throughput |
| **gRPC Streaming** | Chainstack (1 stream) | $49 | Basic Yellowstone gRPC |
| | Triton Dedicated | ~$2,900 | Full Yellowstone, Dragon's Mouth |
| | Chainstack Dedicated | ~$3,577 | Per-hour billing |
| **Jito Bundle API** | Jito Block Engine | $0 | Free access (challenge-response auth) |
| | Jito tip data | $0 | Free via explorer.jito.wtf |

### Layer 2: Historical Data Sources

For backtesting, you need historical state to replay against.

| Source | Cost | What You Get |
|---|---|---|
| **Drift Data API** | Free | OHLCV, funding, orderbook for 48 markets |
| **Drift S3** | Free | Raw trade records (archival) |
| **Birdeye** | Free tier (30K CU/month) | Token prices, trade data |
| | Paid tiers | Contact sales — not publicly listed |
| **Helius Enhanced API** | Included in plan | Parsed transactions, webhooks (70+ tx types) |
| **Pyth Oracle** | ~$0.001/update | 500+ price feeds |
| **Google BigQuery** | Pay-per-query | Full Solana block data — simple queries can cost $100s |
| **Dune Analytics** | $0-$999/month | SQL queries, 1-60 min data lag |
| **Flipside Crypto** | Free-Enterprise | SQL queries, ~15 min delay, Snowflake |
| **Jito Explorer API** | Free | 36B+ classified transactions since Jan 2022 |

### Layer 3: Storage

MEV data is large. Pool state, orderbook snapshots, and transaction logs add up fast.

| Database | Monthly Cost | Best For |
|---|---|---|
| **DuckDB** | $0 (self-hosted) | Analytical queries, backtesting (3.5x faster than TimescaleDB) |
| **ClickHouse Cloud** | ~$25-50/TB | Cold analytics, fast ingestion (4.8x faster loading) |
| **TimescaleDB Cloud** | ~$0.87/GB | Hot time-series, real-time ingestion, PostgreSQL compat |
| **S3/GCS** | ~$23/TB | Raw archive storage (Parquet files) |

**Storage estimates for 1 year of MEV-relevant data:**

| Data Type | Estimated Size | Storage Cost (S3) |
|---|---|---|
| OHLCV candles (48 markets, 1m resolution) | ~50 GB | ~$1/month |
| Orderbook snapshots (5-min intervals, 48 markets) | ~500 GB | ~$12/month |
| DEX pool state (targeted: Raydium, Orca, Phoenix) | 5-20 TB | $115-460/month |
| Oracle prices (Pyth, 1s updates, 20 feeds) | ~200 GB | ~$5/month |
| Full Solana block data (archive) | 400+ TB | ~$9,200/month |
| **Practical MEV dataset (targeted)** | **~5-10 TB** | **~$115-230/month** |

### Layer 4: Compute

For simulation and backtesting at scale.

| Component | Option | Monthly Cost |
|---|---|---|
| **Dev machine** | Local (existing hardware) | $0 |
| **Dedicated RPC node** | Bare metal (256GB RAM, NVMe) | $818-982 |
| | RPCFast dedicated | $1,800 |
| | Triton dedicated | ~$2,900 |
| **Validator node** | Bare metal + voting costs | ~$5,000 |
| | Annual voting (1 SOL/day) | ~$5,500/month (at $195/SOL) |
| **Cloud compute** | AWS r7a.8xlarge (256GB) | ~$4,000-8,000 |
| **Co-location** | Equinix rack | $650-900 |

---

## Cost Summary: Three Tiers

### Tier 1: MVP / Research Tool
*Ship a backtesting product that works for researchers and strategy developers.*

| Item | Monthly Cost |
|---|---|
| Helius Developer (RPC + webhooks) | $49 |
| DuckDB (local storage) | $0 |
| Drift Data API (candles, funding, OI) | $0 |
| Jito Explorer API (historical tips/bundles) | $0 |
| Pyth oracle data | ~$5 |
| S3 storage for historical data (~1 TB) | ~$23 |
| **Total** | **~$77/month** |

What you get: Backtest arb strategies against historical Drift data with realistic fills. Monitor current MEV opportunities. Replay funding rate dislocations. No live execution.

### Tier 2: Competitive Product
*Full MEV backtesting with DEX pool state replay and live monitoring.*

| Item | Monthly Cost |
|---|---|
| Helius Professional (LaserStream gRPC) | $999 |
| Chainstack Yellowstone gRPC | $49 |
| DuckDB + ClickHouse Cloud (5 TB) | ~$150 |
| Birdeye paid tier (token data) | ~$200 (est.) |
| Dedicated RPC node (bare metal) | $900 |
| S3 storage (10 TB archive) | $230 |
| Dune Analytics Plus (SQL queries) | $399 |
| **Total** | **~$2,927/month (~$35K/year)** |

What you get: Full DEX pool state replay for Raydium/Orca/Phoenix. Live gRPC streaming for real-time opportunity detection. Historical MEV analytics. Paper trading for MEV strategies.

### Tier 3: Institutional Grade
*Co-located infrastructure with sub-millisecond latency and full archive.*

| Item | Monthly Cost |
|---|---|
| Helius Enterprise | ~$2,000+ (custom) |
| Dedicated validator node + voting | ~$5,500 |
| Co-located bare metal (Equinix) | $900 |
| Triton dedicated (Yellowstone gRPC) | $2,900 |
| ClickHouse Cloud (20 TB) | ~$600 |
| S3 archive (100 TB) | $2,300 |
| Jito BAM node (TEE enclave) | TBD |
| Staff/ops overhead | $5,000+ |
| **Total** | **~$19,200/month (~$230K/year)** |

What you get: Everything in Tier 2 plus live MEV execution, co-located hardware, full Solana archive replay, BAM integration, and enterprise SLAs.

---

## Part 2: Market Opportunity

### The Numbers

| Metric | Value | Source |
|---|---|---|
| Solana MEV revenue (2025) | **$720 million** | Helius, multiple confirmations |
| Solana DEX volume (2025) | **$1.5-1.95 trillion** | Solana Floor, CoinPedia |
| Jito tips (2025) | 5.8M SOL (~$1.1B) | Jito Labs |
| Jito bundles processed (2025) | ~6 billion | Jito Labs |
| Jito tips as % of Solana revenue | 42-66% | QuickNode, Helius |
| Solana active developers | 17,708 | Electric Capital |
| Solana developer growth (YoY) | +29% | Electric Capital |
| Crypto trading bot market (2024) | $1.4B | Business Research Insights |
| Crypto trading bot market (2033 est.) | $4.8B (15.5% CAGR) | Business Research Insights |

### Is It Profitable?

**For MEV searchers:** Yes, but extremely competitive.
- Average arb profit: $1.58/transaction
- 90M+ successful arb transactions in a year
- Top searchers earn millions (one bot made $1.8M from a single backrun)
- 50%+ of Solana transactions are failed arb attempts (the losers)
- Infrastructure costs ($2K-50K/month) create a high bar

**For an MEV tooling platform:** The economics are much more favorable.
- You don't need to compete in latency races
- You sell picks and shovels to the miners
- Recurring SaaS revenue vs. probabilistic trading profits
- Lower infrastructure requirements than running an actual MEV bot

### Who Would Pay For This?

#### Segment 1: Crypto Quant Funds (~100 globally)
- Average AUM: ~$132M
- 55% of traditional hedge funds now invest in digital assets
- **What they need:** Backtesting against historical state, strategy simulation, risk analytics
- **What they'd pay:** $500-5,000/month (tooling is a profit center, not a cost center)
- **Why they'd pay:** Building custom MEV backtesting infrastructure costs 3-6 engineer-months. A platform saves that.

#### Segment 2: Proprietary Trading Firms
- Industry valued at ~$20B globally
- **What they need:** Low-latency data, DEX simulation, execution analytics
- **What they'd pay:** $1,000-10,000/month
- **Why they'd pay:** Time-to-market. They want to deploy strategies, not build infrastructure.

#### Segment 3: Solo Algorithmic Traders
- 245K+ on QuantConnect, 45K+ on Freqtrade
- **What they need:** Affordable backtesting, strategy templates, paper trading
- **What they'd pay:** $49-199/month
- **Why they'd pay:** Can't afford to build their own infrastructure. Need a platform that works.

#### Segment 4: MEV Researchers / Academics
- Growing field — MEV is a hot research topic
- **What they need:** Historical data access, analytics dashboards, reproducible analysis
- **What they'd pay:** $0-49/month (free tier important for adoption)
- **Why they'd pay:** No tools exist. They're currently scraping data manually.

#### Segment 5: Protocol Teams (Drift, Raydium, Jupiter)
- Need to understand MEV impact on their users
- **What they need:** MEV attribution, user impact analysis, mitigation testing
- **What they'd pay:** Enterprise contracts ($5,000-20,000/month)
- **Why they'd pay:** Regulatory pressure, user protection, protocol optimization

### Pricing Sweet Spot

| Tier | Price | Target |
|---|---|---|
| Free | $0 | Researchers, students, evaluation |
| Indie | $49/month | Solo traders, prototyping |
| Pro | $199/month | Serious algo traders, small funds |
| Team | $499/month | Trading desks, small quant firms |
| Enterprise | Custom ($2K+/month) | Hedge funds, protocol teams |

For context:
- Freqtrade: Free (open source)
- QuantConnect: $10-40/month
- Bloomberg Terminal: $2,665/month
- Dedicated Solana RPC: $1,000-3,000/month

A $199/month MEV backtesting product sits in a **wide-open gap** between free open-source tools and Bloomberg-tier infrastructure.

---

## Part 3: Competitive Landscape

### Does Anyone Offer Solana MEV Backtesting?

**No.**

| Tool | Chain | MEV Backtesting | MEV Analytics |
|---|---|---|---|
| Flashbots (mev-inspect) | Ethereum | Deprecated | Dashboard only |
| Jito Explorer | Solana | No | Dashboard only |
| EigenPhi | Ethereum/BSC | No | Deep analysis (paid) |
| Artemis (Paradigm) | Ethereum | No | Bot framework |
| libMEV | Ethereum | No | Dashboard |
| **Solana MEV backtesting** | **Solana** | **Nothing exists** | **Nothing exists** |

Flint would be **first-to-market** in Solana MEV backtesting. The closest Ethereum equivalent (Flashbots mev-inspect-py) was deprecated, leaving even Ethereum underserved.

### Flint's Existing Advantages

- Already has a backtest engine with realistic fills, fees, and multi-market support
- Already has Drift integration (JIT auctions are a natural MEV venue)
- Already has 13 data providers including cross-venue funding
- Already has 497 tests and a production web UI
- Python-first approach aligns with the research/backtesting use case
- Local-first DuckDB storage means no cloud lock-in

---

## Part 4: Customer Journey

### Stage 1: Discovery
**Trigger:** Developer searching "Solana MEV backtesting", "Solana arb bot tutorial", or "Drift JIT strategy"
- Finds Flint on GitHub (README, MEV guide)
- Sees it's the only platform offering MEV backtesting for Solana
- **Emotion:** Curiosity, skepticism ("does this actually work?")
- **Action:** Stars repo, reads docs

### Stage 2: First Use (Free Tier)
**Trigger:** `pip install -e . && flint init && flint serve`
- Runs a sample backtest in < 2 minutes
- Sees the web UI with charts and results
- Tries the MEV monitoring dashboard
- **Emotion:** Surprise ("this actually works"), excitement
- **Critical metric:** Time to first backtest result (target: < 5 minutes)

### Stage 3: Strategy Development
**Trigger:** User writes their first custom strategy
- Uses Strategy Lab with Monaco editor
- Tests against historical Drift data
- Explores cross-venue funding dislocations
- Tries multi-market arb simulation
- **Emotion:** Engagement, investment in the platform
- **Friction points:** Missing data for specific markets, API limitations

### Stage 4: Evaluation
**Trigger:** Strategy shows promise, user considers going live
- Compares paper trading results to expectations
- Evaluates data quality and latency
- Assesses whether to build custom infra or continue using Flint
- **Decision:** Is Flint good enough, or do I need to build my own?
- **Conversion factor:** If Flint handles 80%+ of their needs, they stay. If < 60%, they leave.

### Stage 5: Paid Conversion
**Trigger:** Needs exceed free tier (more data, faster updates, API access)
- Upgrades to Pro ($199/month)
- Gets historical DEX pool state replay
- Gets priority fee analytics and Jito tip data
- **Emotion:** Justified investment ("this saves me weeks of infra work")

### Stage 6: Expansion / Lock-in
**Trigger:** Strategy is profitable, user adds more strategies
- Moves to Team tier ($499/month)
- Invites team members
- Builds custom providers using Flint's DataProvider interface
- Strategies depend on Flint's data format and APIs
- **Switching cost:** High — rewriting strategies for another platform is expensive

### Stage 7: Advocacy
**Trigger:** User recommends Flint to peers
- Writes a blog post or tweet about results
- Submits a PR or custom provider
- Refers colleagues at other funds
- **Catalyst:** Profitable strategy that wouldn't have been possible without Flint's MEV testing

---

## Part 5: Build vs. Don't Build

### Arguments FOR Building MEV Infrastructure

1. **First-mover in a clear gap** — no one else offers Solana MEV backtesting
2. **$720M/yr market** — even tiny capture = significant revenue
3. **Flint already has 80% of the foundation** — backtest engine, Drift integration, data providers, web UI
4. **MVP is cheap** — Tier 1 costs ~$77/month in infrastructure
5. **Natural extension** — MEV testing is the logical next step after backtesting
6. **Developer community is growing** — 17,700 Solana devs, +29% YoY
7. **Picks and shovels model** — more stable than running MEV bots yourself

### Arguments AGAINST

1. **Niche audience** — MEV researchers/developers are a subset of a subset
2. **Data complexity** — full DEX pool state replay is engineering-heavy
3. **Rapidly changing landscape** — Jito BAM changes assumptions quarterly
4. **Free tier expectations** — open-source trading tools train users to expect free
5. **Support burden** — MEV users are sophisticated and demanding

### Recommendation

**Build it incrementally:**

1. **Phase 1 (now):** MEV monitoring dashboard + arb detection using existing data ($77/month infra)
2. **Phase 2:** Historical MEV replay using Drift data + Jito tip analytics ($500/month infra)
3. **Phase 3:** DEX pool state simulation for Raydium/Orca ($3K/month infra)
4. **Phase 4:** Live MEV paper trading + Python SDK for bundle prototyping ($5K/month infra)
5. **Phase 5:** Enterprise tier with co-located infrastructure ($20K+/month infra)

Each phase is independently valuable and can be shipped as a product increment. Don't build Tier 3 infrastructure before validating demand at Tier 1.

---

## Total Cost Summary

| Phase | Monthly Infra | Annual Cost | What It Delivers |
|---|---|---|---|
| Phase 1: MVP | ~$77 | ~$924 | MEV monitoring, arb detection, basic backtesting |
| Phase 2: Research | ~$500 | ~$6,000 | Historical MEV replay, Jito analytics, funding arb |
| Phase 3: Simulation | ~$3,000 | ~$36,000 | DEX pool state replay, route profitability |
| Phase 4: Paper Trading | ~$5,000 | ~$60,000 | Live MEV paper trading, Python bundle SDK |
| Phase 5: Enterprise | ~$20,000 | ~$240,000 | Co-located, full archive, BAM integration |

**Total for full buildout: ~$28,577/month (~$343K/year)**

But you don't build it all at once. Phase 1 costs less than a Helius Developer plan. Phase 2 costs less than a single QuantConnect Professional subscription. Each phase validates demand before you invest in the next.

---

## Bottom Line

The opportunity is real: $720M/year MEV market, zero backtesting tools, 17,700 developers, and Flint already has the backtest engine. The question isn't whether to build it — it's how fast to move.

Start with Phase 1 ($77/month). Ship the MEV monitoring dashboard. See who shows up. Then iterate.
