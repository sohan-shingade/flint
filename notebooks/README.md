# Proof notebooks

Phase 1 T1.5. Each notebook proves a flagship strategy end-to-end:

1. Pulls candles from the local store (errors if data is missing — no silent network).
2. Pins source-file SHA-256 hashes for reproducibility.
3. Runs backtest with fixed seed.
4. Runs paper replay over the same data.
5. Computes parity deltas; fails if PnL divergence > 2%.
6. Emits a markdown report alongside inline metrics.

**Runnable as both scripts and notebooks.** Files use the [jupytext](https://jupytext.readthedocs.io/) `# %%` cell convention — open in VS Code or Jupyter to see cells, or run directly with `python`.

```bash
# As a script:
python notebooks/funding_arb.py

# Or open in Jupyter (via jupytext):
jupyter notebook notebooks/funding_arb.py
```

## Contents

| Notebook | Strategy | Market | Proves |
|---|---|---|---|
| `funding_arb.py` | `FundingArbStrategy` | SOL-PERP | Cross-venue funding dislocation capture |
| `basis_trade.py` | `BasisTradeStrategy` | SOL-PERP | Perp-vs-spot basis capture |
| `momentum_breakout.py` | `MomentumBreakoutStrategy` | SOL-PERP | Directional momentum baseline |
| `multi_venue_funding_arb.py` | (synthetic) | SOL-PERP | D-2.1.d structural correctness — same-market opposing legs on Drift+HL with per-venue funding ledgers (no spillover, exact per-leg attribution) |

## Prerequisites

```bash
flint init                    # download sample data + run demo backtest
flint data download --market SOL-PERP --days 90
```

Each notebook fails-fast if required data isn't present.

## Output

Running a notebook:
- Prints a summary to stdout.
- Emits a markdown report at `artifacts/parity/{strategy}-{market}-{date}.md`.
- Exits non-zero if parity thresholds are breached (CI-gate-ready).

See `docs/specs/phase-1-trust-correctness.md#15-proof-notebooks` for the spec.
