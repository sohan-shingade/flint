"""The ``flint`` CLI — the everyday drivers over services/ (§12).

Commands are exercised through their injectable seams (``lab=``/``runner=``/``out=``) so the
whole surface is unit-tested with fixtures, no network, no keys (D26). The API-security
contract (a per-session token printed at ``serve`` start, a non-localhost warning) and the
D20 live-cap refusal are asserted here rather than left to a live run.
"""

from __future__ import annotations

import argparse
import json

import duckdb
import pyarrow as pa
import pytest

from flint.adapters import InMemoryUserData
from flint.data import DataManager
from flint.data.ranges import Kind
from flint.data.sources import InMemoryCacheSource
from flint.ports import TenantContext
from flint.sdk import Lab, cli

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
T0 = 1_700_000_000_000
H = 3_600_000
N = 6
COVERED_END = T0 + (N - 1) * H + 60_000 + 1


def _candles(n: int = N) -> pa.Table:
    return pa.table(
        {
            "ts": [T0 + i * H for i in range(n)],
            "open": [100.0] * n,
            "high": [100.0] * n,
            "low": [100.0] * n,
            "close": [100.0] * n,
            "volume": [1000.0] * n,
            "market": [MARKET] * n,
            "resolution_s": [3600] * n,
            "venue": [VENUE] * n,
        }
    )


def _funding(n: int = N) -> pa.Table:
    ts_pred = [T0 + i * H for i in range(n)]
    ts_fin = [T0 + i * H + 60_000 for i in range(n)]
    return pa.table(
        {
            "ts": ts_pred + ts_fin,
            "rate_hourly": [0.001] * (2 * n),
            "interval_s": [3600] * (2 * n),
            "price_basis": ["oracle"] * (2 * n),
            "rate_type": ["predicted"] * n + ["final"] * n,
            "venue": [VENUE] * (2 * n),
            "market": [MARKET] * (2 * n),
        }
    )


def _data() -> DataManager:
    cache = InMemoryCacheSource()
    cache.store(VENUE, MARKET, Kind.CANDLES, _candles())
    cache.store(VENUE, MARKET, Kind.FUNDING, _funding())
    return DataManager(sources=[cache])


def _lab(ud: InMemoryUserData | None = None) -> Lab:
    return Lab(
        TenantContext(tenant_id="alice"),
        user_data=ud or InMemoryUserData(),
        data=_data(),
    )


def _ns(**kw) -> argparse.Namespace:
    kw.setdefault("tenant", "alice")
    return argparse.Namespace(**kw)


def _collect():
    out: list[str] = []
    return out, out.append


# -- backtest ----------------------------------------------------------------


def test_cli_backtest_prints_the_tearsheet_and_exits_zero():
    out, sink = _collect()
    args = _ns(
        strategy="funding_harvest",
        universe=MARKET,
        venues=VENUE,
        start=T0,
        end=COVERED_END,
        resolution=3600,
        seed=0,
        capital="100000",
        param=[],
        signal_venues="",
        json=False,
    )
    rc = cli.cmd_backtest(args, lab=_lab(), out=sink)
    assert rc == 0
    assert "Sharpe (raw):" in out[0]
    assert "timing (total" in out[0]


def test_cli_backtest_rejected_range_exits_nonzero_with_structured_reason():
    out, sink = _collect()
    args = _ns(
        strategy="funding_harvest",
        universe=MARKET,
        venues=VENUE,
        start=T0,
        end=COVERED_END + H,
        resolution=3600,
        seed=0,
        capital="100000",
        param=[],
        signal_venues="",
        json=False,
    )
    rc = cli.cmd_backtest(args, lab=_lab(), out=sink)
    assert rc == 3  # rejected verdict → nonzero exit for scripting
    assert "REJECTED [funding_gap]" in out[0]


def test_cli_backtest_json_emits_the_summary_blob():
    out, sink = _collect()
    args = _ns(
        strategy="funding_harvest",
        universe=MARKET,
        venues=VENUE,
        start=T0,
        end=COVERED_END,
        resolution=3600,
        seed=0,
        capital="100000",
        param=[],
        signal_venues="",
        json=True,
    )
    cli.cmd_backtest(args, lab=_lab(), out=sink)
    summary = json.loads(out[0])
    assert summary["verdict"] == "ok"
    assert "timing" in summary


def test_cli_backtest_param_override_is_typed():
    # --param notional_usd=500 must reach the template as a number, not the string "500".
    out, sink = _collect()
    args = _ns(
        strategy="funding_harvest",
        universe=MARKET,
        venues=VENUE,
        start=T0,
        end=COVERED_END,
        resolution=3600,
        seed=0,
        capital="100000",
        param=["notional_usd=500"],
        signal_venues="",
        json=True,
    )
    rc = cli.cmd_backtest(args, lab=_lab(), out=sink)
    assert rc == 0  # a bad (string) coercion would blow up template param validation


