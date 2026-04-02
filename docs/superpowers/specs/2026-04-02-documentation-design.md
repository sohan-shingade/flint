# Documentation — Design Spec

> Sub-project 5.2 of Phase 5 (ROADMAP.md §5.2)
> Date: 2026-04-02

## Overview

Write 6 comprehensive user-facing guides in `docs/guides/`. No code changes — pure documentation. Each guide is self-contained for someone who's never seen the codebase.

### Files

| File | Purpose |
|------|---------|
| `docs/guides/quickstart.md` | Install → pull data → first backtest in <5 min |
| `docs/guides/strategy-authoring.md` | ExecutionContext API, v1/v2, multi-market, Optuna |
| `docs/guides/data-providers.md` | 14 providers, API keys, custom providers |
| `docs/guides/live-deployment.md` | Wallet setup, RPC, risk config, devnet → mainnet |
| `docs/guides/architecture.md` | Fill pipeline, margin engine, data flow, extension points |
| `docs/guides/slippage-models.md` | 4-tier impact model, vAMM math, calibration equations |

### Content Guidelines

- Written for a developer who knows Python but nothing about Flint
- Code examples use actual CLI commands and Python code that works
- No internal jargon without explanation
- Each guide stands alone (no required reading order, but quickstart first is recommended)
- Reference CLAUDE.md for accurate API endpoints, CLI commands, and config fields
