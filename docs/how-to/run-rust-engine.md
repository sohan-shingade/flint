# How to: Enable the Rust engine

10–50× faster backtests with identical semantics. Auto-used when installed.

## Install

```bash
pip install maturin
cd rust && maturin develop
```

Requires a Rust toolchain (`rustup`). Initial build takes ~2 min.

## Verify

```bash
python -c "from flint_core import RustEngine; print('OK')"
```

If the import works, backtests dispatch to Rust automatically. The Python engine remains as fallback.

## Parity check

```bash
pytest tests/test_rust_parity_benchmark.py -v -s
```

Runs identical backtests through both engines and asserts agreement on metrics + trade list. CI runs this on every PR.

## When to use

Always, if installed. There's no behavioral difference — it's pure speedup.

## When not to use

- **Editing fill model or margin engine code.** The Rust port mirrors the Python — you need to update both. Work in Python first, port once stable.
- **Debugging a strategy.** Python's error output is more useful than PyO3 tracebacks.

## Disable

Uninstall `flint_core` or set `FLINT_USE_RUST=0` (if exposed by your version).

```bash
pip uninstall flint_core
```

## Rebuild after changes

```bash
cd rust && maturin develop          # dev build
cd rust && maturin develop --release # optimized
```

## Gotchas

- **Release build is 3–5× faster than dev.** Don't benchmark on dev builds.
- **First call per process re-initializes Tokio runtime** — budget ~100ms overhead.
- **macOS Apple Silicon** builds fine; **Linux ARM** needs a recent rustc.

## Related

- [concepts/architecture.md#rust-engine](../concepts/architecture.md#rust-engine)
- `tests/test_rust_parity_benchmark.py` — parity definition