# -- optimize ----------------------------------------------------------------


def test_cli_optimize_prints_dsr_and_walkforward_block():
    out, sink = _collect()
    args = _ns(
        strategy="funding_harvest",
        universe=MARKET,
        venues=VENUE,
        start=T0,
        end=COVERED_END,
        resolution=3600,
        seed=1,
        capital="100000",
        param=["rate_threshold=0.0:0.0005"],
        windows=2,
        trials=2,
        purge=0,
        embargo=0,
        label_horizon=1,
        json=False,
    )
    rc = cli.cmd_optimize(args, lab=_lab(), out=sink)
    assert rc == 0
    assert "Deflated Sharpe:" in out[0]
    assert "walk-forward: 2 windows, 4 trials" in out[0]


def test_cli_optimize_without_a_param_is_a_usage_error():
    out, sink = _collect()
    args = _ns(
        strategy="funding_harvest",
        universe=MARKET,
        venues=VENUE,
        start=T0,
        end=COVERED_END,
        resolution=3600,
        seed=0,
        capital="100000",
        param=[],
        windows=2,
        trials=2,
        purge=0,
        embargo=0,
        label_horizon=1,
        json=False,
    )
    rc = cli.cmd_optimize(args, lab=_lab(), out=sink)
    assert rc == 2
    assert "--param" in out[0]


# -- paper -------------------------------------------------------------------


def test_cli_paper_starts_a_session_and_reports_the_run_id():
    ud = InMemoryUserData()
    lab = _lab(ud)
    out, sink = _collect()
    args = _ns(
        strategy="funding_harvest",
        market=MARKET,
        resolution=3600,
        seed=0,
        capital="100000",
        param=[],
    )
    rc = cli.cmd_paper(args, lab=lab, out=sink)
    assert rc == 0
    assert "paper session" in out[0]
    assert "/stream" in out[0]  # points the user at the live monitor
    assert ud.list_runs(lab.tenant)  # the head is persisted


# -- live (D20 safety cap) ---------------------------------------------------


def test_cli_live_refuses_without_a_position_cap():
    out, sink = _collect()
    rc = cli.cmd_live(
        _ns(
            stop=False,
            max_position_usd=None,
            max_daily_loss_usd=None,
            strategy=None,
            market=None,
        ),
        out=sink,
    )
    assert rc == 2
    assert "--max-position-usd is required" in out[0]


def test_cli_live_with_a_cap_is_an_honest_not_yet_no_order_placed():
    out, sink = _collect()
    rc = cli.cmd_live(
        _ns(
            stop=False,
            max_position_usd=1000.0,
            max_daily_loss_usd=250.0,
            strategy="x",
            market=MARKET,
        ),
        out=sink,
    )
    assert rc == 0
    assert "no order was placed" in out[0]


# -- serve (local API security) ----------------------------------------------


def test_cli_serve_prints_a_session_token_and_binds_localhost_by_default():
    ran: dict = {}

    def fake_runner(app, host, port):
        ran["host"], ran["port"] = host, port

    out, sink = _collect()
    rc = cli.cmd_serve(
        _ns(host="127.0.0.1", port=8000), lab=_lab(), runner=fake_runner, out=sink
    )
    assert rc == 0
    token_line = next(line for line in out if "session token:" in line)
    assert len(token_line.split("session token: ")[1]) > 20  # a real per-session token
    assert ran == {"host": "127.0.0.1", "port": 8000}
    assert not any("WARNING" in line for line in out)  # localhost bind → no warning


def test_cli_serve_warns_loudly_on_a_non_localhost_bind():
    out, sink = _collect()
    cli.cmd_serve(
        _ns(host="0.0.0.0", port=8000), lab=_lab(), runner=lambda *a: None, out=sink
    )
    assert any("WARNING" in line for line in out)


# -- data --------------------------------------------------------------------


def test_cli_data_coverage_reports_per_kind_ranges():
    out, sink = _collect()
    cli.cmd_data_coverage(_ns(market=MARKET, venue=VENUE), lab=_lab(), out=sink)
    cov = json.loads(out[0])["coverage"]
    assert cov["candles"] is not None
    assert cov["oi"] is None  # nothing stored → honestly absent


def test_cli_data_cache_warm_fetches_rows():
    out, sink = _collect()
    args = _ns(
        warm=True,
        market=MARKET,
        venues=VENUE,
        kind="candles",
        start=T0,
        end=COVERED_END,
    )
    cli.cmd_data_cache(args, lab=_lab(), out=sink)
    assert json.loads(out[0])["rows"]


def test_cli_data_cache_without_warm_is_a_noop():
    out, sink = _collect()
    args = _ns(
        warm=False,
        market=MARKET,
        venues=VENUE,
        kind="candles",
        start=T0,
        end=COVERED_END,
    )
    rc = cli.cmd_data_cache(args, lab=_lab(), out=sink)
    assert rc == 0
    assert "--warm" in out[0]


