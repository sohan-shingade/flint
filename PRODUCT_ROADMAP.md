# Flint Product Roadmap

> Product-focused roadmap for turning Flint from a strong engineering project into a trustworthy, focused product.
> Based on external product review (April 2026). Complements the technical ROADMAP.md.
> Last updated: 2026-04-06

## Thesis

Flint is technically ambitious but over-scoped as a product. The path forward is not removing features — it's **narrowing the story, earning trust, and proving one workflow end to end** before expanding.

**Target positioning**: The best local backtester and paper-trading lab for Drift + Hyperliquid perp strategies.

Not a general crypto platform. Not an MEV lab. Not an AI trading OS. A **focused, trustworthy, local-first research tool for Solana/Hyperliquid perp traders**.

---

## Phase A: Repo Hygiene & Trust Signals (Week 1)

**Goal**: Remove obvious credibility leaks. Anyone who inspects the repo should see careful engineering, not a personal workstation snapshot.

**Priority**: Critical — these are zero-effort trust destroyers.

### A.1 Remove .venv from git

- [ ] Add `.venv/` to `.gitignore`
- [ ] `git rm -r --cached .venv` (4,421 tracked files)
- [ ] Force-push or rebase to remove from history (optional but recommended — repo size)
- [ ] Verify `.venv` no longer appears on GitHub

### A.2 Fix version mismatch

- [ ] Decide canonical version: either bump `pyproject.toml` to `1.0.0` or retag GitHub release as `0.2.0`
- [ ] Recommendation: use `0.x` until live trading is validated. Retag release as `0.3.0` and set pyproject.toml to match
- [ ] Add version consistency check to CI (pyproject.toml version == git tag)

### A.3 Fix the installer

- [ ] Remove the silent `lsof -ti:8000 | xargs kill` (line 199 of install.sh)
- [ ] Replace with: check if port 8000 is in use → print what's using it → ask user to free it or pick a different port
- [ ] Add `--port` flag to `flint serve` and the installer
- [ ] Consider adding a `pip install flint-trading` path as an alternative to `curl | bash`
- [ ] Document what the installer does *before* running it (in README, not just in the script)

### A.4 General repo cleanup

- [ ] Audit `.gitignore` for other files that shouldn't be tracked (`.env`, `__pycache__`, `.DS_Store`, `*.egg-info`)
- [ ] Ensure no secrets or API keys are anywhere in git history
- [ ] Pin all CI dependencies

### Phase A Deliverables

1. Clean repo that passes the "stranger inspection" test
2. Consistent versioning
3. Installer that respects the user's machine

---

## Phase B: Narrow the Story (Week 1-2)

**Goal**: Make it immediately obvious what Flint is and who it's for. One sentence, one buyer persona, one killer workflow.

### B.1 Rewrite the README

- [ ] **New opening line**: "Flint is a local backtesting and paper-trading lab for Drift and Hyperliquid perp strategies. Write a strategy in Python, backtest it with realistic fills, and paper trade with live prices. Everything runs on your machine."
- [ ] **Remove or demote** from the first screenful:
  - MCP server / AI integration (move to a "For AI developers" section at the bottom)
  - MEV research (move to "Advanced" section)
  - 10-venue funding comparison (mention Drift + Hyperliquid; link to full list in docs)
  - Cross-venue arb (move to "Advanced strategies" section)
- [ ] **Lead with the core loop**: install → download data → write strategy → backtest → see results → paper trade
- [ ] **Rewrite "Why Flint?" section**: Replace the 13-row comparison matrix with 3-4 bullets that explain what's different in plain English. Save the matrix for docs.
- [ ] **Add a "Limitations" section**: Single-machine only, no cloud, DuckDB single-writer, local-first by design. Frame as intentional, not missing.

### B.2 Sharpen the tagline

- [ ] Current: "Algorithmic trading, backtesting, and MEV research for Solana"
- [ ] Proposed: "Backtest and paper trade Solana perp strategies. Local-first, free data."
- [ ] Update: GitHub repo description, README header, `pyproject.toml` description, docs site (if any)

### B.3 Restructure documentation hierarchy

- [ ] **Tier 1 (README)**: What it is, install, first backtest, first paper trade
- [ ] **Tier 2 (docs/)**: Strategy authoring, data providers, optimization, deployment
- [ ] **Tier 3 (docs/advanced/)**: Cross-venue strategies, MEV, MCP server, CLMM models, vAMM math, calibration
- [ ] Everything still exists — it's just not in someone's face on first visit

