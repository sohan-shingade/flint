"""Flint CLI — command-line interface for backtesting, optimization, and trading.

Usage:
    flint init                          # scaffold project + backfill data
    flint backtest strategy.py          # run backtest from terminal
    flint optimize strategy.py          # hyperparameter optimization
    flint serve                         # start API + UI
    flint data download --market SOL-PERP --days 365
    flint data status                   # show data coverage
    flint new strategy                  # scaffold a new strategy file
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

app = typer.Typer(
    name="flint",
    help="Algorithmic trading, backtesting, and MEV research for Solana",
    no_args_is_help=True,
)
data_app = typer.Typer(help="Data management commands")
app.add_typer(data_app, name="data")

provider_app = typer.Typer(help="Manage data providers")
data_app.add_typer(provider_app, name="provider")

console = Console()


def _load_strategy_from_file(path: str, params: Optional[dict] = None):
    """Load a Strategy from a .py file."""
    from flint.strategy.loader import load_user_strategy, StrategyLoadError

    p = Path(path)
    if not p.exists():
        console.print(f"[red]Strategy file not found: {path}[/red]")
        raise typer.Exit(1)

    code = p.read_text(encoding="utf-8")
    try:
        return load_user_strategy(code, params)
    except StrategyLoadError as e:
        console.print(f"[red]Strategy load error: {e}[/red]")
        raise typer.Exit(1)


def _print_results(result, strategy_name: str, elapsed: float):
    """Print backtest results as a rich table."""
    table = Table(title=f"Backtest Results — {strategy_name}", border_style="dim")
    table.add_column("Metric", style="dim")
    table.add_column("Value", justify="right")

    pnl_color = "green" if result.total_pnl >= 0 else "red"
    table.add_row("Total PnL", f"[{pnl_color}]${result.total_pnl:+,.2f}[/{pnl_color}]")
    table.add_row("Total Trades", str(result.total_trades))
    table.add_row("Win Rate", f"{result.win_rate * 100:.1f}%")
    table.add_row("Max Drawdown", f"{result.max_drawdown * 100:.1f}%")
    table.add_row("Sharpe Ratio", f"{result.sharpe_ratio:.2f}")
    table.add_row("Winners / Losers", f"{result.winning_trades} / {result.losing_trades}")
    table.add_row("Total Fees", f"${result.total_fees:.2f}")
    table.add_row("Funding Paid", f"${result.funding_paid:.2f}")
    table.add_row("Candles", f"{len(result.equity_curve):,}")
    table.add_row("Elapsed", f"{elapsed:.2f}s")

    console.print()
    console.print(table)

    # ASCII equity sparkline
    if result.equity_curve and len(result.equity_curve) > 1:
        eq = result.equity_curve
        mn, mx = min(eq), max(eq)
        rng = mx - mn if mx != mn else 1
        chars = "▁▂▃▄▅▆▇█"
        width = min(60, len(eq))
        step = max(1, len(eq) // width)
        spark = ""
        for i in range(0, len(eq), step):
            idx = int((eq[i] - mn) / rng * (len(chars) - 1))
            spark += chars[idx]
        console.print(f"\n  [dim]Equity:[/dim] {spark}")
        console.print(f"  [dim]       ${mn:,.0f} → ${mx:,.0f}[/dim]\n")


# ─── INIT ─────────────────────────────────────────────────

@app.command()
def init(
    days: int = typer.Option(90, help="Days of data to backfill"),
    market: str = typer.Option("SOL-PERP", help="Primary market to backfill"),
):
    """Scaffold a Flint project — create config, backfill data, run sample backtest."""
    console.print(Panel("[bold]Flint Init[/bold]\nSetting up your trading environment", border_style="yellow"))

    # 1. Create config if not exists
    config_path = Path("flint.yaml")
    if not config_path.exists():
        from flint.config import FlintConfig
        import yaml
        cfg = FlintConfig()
        config_data = {
            "db": {"path": cfg.db_path},
            "trading": {
                "default_markets": list(cfg.default_markets),
                "default_fee_rate": cfg.default_fee_rate,
                "default_capital": cfg.default_capital,
            },
            "collector": {"enabled": True, "candle_backfill_days": days},
        }
        config_path.write_text(yaml.dump(config_data, default_flow_style=False))
        console.print("  [green]✓[/green] Created flint.yaml")
    else:
        console.print("  [dim]· flint.yaml already exists[/dim]")

    # 2. Create data directory
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    console.print("  [green]✓[/green] Data directory ready")

    # 3. Create strategies directory
    strat_dir = Path("strategies/user")
    strat_dir.mkdir(parents=True, exist_ok=True)
    console.print("  [green]✓[/green] Strategies directory ready")

    # 4. Backfill data — try Drift Data API first, fall back to S3
    console.print(f"\n  Backfilling {days} days of {market} data...")
    from flint.store import FlintStore
    from flint.providers.drift_candles import DriftCandleProvider
    from flint.providers.drift_s3 import DriftS3Provider

    store = FlintStore("./data/flint.duckdb")

    end_ts = int(time.time())
    start_ts = end_ts - days * 86400

    candles = []
    with console.status(f"[bold yellow]Fetching {market} from Drift Data API...[/bold yellow]"):
        try:
            api_provider = DriftCandleProvider()
            candles = api_provider.fetch_candles(market, 3600, start_ts, end_ts)
            api_provider.close()
            if candles:
                console.print(f"  [green]✓[/green] Got {len(candles):,} candles from Drift API")
        except Exception as e:
            console.print(f"  [dim]API unavailable: {e}[/dim]")

    # Fall back to S3 if API returned nothing
    if not candles:
        console.print("  [dim]Trying Drift S3 archive...[/dim]")
        try:
            s3_provider = DriftS3Provider()
            candles = s3_provider.fetch_candles(market, 3600, start_ts, end_ts)
            s3_provider.close()
        except Exception:
            pass

    # Last resort: try a known-good date range (2024)
    if not candles:
        console.print("  [yellow]No recent data available — downloading 2024 data instead[/yellow]")
        from datetime import datetime, timezone
        fallback_start = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp())
        fallback_end = int(datetime(2024, 12, 31, tzinfo=timezone.utc).timestamp())
        try:
            api_provider = DriftCandleProvider()
            candles = api_provider.fetch_candles(market, 3600, fallback_start, fallback_end)
            api_provider.close()
        except Exception:
            pass

    if candles:
        count = store.upsert_candles(candles)
        console.print(f"  [green]✓[/green] Stored {count:,} candles for {market}")
    else:
        console.print(f"  [yellow]![/yellow] Could not fetch data. Run manually:")
        console.print(f"    flint data download --market {market} --days 365")

    # 5. Run sample backtest
    console.print("\n  Running sample backtest (MA Crossover 10/30)...")
    from flint.strategy import MACrossoverStrategy
    from flint.backtest.engine import BacktestEngine

    if candles:
        strategy = MACrossoverStrategy()
        engine = BacktestEngine(strategy, initial_capital=10_000, fee_rate=0.0005)
        t0 = time.time()
        result = engine.run(candles)
        elapsed = time.time() - t0
        _print_results(result, strategy.name, elapsed)
        console.print("[green]Flint is ready![/green] Run [bold]flint serve[/bold] to start the UI.")
    else:
        console.print("  [dim]Skipped — no data available[/dim]")

    store.close()


# ─── BACKTEST ──────────────────────────────────────────────

@app.command()
def backtest(
    strategy_file: str = typer.Argument(..., help="Path to strategy .py file"),
    market: str = typer.Option("SOL-PERP", "--market", "-m", help="Market to backtest"),
    resolution: int = typer.Option(3600, "--resolution", "-r", help="Candle resolution in seconds"),
    start: Optional[str] = typer.Option(None, "--start", "-s", help="Start date YYYY-MM-DD"),
    end: Optional[str] = typer.Option(None, "--end", "-e", help="End date YYYY-MM-DD"),
    period: Optional[str] = typer.Option(None, "--period", "-p", help="Period like 30d, 90d, 1y"),
    capital: float = typer.Option(10_000, "--capital", "-c", help="Initial capital"),
    fee_rate: float = typer.Option(0.0005, "--fee", help="Fee rate (e.g., 0.0005 for 5bps)"),
):
    """Run a backtest on a strategy file."""
    from datetime import datetime, timezone, timedelta
    from flint.store import FlintStore
    from flint.backtest.engine import BacktestEngine
    from flint.providers.drift_s3 import DriftS3Provider

    # Parse dates
    now = int(time.time())
    if period:
        unit = period[-1]
        num = int(period[:-1])
        if unit == "d":
            duration = num * 86400
        elif unit == "m":
            duration = num * 30 * 86400
        elif unit == "y":
            duration = num * 365 * 86400
        else:
            console.print(f"[red]Invalid period: {period}. Use 30d, 3m, 1y[/red]")
            raise typer.Exit(1)
        end_ts = now
        start_ts = now - duration
    elif start and end:
        start_ts = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    elif start:
        start_ts = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        end_ts = now
    else:
        # Default: last 90 days
        end_ts = now
        start_ts = now - 90 * 86400

    strategy = _load_strategy_from_file(strategy_file)
    console.print(f"  Strategy: [bold]{strategy.name}[/bold]")
    console.print(f"  Market:   {market} @ {resolution}s")

    start_date = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    end_date = datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    console.print(f"  Period:   {start_date} → {end_date}")

    # Try running via API if server is up, otherwise direct DB
    api_available = False
    try:
        import httpx
        r = httpx.get("http://localhost:8000/api/v1/health", timeout=2)
        api_available = r.status_code == 200
    except Exception:
        pass

    if api_available:
        # Run via API — avoids DuckDB lock conflicts with running server
        console.print("  [dim]Using running Flint server[/dim]")
        import httpx

        code = Path(strategy_file).read_text()
        resp = httpx.post("http://localhost:8000/api/v1/backtest/run", json={
            "strategy": "custom", "code": code, "market": market,
            "resolution_s": resolution, "start_ts": start_ts, "end_ts": end_ts,
            "initial_capital": capital, "fee_rate": fee_rate,
        }, timeout=10)
        run_id = resp.json().get("id")
        if not run_id:
            console.print(f"[red]API error: {resp.json()}[/red]")
            raise typer.Exit(1)

        with console.status("[bold yellow]Running backtest...[/bold yellow]") as spinner:
            while True:
                r = httpx.get(f"http://localhost:8000/api/v1/backtest/{run_id}/results", timeout=10)
                d = r.json()
                p = d.get("progress", {})
                if p.get("detail"):
                    spinner.update(f"[bold yellow]{p['detail']}[/bold yellow]")
                if d["status"] == "complete":
                    res = d["results"]
                    from flint.models import BacktestResult
                    result = BacktestResult(
                        total_pnl=res["metrics"].get("total_pnl", 0),
                        win_rate=res["metrics"].get("win_rate", 0),
                        max_drawdown=res["metrics"].get("max_drawdown", 0),
                        sharpe_ratio=res["metrics"].get("sharpe_ratio", 0),
                        total_trades=len(res.get("trades", [])),
                        winning_trades=int(res["metrics"].get("win_rate", 0) * len(res.get("trades", []))),
                        losing_trades=len(res.get("trades", [])) - int(res["metrics"].get("win_rate", 0) * len(res.get("trades", []))),
                        equity_curve=[pt[1] for pt in res.get("equity_curve", [])],
                        total_fees=res["metrics"].get("total_fees", 0) if "total_fees" in res.get("metrics", {}) else 0,
                        funding_paid=res["metrics"].get("funding_paid", 0) if "funding_paid" in res.get("metrics", {}) else 0,
                    )
                    elapsed = p.get("elapsed_s", 0)
                    _print_results(result, res.get("strategy_name", strategy.name), elapsed)
                    break
                elif d["status"] == "failed":
                    console.print(f"[red]Backtest failed: {d.get('results', {}).get('error', 'unknown')}[/red]")
                    raise typer.Exit(1)
                time.sleep(0.5)
    else:
        # Direct DB access — no server running
        store = FlintStore("./data/flint.duckdb")
        candles = store.query_candles(market, resolution, start_ts, end_ts)

        expected = (end_ts - start_ts) // resolution
        coverage = len(candles) / expected if expected > 0 else 0

        if coverage < 0.8:
            console.print(f"  [yellow]Local coverage: {len(candles)}/{expected} ({coverage*100:.0f}%) — fetching from Drift S3...[/yellow]")
            provider = DriftS3Provider()
            with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
                          TextColumn("{task.percentage:>3.0f}%"), console=console) as progress:
                from flint.providers.drift_s3 import _date_range
                dates = _date_range(start_ts, end_ts)
                dl_task = progress.add_task(f"Downloading {market}", total=len(dates))

                def on_progress(done, total, date_str):
                    progress.update(dl_task, completed=done)

                fetched = provider.fetch_candles(market, resolution, start_ts, end_ts, on_progress=on_progress)
                progress.update(dl_task, completed=len(dates))
            provider.close()
            if fetched:
                store.upsert_candles(fetched)
                candles = fetched
                console.print(f"  [green]✓[/green] Cached {len(candles):,} candles locally")

        store.close()

        if not candles:
            console.print("[red]No data available for this market/period[/red]")
            raise typer.Exit(1)

        console.print(f"  Candles:  {len(candles):,}")

        engine = BacktestEngine(strategy, initial_capital=capital, fee_rate=fee_rate)
        t0 = time.time()
        result = engine.run(candles)
        elapsed = time.time() - t0

        _print_results(result, strategy.name, elapsed)


# ─── OPTIMIZE ──────────────────────────────────────────────

@app.command()
def optimize(
    strategy_file: str = typer.Argument(..., help="Path to strategy .py file"),
    market: str = typer.Option("SOL-PERP", "--market", "-m"),
    resolution: int = typer.Option(3600, "--resolution", "-r"),
    start: Optional[str] = typer.Option(None, "--start", "-s"),
    end: Optional[str] = typer.Option(None, "--end", "-e"),
    period: Optional[str] = typer.Option("90d", "--period", "-p"),
    metric: str = typer.Option("sharpe_ratio", "--metric", help="Metric to optimize"),
    trials: int = typer.Option(50, "--trials", "-n", help="Number of optimization trials"),
    capital: float = typer.Option(10_000, "--capital", "-c"),
):
    """Optimize strategy parameters using Optuna."""
    from datetime import datetime, timezone
    from flint.store import FlintStore
    from flint.optimization.optimizer import StrategyOptimizer
    from flint.strategy.loader import load_user_strategy

    # Parse dates
    now = int(time.time())
    if period:
        unit = period[-1]
        num = int(period[:-1])
        duration = num * (86400 if unit == "d" else 30 * 86400 if unit == "m" else 365 * 86400)
        end_ts = now
        start_ts = now - duration
    elif start and end:
        start_ts = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    else:
        end_ts = now
        start_ts = now - 90 * 86400

    # Load strategy class (not instance)
    p = Path(strategy_file)
    if not p.exists():
        console.print(f"[red]File not found: {strategy_file}[/red]")
        raise typer.Exit(1)

    code = p.read_text()
    ns = {}
    exec(code, ns)
    from flint.strategy.base import Strategy
    strategy_cls = None
    for obj in ns.values():
        if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
            strategy_cls = obj
            break

    if strategy_cls is None:
        console.print("[red]No Strategy subclass found[/red]")
        raise typer.Exit(1)

    params = strategy_cls.parameters()
    if not params:
        console.print(f"[red]{strategy_cls.__name__}.parameters() returned empty — nothing to optimize[/red]")
        console.print("[dim]Add parameter ranges to your strategy class:[/dim]")
        console.print('[dim]    @classmethod\n    def parameters(cls):\n        return \\{"param": \\{"type": "int", "low": 5, "high": 50\\}\\}[/dim]')
        raise typer.Exit(1)

    console.print(f"  Strategy:   [bold]{strategy_cls.__name__}[/bold]")
    console.print(f"  Parameters: {', '.join(params.keys())}")
    console.print(f"  Metric:     {metric}")
    console.print(f"  Trials:     {trials}")

    store = FlintStore("./data/flint.duckdb")
    candles = store.query_candles(market, resolution, start_ts, end_ts)
    store.close()

    if not candles:
        console.print("[red]No data — run `flint data download` first[/red]")
        raise typer.Exit(1)

    console.print(f"  Candles:    {len(candles):,}\n")

    with console.status(f"[bold yellow]Optimizing ({trials} trials)...[/bold yellow]"):
        optimizer = StrategyOptimizer(strategy_cls, candles, metric=metric, n_trials=trials, initial_capital=capital)
        result = optimizer.optimize()

    # Print results
    console.print(f"\n[bold green]Best {metric}: {result.best_value:.4f}[/bold green]")

    param_table = Table(title="Best Parameters", border_style="dim")
    param_table.add_column("Parameter", style="dim")
    param_table.add_column("Value", justify="right")
    for k, v in result.best_params.items():
        param_table.add_row(k, str(v))
    console.print(param_table)

    if result.trials:
        trial_table = Table(title=f"Top 5 Trials (of {len(result.trials)})", border_style="dim")
        trial_table.add_column("#", style="dim", width=4)
        trial_table.add_column(metric, justify="right")
        trial_table.add_column("PnL", justify="right")
        trial_table.add_column("Trades", justify="right")
        trial_table.add_column("Params")
        for i, t in enumerate(result.trials[:5]):
            pnl_color = "green" if t.total_pnl >= 0 else "red"
            params_str = ", ".join(f"{k}={v}" for k, v in t.params.items())
            trial_table.add_row(
                str(i + 1),
                f"{t.metric_value:.4f}",
                f"[{pnl_color}]${t.total_pnl:+,.2f}[/{pnl_color}]",
                str(t.total_trades),
                params_str,
            )
        console.print(trial_table)


# ─── SERVE ─────────────────────────────────────────────────

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    dev: bool = typer.Option(False, "--dev", help="Run in dev mode (API only, no UI build)"),
):
    """Start the Flint API server + UI."""
    import subprocess
    import shutil
    import uvicorn

    if dev:
        console.print(Panel(
            f"[bold]Flint Server (dev mode)[/bold]\n"
            f"API: http://{host}:{port}/api/v1/health\n"
            f"UI:  run [bold]cd ui && npm run dev[/bold] in another terminal",
            border_style="yellow",
        ))
        uvicorn.run("flint.api.main:app", host=host, port=port, reload=True)
        return

    # Production mode: build UI if needed, then serve everything from one process
    ui_dir = Path(__file__).resolve().parent.parent / "ui"
    dist_dir = ui_dir / "dist"

    # Build UI if dist doesn't exist or is stale
    if ui_dir.exists() and (ui_dir / "package.json").exists():
        needs_build = not dist_dir.exists() or not (dist_dir / "index.html").exists()

        if not needs_build:
            # Check if source is newer than build
            src_dir = ui_dir / "src"
            if src_dir.exists():
                build_time = (dist_dir / "index.html").stat().st_mtime
                for src_file in src_dir.rglob("*"):
                    if src_file.is_file() and src_file.stat().st_mtime > build_time:
                        needs_build = True
                        break

        if needs_build:
            npm = shutil.which("npm")
            if npm:
                console.print("[yellow]Building UI...[/yellow]")
                try:
                    # Install deps if needed
                    if not (ui_dir / "node_modules").exists():
                        subprocess.run([npm, "install"], cwd=str(ui_dir), check=True,
                                       capture_output=True, timeout=120)
                    subprocess.run([npm, "run", "build"], cwd=str(ui_dir), check=True,
                                   capture_output=True, timeout=120)
                    console.print("[green]UI built successfully[/green]")
                except subprocess.CalledProcessError as e:
                    console.print(f"[red]UI build failed:[/red] {e.stderr.decode()[:500] if e.stderr else 'unknown error'}")
                except FileNotFoundError:
                    console.print("[yellow]npm not found — serving API only[/yellow]")
            else:
                console.print("[yellow]npm not found — serving API only (install Node.js for UI)[/yellow]")

    has_ui = dist_dir.exists() and (dist_dir / "index.html").exists()
    ui_url = f"http://localhost:{port}" if has_ui else "not available (npm not found)"

    console.print(Panel(
        f"[bold]Flint Server[/bold]\n"
        f"API: http://{host}:{port}/api/v1/health\n"
        f"UI:  {ui_url}",
        border_style="yellow",
    ))

    uvicorn.run("flint.api.main:app", host=host, port=port)


# ─── DATA COMMANDS ─────────────────────────────────────────

@data_app.command("download")
def data_download(
    market: Optional[List[str]] = typer.Option(None, "--market", "-m", help="Market(s) to download (repeatable)"),
    days: int = typer.Option(365, "--days", "-d"),
    resolution: int = typer.Option(3600, "--resolution", "-r"),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="End date YYYY-MM-DD"),
):
    """Download historical data from Drift S3."""
    from datetime import datetime, timezone
    from flint.store import FlintStore
    from flint.providers.drift_s3 import DriftS3Provider, _date_range

    markets = market if market else ["SOL-PERP"]

    if end_date:
        end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    else:
        end_ts = int(time.time())
    start_ts = end_ts - days * 86400

    store = FlintStore("./data/flint.duckdb")
    provider = DriftS3Provider()

    for mkt in markets:
        console.print(f"\n  Market:     {mkt}")
        console.print(f"  Resolution: {resolution}s")
        console.print(f"  Days:       {days}")

        dates = _date_range(start_ts, end_ts)
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
                      TextColumn("{task.percentage:>3.0f}%"), console=console) as progress:
            task = progress.add_task(f"Downloading {mkt}", total=len(dates))

            def on_progress(done, total, date_str):
                progress.update(task, completed=done)

            candles = provider.fetch_candles(mkt, resolution, start_ts, end_ts, on_progress=on_progress)
            progress.update(task, completed=len(dates))

        if candles:
            count = store.upsert_candles(candles)
            console.print(f"  [green]✓[/green] Stored {count:,} candles for {mkt}")
        else:
            console.print(f"  [yellow]No data found for {mkt}[/yellow]")

    provider.close()
    store.close()


@data_app.command("status")
def data_status():
    """Show data coverage in the local database."""
    from datetime import datetime, timezone
    import duckdb

    db_path = Path("./data/flint.duckdb")
    if not db_path.exists():
        console.print("[yellow]No database found. Run `flint init` first.[/yellow]")
        raise typer.Exit(1)

    # Try API first (if server is running), then fall back to direct DB
    rows = None
    try:
        import httpx
        r = httpx.get("http://localhost:8000/api/v1/data/markets", timeout=3)
        if r.status_code == 200:
            markets = r.json().get("markets", [])
            rows = [(m["market"], m["resolution_s"], m["candle_count"], m["first_ts"], m["last_ts"]) for m in markets]
    except Exception:
        pass

    if rows is None:
        try:
            import duckdb
            conn = duckdb.connect(str(db_path), read_only=True)
            rows = conn.execute(
                "SELECT market, resolution_s, COUNT(*) as cnt, MIN(ts) as first_ts, MAX(ts) as last_ts "
                "FROM candles GROUP BY market, resolution_s ORDER BY market"
            ).fetchall()
            conn.close()
        except Exception as e:
            console.print(f"[red]Could not read database (is the server running?): {e}[/red]")
            console.print("[dim]Try: flint serve (in another terminal), then flint data status[/dim]")
            raise typer.Exit(1)

    if not rows:
        console.print("[yellow]Database is empty. Run `flint data download` or `flint init`.[/yellow]")
        return

    table = Table(title="Data Inventory", border_style="dim")
    table.add_column("Market", style="bold")
    table.add_column("Resolution", justify="right")
    table.add_column("Candles", justify="right")
    table.add_column("From", style="dim")
    table.add_column("To", style="dim")

    for market, res, cnt, first_ts, last_ts in rows:
        first = datetime.fromtimestamp(first_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        last = datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        res_label = f"{res // 3600}h" if res >= 3600 else f"{res // 60}m" if res >= 60 else f"{res}s"
        table.add_row(market, res_label, f"{cnt:,}", first, last)

    console.print(table)
    console.print(f"\n  [dim]{len(rows)} market/resolution pairs in database[/dim]")


# ─── CCXT / EXCHANGE COMMANDS ──────────────────────────────

@data_app.command("exchanges")
def data_exchanges():
    """List supported exchanges (via CCXT)."""
    try:
        from flint.providers.ccxt_provider import CCXTProvider, _is_ccxt_available
    except ImportError:
        console.print("[red]Could not import CCXTProvider[/red]")
        raise typer.Exit(1)

    common = CCXTProvider.list_exchanges()

    table = Table(title="Supported Exchanges (CCXT)", border_style="dim")
    table.add_column("#", style="dim", width=4)
    table.add_column("Exchange", style="bold")
    table.add_column("Status", justify="center")

    if _is_ccxt_available():
        all_exchanges = CCXTProvider.list_all_exchanges()
        for i, name in enumerate(common, 1):
            table.add_row(str(i), name, "[green]available[/green]")
        console.print(table)
        console.print(f"\n  [dim]{len(common)} common exchanges shown. {len(all_exchanges)} total supported by CCXT.[/dim]")
        console.print("  [dim]Use any exchange name with: flint data markets <exchange>[/dim]")
    else:
        for i, name in enumerate(common, 1):
            table.add_row(str(i), name, "[yellow]ccxt not installed[/yellow]")
        console.print(table)
        console.print("\n  [yellow]Install CCXT for live exchange access:[/yellow]")
        console.print("  pip install 'flint[ccxt]'  [dim]or[/dim]  pip install ccxt>=4.0")


@data_app.command("markets")
def data_markets(
    exchange: str = typer.Argument(..., help="Exchange name (e.g. binance, bybit, okx)"),
    market_type: str = typer.Option("swap", "--type", "-t", help="Market type: swap, spot, future, option"),
    quote: str = typer.Option("USDT", "--quote", "-q", help="Quote currency filter"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max markets to display"),
):
    """List available markets on an exchange (via CCXT)."""
    try:
        from flint.providers.ccxt_provider import CCXTProvider
    except ImportError:
        console.print("[red]Could not import CCXTProvider[/red]")
        raise typer.Exit(1)

    try:
        provider = CCXTProvider(exchange=exchange)
    except Exception as e:
        console.print(f"[red]Error creating provider: {e}[/red]")
        raise typer.Exit(1)

    with console.status(f"[bold yellow]Loading {exchange} markets...[/bold yellow]"):
        try:
            markets = provider.list_markets(quote=quote)
        except ImportError:
            console.print("\n  [yellow]CCXT is not installed.[/yellow]")
            console.print("  pip install 'flint[ccxt]'  [dim]or[/dim]  pip install ccxt>=4.0")
            provider.close()
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"\n  [red]Error loading markets: {e}[/red]")
            provider.close()
            raise typer.Exit(1)

    # Filter by type
    if market_type:
        markets = [m for m in markets if m.get("type") == market_type]

    if not markets:
        console.print(f"  [yellow]No {market_type} markets with quote={quote} on {exchange}[/yellow]")
        console.print("  [dim]Try: --type spot  or  --quote BTC  or  --type swap --quote USD[/dim]")
        provider.close()
        return

    table = Table(
        title=f"{exchange.title()} Markets — {market_type} / {quote} ({len(markets)} found)",
        border_style="dim",
    )
    table.add_column("Symbol", style="bold")
    table.add_column("Flint Name")
    table.add_column("Base")
    table.add_column("Quote")
    table.add_column("Type", style="dim")
    table.add_column("Active", justify="center")

    for m in markets[:limit]:
        active = "[green]yes[/green]" if m.get("active") else "[red]no[/red]"
        table.add_row(
            m["symbol"],
            m.get("flint_symbol", ""),
            m["base"],
            m["quote"],
            m["type"],
            active,
        )

    console.print(table)
    if len(markets) > limit:
        console.print(f"\n  [dim]Showing {limit} of {len(markets)} markets. Use --limit to show more.[/dim]")

    provider.close()


# ─── NEW STRATEGY ──────────────────────────────────────────

@app.command("new")
def new_strategy(
    name: str = typer.Argument("my_strategy", help="Strategy name"),
    v2: bool = typer.Option(False, "--v2", help="Use v2 ExecutionContext API"),
):
    """Scaffold a new strategy file."""
    filename = f"strategies/user/{name}.py"
    p = Path(filename)
    p.parent.mkdir(parents=True, exist_ok=True)

    if p.exists():
        console.print(f"[yellow]File already exists: {filename}[/yellow]")
        raise typer.Exit(1)

    if v2:
        template = f'''from flint.strategy.base import Strategy
from flint.models import Candle, Signal, Side
from typing import List, Optional

# v2 strategy — uses ExecutionContext for order management

class {name.title().replace("_", "")}Strategy(Strategy):
    def __init__(self):
        pass

    @property
    def name(self) -> str:
        return "{name}"

    @classmethod
    def parameters(cls) -> dict:
        return {{}}  # Add optimizable params here

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if ctx is None or len(history) < 20:
            return Signal.HOLD

        # Your logic here — use ctx.market_order(), ctx.limit_order(), etc.
        # Example:
        # if some_condition:
        #     ctx.market_order(candle.market, Side.LONG, 10.0)
        #     ctx.stop_order(candle.market, Side.SHORT, 10.0, candle.close * 0.95)

        return Signal.HOLD

    def reset(self) -> None:
        pass
'''
    else:
        template = f'''from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class {name.title().replace("_", "")}Strategy(Strategy):
    def __init__(self):
        pass

    @property
    def name(self) -> str:
        return "{name}"

    @classmethod
    def parameters(cls) -> dict:
        return {{}}  # Add optimizable params here

    def on_candle(self, candle: Candle, history: List[Candle], ctx=None) -> Signal:
        if len(history) < 20:
            return Signal.HOLD

        # Your logic here
        # Return Signal.BUY, Signal.SELL, or Signal.HOLD

        return Signal.HOLD

    def reset(self) -> None:
        pass
'''

    p.write_text(template)
    console.print(f"  [green]✓[/green] Created {filename}")
    console.print(f"  Run: [bold]flint backtest {filename} --market SOL-PERP --period 90d[/bold]")


# ─── LIVE TRADING ──────────────────────────────────────────

@app.command()
def live(
    strategy_file: str = typer.Argument(..., help="Path to strategy .py file"),
    market: str = typer.Option("SOL-PERP", "--market", "-m"),
    paper: bool = typer.Option(True, "--paper/--real", help="Paper trading (default) or real"),
    capital: float = typer.Option(10_000, "--capital", "-c"),
    private_key: Optional[str] = typer.Option(None, "--key", help="Base58 private key (or set FLINT_PRIVATE_KEY)"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc", help="Solana RPC URL"),
):
    """Run a strategy live (paper or real trading on Drift)."""
    import asyncio
    import os

    strategy = _load_strategy_from_file(strategy_file)
    console.print(f"  Strategy: [bold]{strategy.name}[/bold]")
    console.print(f"  Market:   {market}")
    console.print(f"  Mode:     [{'yellow' if paper else 'red'}]{'PAPER' if paper else 'LIVE (REAL MONEY)'}[/{'yellow' if paper else 'red'}]")

    if not paper:
        if not private_key and not os.environ.get("FLINT_PRIVATE_KEY"):
            console.print("[red]Live trading requires a private key. Set FLINT_PRIVATE_KEY or use --key[/red]")
            raise typer.Exit(1)

        confirm = typer.confirm("You are about to trade with REAL MONEY on Drift. Continue?")
        if not confirm:
            raise typer.Exit(0)

        try:
            from flint.execution.drift_live import LiveDriftContext
        except ImportError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

        console.print("[red bold]Live trading starting...[/red bold]")
        ctx = LiveDriftContext(private_key=private_key, rpc_url=rpc_url, initial_capital=capital)

        async def _run_live():
            await ctx.connect()
            console.print("[green]Connected to Drift Protocol[/green]")
            positions = ctx.positions
            if positions:
                for p in positions:
                    console.print(f"  Position: {p.market} {p.side.value} {p.size:.4f} @ {p.entry_price:.2f}")
            else:
                console.print("  No open positions")
            await ctx.disconnect()

        asyncio.run(_run_live())
    else:
        console.print("[yellow]Paper trading mode — using simulated execution[/yellow]")
        console.print("  Start the server: [bold]flint serve[/bold]")
        console.print("  Then use the API: POST /api/v1/paper/start")


# ─── PROVIDER MANAGEMENT ──────────────────────────────────

def _load_providers_yaml() -> dict:
    """Read flint.yaml and return the providers section."""
    import yaml

    yaml_path = Path("flint.yaml")
    if not yaml_path.exists():
        return {}
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("providers", {})


def _build_registry(providers_cfg: dict):
    """Create a ProviderRegistry and register all known providers."""
    from flint.providers.registry import ProviderRegistry
    from flint.providers.birdeye import BirdeyeProvider
    from flint.providers.helius import HeliusProvider
    from flint.providers.pyth import PythProvider
    from flint.providers.raydium import RaydiumProvider
    from flint.providers.orca import OrcaProvider
    from flint.providers.open_interest import DriftOpenInterestProvider

    registry = ProviderRegistry()
    registry.register(BirdeyeProvider())
    registry.register(HeliusProvider())
    registry.register(PythProvider())
    registry.register(RaydiumProvider())
    registry.register(OrcaProvider())
    registry.register(DriftOpenInterestProvider())

    # Register CCXT provider if configured
    try:
        from flint.providers.ccxt_provider import CCXTProvider
        ccxt_cfg = providers_cfg.get("ccxt", {})
        exchange = ccxt_cfg.get("exchange", "binance") if isinstance(ccxt_cfg, dict) else "binance"
        api_key = ccxt_cfg.get("api_key", "") if isinstance(ccxt_cfg, dict) else ""
        secret = ccxt_cfg.get("secret", "") if isinstance(ccxt_cfg, dict) else ""
        registry.register(CCXTProvider(exchange=exchange, api_key=api_key, secret=secret))
    except Exception:
        pass  # CCXT is optional

    registry.load_config(providers_cfg)
    return registry


@provider_app.command("status")
def provider_status():
    """Show status of all data providers."""
    from flint.config import load_config

    providers_cfg = _load_providers_yaml()
    registry = _build_registry(providers_cfg)
    statuses = registry.status()

    table = Table(title="Data Providers", border_style="dim")
    table.add_column("Provider", style="bold")
    table.add_column("Enabled", justify="center")
    table.add_column("Available", justify="center")
    table.add_column("API Key", justify="center")
    table.add_column("Data Types")

    for s in statuses:
        enabled = "[green]yes[/green]" if s["enabled"] else "[dim]no[/dim]"
        available = "[green]yes[/green]" if s["available"] else "[red]no[/red]"
        api_key = "[yellow]required[/yellow]" if s["requires_api_key"] else "[dim]—[/dim]"
        data_types = ", ".join(s["data_types"]) if s["data_types"] else "[dim]—[/dim]"
        table.add_row(s["name"], enabled, available, api_key, data_types)

    console.print(table)
    console.print(f"\n  [dim]{len(statuses)} providers registered[/dim]")


@provider_app.command("list")
def provider_list():
    """List all data providers (alias for status)."""
    provider_status()


@provider_app.command("enable")
def provider_enable(
    name: str = typer.Argument(..., help="Provider name to enable"),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="API key (appended to .env)"),
):
    """Enable a data provider in flint.yaml."""
    import yaml

    yaml_path = Path("flint.yaml")
    if not yaml_path.exists():
        console.print("[red]flint.yaml not found. Run `flint init` first.[/red]")
        raise typer.Exit(1)

    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    providers = data.setdefault("providers", {})
    if name not in providers:
        providers[name] = {}
    if isinstance(providers[name], dict):
        providers[name]["enabled"] = True
    else:
        providers[name] = {"enabled": True}

    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    console.print(f"  [green]✓[/green] Enabled provider [bold]{name}[/bold] in flint.yaml")

    if api_key:
        env_path = Path(".env")
        env_var = f"FLINT_{name.upper()}_API_KEY={api_key}\n"
        with open(env_path, "a") as f:
            f.write(env_var)
        console.print(f"  [green]✓[/green] Appended {name.upper()} API key to .env")


@provider_app.command("disable")
def provider_disable(
    name: str = typer.Argument(..., help="Provider name to disable"),
):
    """Disable a data provider in flint.yaml."""
    import yaml

    yaml_path = Path("flint.yaml")
    if not yaml_path.exists():
        console.print("[red]flint.yaml not found. Run `flint init` first.[/red]")
        raise typer.Exit(1)

    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    providers = data.setdefault("providers", {})
    if name not in providers:
        providers[name] = {}
    if isinstance(providers[name], dict):
        providers[name]["enabled"] = False
    else:
        providers[name] = {"enabled": False}

    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    console.print(f"  [green]✓[/green] Disabled provider [bold]{name}[/bold] in flint.yaml")


# ─── PARITY TEST ──────────────────────────────────────────

@app.command()
def parity(
    strategy: str = typer.Argument(..., help="Strategy name (e.g. momentum)"),
    market: str = typer.Option("SOL-PERP", help="Market to test"),
    start: str = typer.Option(..., help="Start date (YYYY-MM-DD)"),
    end: str = typer.Option(..., help="End date (YYYY-MM-DD)"),
    capital: float = typer.Option(10_000.0, help="Initial capital"),
    fee_rate: float = typer.Option(0.0005, help="Fee rate"),
):
    """Run backtest-vs-paper parity test."""
    import datetime
    from flint.config import load_config
    from flint.store import FlintStore
    from flint.backtest.parity import ParityTest

    config = load_config()
    store = FlintStore(config.db_path)

    start_dt = datetime.datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.datetime.strptime(end, "%Y-%m-%d")
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    candles = store.query_candles(market, 3600, start_ts=start_ts, end_ts=end_ts)
    if not candles:
        console.print(f"[red]No candle data for {market} in date range. Run 'flint init' first.[/red]")
        store.close()
        raise typer.Exit(1)

    from flint.api.routes.backtest import _build_strategy
    strat = _build_strategy(strategy, {})
    if strat is None:
        console.print(f"[red]Unknown strategy: {strategy}[/red]")
        store.close()
        raise typer.Exit(1)

    console.print(f"Parity Test: {strategy} on {market} ({start} to {end})")
    console.print("[dim]" + "\u2500" * 50 + "[/dim]")

    pt = ParityTest(
        strategy=strat, market=market, candles=candles,
        initial_capital=capital, fee_rate=fee_rate,
    )
    report = pt.run()
    console.print(report.summary())
    store.close()


# ─── ENTRY POINT ───────────────────────────────────────────

def main():
    app()


if __name__ == "__main__":
    main()