# -- export (reproducibility bundle) -----------------------------------------


def test_cli_export_writes_the_reproducibility_bundle(tmp_path):
    ud = InMemoryUserData()
    lab = _lab(ud)
    lab.backtest(
        "funding_harvest",
        universe=[MARKET],
        venues=[VENUE],
        start_ms=T0,
        end_ms=COVERED_END,
        run_id="run-export",
    )
    dest = tmp_path / "bundle.flint"
    out, sink = _collect()
    rc = cli.cmd_export(_ns(run_id="run-export", out=str(dest)), lab=lab, out=sink)
    assert rc == 0
    bundle = json.loads(dest.read_text())
    assert bundle["run_id"] == "run-export"
    assert bundle["events"]  # carries the recorded event stream for bit-for-bit re-run


def test_cli_export_to_stdout_when_no_out_given():
    ud = InMemoryUserData()
    lab = _lab(ud)
    lab.backtest(
        "funding_harvest",
        universe=[MARKET],
        venues=[VENUE],
        start_ms=T0,
        end_ms=COVERED_END,
        run_id="run-2",
    )
    out, sink = _collect()
    cli.cmd_export(_ns(run_id="run-2", out=None), lab=lab, out=sink)
    assert json.loads(out[0])["run_id"] == "run-2"


# -- legacy import (§19.6, carry-forward g) ----------------------------------


def _legacy_db(path) -> None:
    """A hand-authored legacy DuckDB with only the run-metadata tables (D26)."""
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE strategies ("
            "strategy_id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, code TEXT NOT NULL "
            "DEFAULT '', params_json VARCHAR NOT NULL DEFAULT '{}', category VARCHAR NOT "
            "NULL DEFAULT 'custom', status VARCHAR NOT NULL DEFAULT 'draft', created_at "
            "BIGINT NOT NULL, updated_at BIGINT NOT NULL, notes VARCHAR NOT NULL DEFAULT '')"
        )
        con.execute(
            "INSERT INTO strategies VALUES "
            "('sid-1','sma_cross','class S: pass','{}','custom','final',1,2,'')"
        )
    finally:
        con.close()


def test_cli_import_legacy_lifts_runs_into_the_library(tmp_path):
    path = tmp_path / "legacy.duckdb"
    _legacy_db(path)
    ud = InMemoryUserData()
    lab = _lab(ud)
    out, sink = _collect()
    rc = cli.cmd_data_import_legacy(_ns(path=str(path)), lab=lab, out=sink)
    assert rc == 0
    imported = json.loads(out[0])["imported"]
    assert imported  # the legacy strategy became a Run-Library manifest
    assert ud.list_runs(lab.tenant)


# -- recorder ----------------------------------------------------------------


def test_cli_recorder_start_reports_the_capture_plan():
    out, sink = _collect()
    rc = cli.cmd_recorder_start(
        _ns(market=MARKET, venue=VENUE, resolution=3600), out=sink
    )
    assert rc == 0
    plan = json.loads(out[0])
    assert plan["action"] == "recorder.start"
    assert plan["market"] == MARKET


# -- parser + main dispatch --------------------------------------------------


def test_parser_exposes_every_command():
    parser = cli.build_parser()
    # A representative parse for each top-level command must succeed.
    assert parser.parse_args(
        ["backtest", "--strategy", "x", "--start", "0", "--end", "1"]
    ).func
    assert parser.parse_args(["serve"]).func
    assert parser.parse_args(["data", "coverage", "--market", "m", "--venue", "v"]).func
    assert parser.parse_args(["export", "--run-id", "r"]).func
    assert parser.parse_args(["recorder", "start", "--market", "m"]).func


def test_main_dispatches_through_the_parser(monkeypatch):
    lab = _lab()
    monkeypatch.setattr(cli, "_build_lab", lambda args: lab)
    rc = cli.main(
        [
            "backtest",
            "--strategy",
            "funding_harvest",
            "--universe",
            MARKET,
            "--venues",
            VENUE,
            "--start",
            str(T0),
            "--end",
            str(COVERED_END),
            "--json",
        ]
    )
    assert rc == 0


def test_main_missing_required_arg_exits_two():
    with pytest.raises(SystemExit) as exc:
        cli.main(["backtest"])  # no --strategy/--start/--end
    assert exc.value.code == 2


def test_main_surfaces_a_clean_error_not_a_traceback(monkeypatch):
    lab = _lab()
    monkeypatch.setattr(cli, "_build_lab", lambda args: lab)
    # Export an unknown run → NotFoundError → main() maps it to exit 1, no traceback.
    rc = cli.main(["export", "--run-id", "nope"])
    assert rc == 1