### Phase B Deliverables

1. README that makes one buyer say "this is for me" within 10 seconds
2. Clear documentation hierarchy: core → intermediate → advanced
3. Honest scope framing

---

## Phase C: Prove the Core Workflow (Weeks 2-4)

**Goal**: Publish undeniable evidence that the backtester works, the fill models are reasonable, and paper trading behaves like backtesting predicted. This is where trust is earned.

### C.1 Canonical strategy with reproducible results

- [ ] Pick ONE strategy (suggest: momentum breakout on SOL-PERP, Drift data)
- [ ] Publish:
  - The strategy code (already in repo)
  - Exact data range and download command
  - Backtest command or API call
  - Full results: equity curve, trade log, metrics
  - Expected output (approximate, since data may update)
- [ ] Put this in README as "Try it yourself" section
- [ ] Ensure a new user can reproduce these results within 5 minutes of installing

### C.2 Fill model validation report

- [ ] Compare the 4 fill model tiers (vAMM, orderbook, sqrt-impact, close-price) on the same strategy and data
- [ ] Show: how much PnL differs across models, which is most conservative, which matches real fills best
- [ ] Publish as a doc or notebook in the repo
- [ ] This directly addresses the "serious claims without proof" critique

### C.3 Backtest vs. paper trade parity report

- [ ] Run the canonical strategy through both engines on the same time window
- [ ] Publish the parity report (already have `ParityTest` class)
- [ ] Show: PnL divergence, fill price MAE, equity correlation
- [ ] Be honest about where divergence exists and why

### C.4 Known edge cases and failure modes

- [ ] Document what the backtester does NOT model well:
  - Slippage on large orders in illiquid markets
  - Exact funding timing vs. bar boundaries
  - Network latency and tx failure in live mode
  - Liquidation detection granularity (per-bar, not per-tick)
- [ ] Put this alongside the validation reports — honesty builds trust faster than perfection

### Phase C Deliverables

1. One reproducible end-to-end example anyone can run
2. Fill model comparison with real data
3. Parity report showing backtest ≈ paper trade
4. Honest documentation of known limitations

---

## Phase D: Installer & Onboarding Polish (Weeks 3-4)

**Goal**: A new user goes from zero to their first backtest result in under 5 minutes, feeling in control the whole time.

### D.1 Safe installer

