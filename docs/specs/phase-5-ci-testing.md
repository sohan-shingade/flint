# Phase 5 — CI & Testing Hardening

**Owner:** TBD
**Duration:** 2-3 weeks (runs parallel to Phases 1-4)
**Blocks:** Phase 1 artifacts (§1.2, §1.4 need CI gates); long-term regression confidence

Current CI is Linux-only, Py 3.11 only, no Rust build, swallows install errors, lint is `python -c "import flint"`. Test suite is broad but shallow in critical paths.

---

## Items

- [5.1 Matrix and macOS](#51-matrix-and-macos)
- [5.2 Rust CI job](#52-rust-ci-job)
- [5.3 Parity report CI gate](#53-parity-report-ci-gate)
- [5.4 Sandbox escape tests](#54-sandbox-escape-tests)
- [5.5 Test count anti-drift](#55-test-count-anti-drift)
- [5.6 Live-venue smoke tests](#56-live-venue-smoke-tests)

---

## 5.1 Matrix and macOS

**Problem:** `.github/workflows/ci.yml` runs on `ubuntu-latest` + `python-version: "3.11"` only. Masks platform-specific bugs. `|| pip install -e .` fallback swallows dependency errors.

### Tasks

**T5.1.a — OS + Python matrix**
```yaml
jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        python-version: ["3.10", "3.11", "3.12"]
    runs-on: ${{ matrix.os }}
```

**T5.1.b — Fix real install path, drop `|| pip install -e .` fallback**
- Audit current fallback: `pip install -e ".[dev,hyperliquid]" solders 2>/dev/null || pip install -e ".[dev]" solders 2>/dev/null || pip install -e .`
- Three levels of silent failure. Replace with a deterministic install + explicit error if optional deps fail.

**T5.1.c — Test timeouts**
- `pytest --timeout=300 tests/` — add `pytest-timeout` to `dev` extras.
- Catches polling-loop tests that hang.

**T5.1.d — Real linting**
- Add `ruff check flint/ tests/` and `ruff format --check flint/ tests/`.
- Replace the `python -c "import flint"` import check with proper lint.

**T5.1.e — Type check**
- Add `mypy flint/ --strict-optional` (not full strict; reduce gradually).

### Acceptance

- CI green on Linux + macOS × Py 3.10/3.11/3.12.
- Install errors surface — no more silent fallback.
- Ruff + mypy enforced.

### Effort

~1-2 days.

---

## 5.2 Rust CI job

**Problem:** Rust engine not built in CI. `tests/test_rust_parity_benchmark.py` skips if `flint_core` not present → always passes.

### Tasks

**T5.2.a — Rust build job**
```yaml
rust-build:
  strategy:
    matrix:
      os: [ubuntu-latest, macos-latest]
  runs-on: ${{ matrix.os }}
  steps:
    - uses: actions/checkout@v4
    - uses: dtolnay/rust-toolchain@stable
    - uses: actions/setup-python@v5
      with: { python-version: "3.11" }
    - name: Install maturin
      run: pip install maturin
    - name: Build Rust engine
      working-directory: rust
      run: maturin develop --release
    - name: Run Rust tests
      working-directory: rust
      run: cargo test --release
    - name: Run Rust parity tests
      run: pytest tests/test_rust_python_parity.py tests/test_rust_orderbook_parity.py tests/test_rust_fee_parity.py -v
```

**T5.2.b — Remove "tested separately" stub**
- `tests/test_rust_parity_benchmark.py:100-104` — remove the stub comment, make it a real end-to-end parity assertion.

**T5.2.c — Prebuilt wheel path**
- Stretch goal: `maturin build --release` + publish wheels on release for common platforms so users don't need Rust toolchain.

### Acceptance

- Rust job green on Linux + macOS.
- Parity tests actually run, not skipped.
- If Rust build fails, main CI still reports the failure (no silent skip).

### Effort

~2 days.

---

## 5.3 Parity report CI gate

**Depends on:** Phase 1.2.

### Tasks

**T5.3.a — Parity job**
```yaml
parity:
  needs: [test]
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with: { python-version: "3.11" }
    - run: pip install -e ".[dev]"
    - run: flint init --non-interactive
    - name: Run parity report
      run: python scripts/run_parity_report.py funding_momentum_v4 SOL-PERP 30
    - name: Verify thresholds
      run: python scripts/verify_parity_thresholds.py artifacts/parity/funding_momentum_v4-SOL-PERP-*.md
    - name: Upload artifact
      uses: actions/upload-artifact@v4
      with:
        name: parity-report
        path: artifacts/parity/
```

**T5.3.b — Thresholds**
- PnL divergence > 2% of initial → fail
- Fill-time MAE > 60s → fail
- Per-trade notional residual p95 > 5 bps → fail

**T5.3.c — Nightly extended run**
- Weekly scheduled run with all flagship strategies (not just the reference one).

### Acceptance

- Every main-branch push produces a parity artifact.
- Breaking the thresholds fails the build.

### Effort

~1 day (on top of Phase 1.2).

---

## 5.4 Sandbox escape tests

**Depends on:** Phase 2.4.

### Tasks

**T5.4.a — Escape attempt suite**
- `tests/test_sandbox_escape.py` with cases:
  1. `import os; os.system('ls')` → must be rejected at load
  2. `import subprocess; subprocess.Popen(['ls'])` → rejected
  3. `open('/etc/passwd')` → rejected (no `builtins.open` in whitelist)
  4. `eval('1+1')` → rejected
  5. `exec('print(1)')` → rejected
  6. `__import__('os')` → rejected
  7. `getattr(__builtins__, 'open')` → rejected
  8. Infinite loop (`while True: pass` in `on_candle`) → `StrategyTimeoutError` within 6 min
  9. Memory bomb (`np.zeros(10**10)`) → `StrategyMemoryError`
  10. File write (`pathlib.Path('/tmp/x').write_text('y')`) → rejected
  11. Socket open (`import socket; s = socket.socket()`) → rejected
  12. Nested-import escape (`import numpy; numpy.__import__('os')`) → rejected

**T5.4.b — Fuzz strategy**
- Generate random strategy code with hostile payloads; assert loader rejects.

### Acceptance

- All 12 escape attempts rejected.
- Fuzz run produces 1000 hostile strategies; 100% rejected.

### Effort

~1-2 days. Depends on Phase 2.4 landing.

---

## 5.5 Test count anti-drift

**Problem:** README says "676 tests"; actual is ~1.6k test functions across 153 files. Manual counts go stale.

### Tasks

**T5.5.a — Auto-count in `scripts/build_docs.py`**
- Collect via `pytest --collect-only -q | tail -1` (produces "N tests collected").
- Inject into README between `<!-- counts:auto -->` markers.

**T5.5.b — CI check**
- If README drifts from current count, build fails.

### Acceptance

- README count regenerates on every `python scripts/build_docs.py`.
- Drift blocks the build.

### Effort

~1 hour.

---

## 5.6 Live-venue smoke tests

**Problem:** live trading tests are 100% mocked. No confidence that live order submission actually works end-to-end.

### Tasks

**T5.6.a — Smoke test harness**
- New: `tests/integration/test_live_smoke.py` (marked `@pytest.mark.integration`).
- Gated: runs only when `FLINT_LIVE_SMOKE=1` and venue credentials in env.
- Tests:
  - Drift devnet: place + cancel a $0.01 SOL-PERP limit order; assert order lifecycle events.
  - HL testnet: same.

**T5.6.b — Manual-trigger workflow**
- `.github/workflows/live-smoke.yml` with `workflow_dispatch` only (never on push).
- Uses repo secrets `DRIFT_DEVNET_KEYPAIR`, `HL_TESTNET_KEY`.

**T5.6.c — Guard rails**
- Hard caps: max 1 order per test, max 0.01 SOL notional, auto-cancel after 10s.
- Test fails fast if balances above $10 (we're not funding it beyond smoke).

### Acceptance

- Manually triggered workflow places + cancels orders on devnet/testnet.
- No real-mainnet code path reachable from this suite.

### Effort

~2-3 days.

---

## Dependencies

```
5.1 ── independent
5.2 ── independent
5.3 ── needs 1.2
5.4 ── needs 2.4
5.5 ── independent
5.6 ── independent (but needs live code paths from Phase 6.5 to cover live UI)
```

Start 5.1 + 5.2 + 5.5 on Day 1. 5.6 any time.

---

## Exit criteria (Phase 5 complete)

1. CI matrix: 2 OS × 3 Py versions green.
2. Rust build + parity runs on CI on every push.
3. Parity report CI gate blocks regressions.
4. Sandbox escape suite runs on every push.
5. Test counts auto-regenerate in README.
6. Manual-trigger smoke test harness works on Drift devnet + HL testnet.
