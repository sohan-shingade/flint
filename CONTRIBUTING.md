# Contributing to Flint

Thanks for your interest in contributing. Flint is a local-first trading research platform for Solana — contributions that improve backtesting accuracy, data coverage, strategy quality, or developer experience are all welcome.

## Dev Setup

```bash
git clone https://github.com/sohan-shingade/flint.git
cd flint
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
flint init           # download sample data + run a quick smoke test
```

Run the UI in dev mode (optional):

```bash
cd ui && npm install && npm run dev   # hot-reload UI at localhost:5173
flint serve --dev                     # API only at localhost:8000
```

## Running Tests

```bash
pytest tests/ -v              # all 536+ tests, ~90s, fully mocked
pytest tests/ -k "backtest"   # filter by keyword
pytest tests/test_birdeye.py  # single file
```

All tests use mocks — no network calls, no API keys needed. Every new feature needs tests.

## Code Standards

- **Python**: standard library conventions, type hints where helpful
- **No strict formatter** — be consistent with surrounding code
- **No new dependencies** without opening an issue first to discuss
- **All DuckDB access** goes through `FlintStore` with `with self._lock:` — never create a second connection, never access `store._conn` directly from API routes
- **Strategy loader** blocks non-approved imports — only `flint`, `numpy`, `math`, `statistics`, `collections`, `dataclasses`, `typing`, `enum`, `abc`, `functools`, `itertools`, `operator`
- **Follow existing patterns** — look at how similar code is structured before writing new code

## Adding a Strategy

1. Create `flint/strategy/my_strategy.py`
2. Inherit from `Strategy`, implement `name`, `parameters`, `on_candle`, `reset`
3. Add to the builders dict in `flint/api/routes/backtest.py`
4. Add tests in `tests/`
5. Add a README at `docs/strategies/my_strategy.md` — include: signal logic, parameters, backtest results (PnL, Sharpe, max drawdown), and known limitations
6. Submit a PR using the PR template — backtest results are required

Strategy PRs without backtest results will not be merged.

## Adding a Data Provider

1. Create `flint/providers/my_provider.py`
2. Inherit from `DataProvider` in `registry.py`
3. Implement `is_available()` and `supported_data_types()`
4. Add config in `flint.yaml` providers section
5. Add tests in `tests/`

## Adding an API Endpoint

1. Add to the relevant router in `flint/api/routes/`
2. Register the router in `flint/api/main.py` if it's a new file
3. Use `app.state.store` for all DB access — never create a new `FlintStore`

## PR Process

1. **Fork** the repo and create a branch from `main`
2. **Write tests** for any new functionality
3. **Run the full test suite** — PRs that break tests will not be merged
4. **Open a PR** using the pull request template
5. **For strategy PRs**: include backtest results (period, capital, PnL, Sharpe, drawdown, trade count)
6. Keep PRs focused — one feature or fix per PR

## Reporting Issues

- **Bugs**: use [GitHub Issues](https://github.com/sohan-shingade/flint/issues) with the bug report template
- **Questions**: use [GitHub Discussions](https://github.com/sohan-shingade/flint/discussions) — the Q&A category
- **Feature ideas**: use Discussions (Ideas category) for early discussion before opening a PR
- **Strategy ideas**: use the strategy idea issue template

## Code of Conduct

Be professional and constructive. Criticism of code is fine; personal attacks are not. If something isn't working, explain the problem clearly. If you disagree with a design decision, make the case with evidence. We are all here to build something useful.

## License

By contributing, you agree that your contributions will be licensed under the AGPL-3.0 License.