- [ ] Rewrite `install.sh` with explicit opt-in for each step:
  - Check Python version → if missing, suggest install command (don't run Homebrew automatically)
  - Check Node version → if missing, suggest install command
  - Clone repo → show destination path, ask to proceed
  - Install deps → `pip install -e .`
  - Build UI → `cd ui && npm install && npm run build`
  - Start server → `flint serve`
- [ ] Add a `--non-interactive` flag that does everything without prompts (for CI/advanced users)
- [ ] Never kill processes the user didn't ask to kill

### D.2 PyPI package

- [ ] Publish `flint-trading` (or similar) to PyPI so users can `pip install` instead of cloning
- [ ] `flint init` should still work for downloading sample data
- [ ] This addresses the "reproducibility and control" critique — pip is a known quantity

### D.3 Setup wizard improvements

- [ ] The setup wizard is a good idea — just make sure it works without the invasive installer
- [ ] Ensure `flint init` works standalone after `pip install`
- [ ] Clear error messages if dependencies are missing

### Phase D Deliverables

1. Installer that respects user's machine and explains each step
2. `pip install` path as alternative
3. Under-5-minute first backtest experience

---

## Phase E: Live Trading Credibility (Weeks 5-8)

**Goal**: De-emphasize live trading in marketing while quietly making it more trustworthy for early adopters.

### E.1 Devnet-first messaging

- [ ] README should say: "Live trading is available on Drift devnet and Hyperliquid testnet. Mainnet deployment requires explicit configuration."
- [ ] Remove any implication that mainnet live trading is battle-tested
- [ ] Add a "maturity matrix" showing what's production-ready vs. experimental:

| Feature | Status |
|---------|--------|
| Backtesting | Production-ready |
| Paper trading | Production-ready |
| Data providers | Production-ready |
| Optimization | Production-ready |
| Live trading (devnet) | Beta |
| Live trading (mainnet) | Experimental — use at own risk |
| Cross-venue execution | Beta |
| MEV scanning | Experimental |

### E.2 Collect live execution data

- [ ] Run strategies on devnet and collect fill data
- [ ] Use fill data to calibrate impact models (already have `CalibrationEngine`)
- [ ] Publish calibration results as trust artifacts
- [ ] When backtest-to-live parity is within 5% on devnet → that's the proof to publish

### E.3 Safety documentation

- [ ] Document all safety rails in one place: kill switches, rate limits, position limits, dry-run mode
- [ ] Show the safety rails code — open-source trust
- [ ] Add a "what happens when things go wrong" section with specific scenarios

### Phase E Deliverables

1. Honest maturity matrix in README
2. Devnet execution data feeding into calibration
3. Safety documentation that builds confidence

---

## Phase F: Strategic Decisions (Decide by Week 4)

These are decisions, not implementation tasks. They shape everything else.

### F.1 License decision

**Current**: AGPL-3.0

**Options**:
1. **Keep AGPL** — fine for hobbyist/OSS adoption, friction for commercial users
2. **MIT/Apache core + AGPL server** — lets people embed the backtest engine while protecting the platform
3. **MIT everything** — maximum adoption, minimal friction, no copyleft protection
4. **Business Source License (BSL)** — open-source with delayed commercial rights (used by Sentry, CockroachDB)

**Decision criteria**: Who is the primary user? Solo quant hobbyists → AGPL is fine. Funds/prop shops → need permissive core. Decide based on ICP.

### F.2 Versioning policy

- [ ] Adopt semantic versioning strictly
- [ ] `0.x` until live trading is validated in production
- [ ] `1.0` means: backtesting is trustworthy, paper trading works, one venue live-tested on mainnet
- [ ] Automate: CI checks that pyproject.toml version matches git tag on release

### F.3 Scope discipline going forward

**Rule**: No new feature categories (e.g., no "portfolio analytics platform," no "social trading," no "cloud deployment") until the core loop is proven with external users.

**Allowed**: Depth improvements to existing features (better fill models, more data providers, performance).

**Not allowed**: New surface area that requires new trust (new venues, new asset classes, new deployment modes) until existing surface area is validated.

---

## Timeline Summary

| Phase | Scope | Estimate | Focus |
|-------|-------|----------|-------|
| A. Repo Hygiene | .venv, versions, installer | 1 week | Trust |
| B. Narrow Story | README, tagline, docs hierarchy | 1-2 weeks | Positioning |
| C. Prove Core | Reproducible examples, validation reports | 2-3 weeks | Credibility |
| D. Onboarding | Safe installer, PyPI, setup flow | 1-2 weeks | Adoption |
| E. Live Credibility | Devnet data, maturity matrix, safety docs | 3-4 weeks | Trust |
| F. Strategic Decisions | License, versioning, scope policy | Week 4 deadline | Direction |

**Phases A+B are parallel and should start immediately.**
**Phase C is the most important phase — this is where trust is actually earned.**
**Phase F decisions should be made before Phase E work begins.**

---

## Relationship to Technical Roadmap

This product roadmap does NOT replace `ROADMAP.md`. That document covers the engineering work (live Drift execution, Hyperliquid integration, cross-venue strategies, execution fidelity). This document covers the **product and trust work** that makes the engineering work matter to users.

The two roadmaps interact:
- Technical Phase 1 (live Drift) feeds into Product Phase E (live credibility)
- Technical Phase 4 (execution fidelity) feeds into Product Phase C (proof artifacts)
- Product Phase B (narrow story) should inform which technical features get highlighted vs. buried

**Priority order**: Product Phases A-C should happen before or alongside Technical Phases, because without trust and focus, more features don't help.

---

## Success Criteria

How we know this roadmap worked:

1. **The "stranger test"**: A developer finds Flint on GitHub. Within 60 seconds they know what it is, who it's for, and whether it's for them. Within 5 minutes they have their first backtest result.
2. **The "trust test"**: A user considering paper trading can find validation reports, known limitations, and safety documentation without digging through source code.
3. **The "focus test"**: The README mentions ≤3 primary capabilities. Everything else is discoverable but not in-your-face.
4. **The "honesty test"**: No claim in the README lacks a corresponding proof artifact or is honestly labeled as experimental.
