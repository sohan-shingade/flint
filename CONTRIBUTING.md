# Contributing to Flint

Thanks for your interest in contributing to Flint! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/sohan-shingade/flint.git
cd flint
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
flint init
```

Run the UI in dev mode:
```bash
cd ui && npm install && npm run dev
```

## Running Tests

```bash
pytest tests/ -v          # all 536+ tests, fully mocked
pytest tests/ -k "paper"  # filter by keyword
```

All tests use mocks — no network calls, no API keys needed.

## Making Changes

1. **Fork the repo** and create a branch from `main`
2. **Write tests** for any new functionality
3. **Run the test suite** to make sure nothing breaks
4. **Follow existing patterns** — look at how similar code is structured
5. **Keep PRs focused** — one feature or fix per PR

## Code Style

- Python: standard library conventions, type hints where helpful
- No strict formatter enforced — just be consistent with surrounding code
- Strategy code goes in `flint/strategy/`, providers in `flint/providers/`
- All DuckDB access goes through `FlintStore` with `with self._lock:`

## Key Architecture Rules

- **Don't create new DuckDB connections** — use the shared `FlintStore`
- **Don't skip `with self._lock:`** in store methods — DuckDB is not thread-safe
- **Don't access `store._conn` directly** from API routes — add a method to `FlintStore`
- **Strategy loader blocks non-approved imports** — only `flint`, `numpy`, `math`, `statistics`, `collections`, `dataclasses`, `typing`, `enum`, `abc`, `functools`, `itertools`, `operator`

## Adding a Data Provider

1. Create `flint/providers/my_provider.py`
2. Inherit from `DataProvider` in `registry.py`
3. Implement `is_available()` + `supported_data_types()`
4. Add config in `flint.yaml` providers section

## Adding a Strategy Template

1. Create in `flint/strategy/`
2. Inherit from `Strategy`, implement `name`, `on_candle`, `reset`
3. Add to builders dict in `flint/api/routes/backtest.py`

## Reporting Issues

Use [GitHub Issues](https://github.com/sohan-shingade/flint/issues) for bugs. Use [GitHub Discussions](https://github.com/sohan-shingade/flint/discussions) for questions and feature ideas.

## License

By contributing, you agree that your contributions will be licensed under the AGPL-3.0 License.
