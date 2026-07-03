"""The ``flint`` CLI — the everyday drivers over ``services/`` (§12).

``backtest``/``optimize``/``paper``/``live``/``serve`` are the run commands; ``data
coverage`` / ``data cache --warm`` answer "what ranges exist" and warm the on-demand
cache; ``export --run-id`` emits a reproducibility bundle; ``data import-legacy`` lifts a
legacy DuckDB's run metadata into the Run Library (§19.6); ``recorder start`` is the
self-hosted capture. There is **no** required download step — data is fetched on demand
(§12).

Every command drives the domain through an in-process :class:`~flint.sdk.lab.Lab` (and
thus ``services/`` under a :class:`~flint.ports.TenantContext`) — never the engine or a
store directly (§4, §17). Logs are structured JSON with ``--verbose`` for a human mode
(§19.2). The command layer is kept injectable (``lab=``/``runner=``/``out=``) so the whole
surface is unit-tested with fixtures and no network (D26).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any, Callable

from flint.ports import TenantContext

from .lab import Lab
from .logging import configure_logging, get_logger


# -- shared parsing ----------------------------------------------------------


def _csv(value: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in value.split(",") if v.strip())


def _build_lab(args: argparse.Namespace) -> Lab:
    """Build the in-process Lab for a command (local adapters, on-demand data)."""
    tenant = TenantContext(tenant_id=getattr(args, "tenant", None) or "local")
    return Lab(tenant)


def _emit(out: Callable[[str], None], text: str) -> None:
    out(text)


# -- run commands ------------------------------------------------------------


def cmd_backtest(args: argparse.Namespace, *, lab: Lab | None = None, out=print) -> int:
    lab = lab or _build_lab(args)
    log = get_logger("backtest", tenant=lab.tenant.tenant_id)
    result = lab.backtest(
        args.strategy,
        universe=_csv(args.universe),
        venues=_csv(args.venues),
        start_ms=args.start,
        end_ms=args.end,
        resolution_s=args.resolution,
        seed=args.seed,
        initial_capital=args.capital,
        overrides=_params_to_overrides(args.param),
        signal_venues=_csv(args.signal_venues) if args.signal_venues else (),
    )
    log.info(
        "backtest complete",
        extra={"fields": {"run_id": result.run_id, "verdict": result.verdict}},
    )
    if args.json:
        _emit(out, json.dumps(result.summary, sort_keys=True, default=str))
    else:
        _emit(out, result.tearsheet())
    return 0 if not result.rejected else 3


def cmd_optimize(args: argparse.Namespace, *, lab: Lab | None = None, out=print) -> int:
    lab = lab or _build_lab(args)
    log = get_logger("optimize", tenant=lab.tenant.tenant_id)
    if not args.param:
        _emit(out, "error: optimize needs at least one --param name=lo:hi[:step]")
        return 2
    result = lab.optimize(
        args.strategy,
        params=list(args.param),
        universe=_csv(args.universe),
        venues=_csv(args.venues),
        start_ms=args.start,
        end_ms=args.end,
        resolution_s=args.resolution,
        n_windows=args.windows,
        n_trials=args.trials,
        purge_bars=args.purge,
        embargo_bars=args.embargo,
        label_horizon_bars=args.label_horizon,
        seed=args.seed,
        initial_capital=args.capital,
    )
    log.info(
        "optimize complete",
        extra={
            "fields": {
                "run_id": result.run_id,
                "trials": result.summary.get("n_trials"),
            }
        },
    )
    if args.json:
        _emit(out, json.dumps(result.summary, sort_keys=True, default=str))
    else:
        _emit(out, result.tearsheet())
    return 0


def cmd_paper(args: argparse.Namespace, *, lab: Lab | None = None, out=print) -> int:
    lab = lab or _build_lab(args)
    log = get_logger("paper", tenant=lab.tenant.tenant_id)
    handle = lab.paper(
        args.strategy,
        market=args.market,
        resolution_s=args.resolution,
        initial_capital=args.capital,
        seed=args.seed,
        overrides=_params_to_overrides(args.param),
    )
    log.info("paper session started", extra={"fields": {"run_id": handle.run_id}})
    _emit(
        out,
        f"paper session {handle.run_id} started (market {args.market}).\n"
        f"monitor it live via `flint serve` → WS /api/v1/paper/{handle.run_id}/stream.",
    )
    return 0


def cmd_live(
    args: argparse.Namespace,
    *,
    lab: Lab | None = None,
    secrets=None,
    client_factory=None,
    out=print,
) -> int:
    """The HL live executor entry (D20, §3.6) — caps enforced, kill switch armed.

    ``--stop`` is the kill switch: ``--all`` stops every running live run for the
    tenant, ``--run-id <id>`` stops one; ``--flatten`` also closes open positions.
    Starting a run **requires** ``--max-position-usd`` (the D20 cap) and a venue
    signing key resolved from the ``SecretsPort`` — it refuses (exit 2) without
    either, never placing an order. The executor's logic (order state machine,
    caps, reconciliation, kill switch) is real and persisted; the continuous
    market-data feed loop and the real HL order transport are the v1.x deferrals
    (docs/redesign/STATUS.md), so this command arms the run but places no order.
    """
    from flint.adapters import EnvSecrets
    from flint.live import LiveCaps, LiveExecutor, LiveStartRefused, stop_all_live
    from flint.services import new_run_id
    from flint.venues.hyperliquid import hyperliquid_client_factory

    lab = lab or _build_lab(args)
    secrets = secrets or EnvSecrets()
    factory = client_factory or hyperliquid_client_factory

    if args.stop:
        if args.all:
            reports = stop_all_live(
                lab.tenant,
                store=lab.user_data,
                secrets=secrets,
                client_factory=factory,
                flatten=args.flatten,
            )
            _emit(
                out,
                json.dumps(
                    {"stopped": [r.to_payload() for r in reports]},
                    sort_keys=True,
                    default=str,
                ),
            )
            return 0
        if args.run_id:
            try:
                executor = LiveExecutor.resume(
                    tenant=lab.tenant,
                    store=lab.user_data,
                    secrets=secrets,
                    run_id=args.run_id,
                    client_factory=factory,
                )
            except KeyError:
                _emit(out, f"no live run {args.run_id!r} for this tenant.")
                return 1
            except LiveStartRefused as refused:
                _emit(out, refused.message)
                return 1
            report = executor.stop(flatten=args.flatten)
            _emit(out, json.dumps(report.to_payload(), sort_keys=True, default=str))
            return 0
        _emit(out, "pass --all to stop every live run, or --run-id <id> to stop one.")
        return 2

    if args.max_position_usd is None:
        _emit(
            out,
            "refusing to start live: --max-position-usd is required (D20 safety cap).",
        )
        return 2

    caps = LiveCaps(
        max_position_usd=args.max_position_usd,
        max_daily_loss_usd=args.max_daily_loss_usd,
    )
    run_id = args.run_id or new_run_id()
    log = get_logger("live", run_id=run_id, tenant=lab.tenant.tenant_id)
    try:
        LiveExecutor.start(
            tenant=lab.tenant,
            store=lab.user_data,
            secrets=secrets,
            run_id=run_id,
            market=args.market,
            caps=caps,
            client_factory=factory,
        )
    except LiveStartRefused as refused:
        _emit(
            out,
            refused.message + (f" ({refused.hint})" if refused.hint else ""),
        )
        return 2
    log.info("live run armed", extra={"fields": {"run_id": run_id}})
    daily = (
        f", max daily loss ${args.max_daily_loss_usd}"
        if args.max_daily_loss_usd is not None
        else ""
    )
    _emit(
        out,
        f"live run {run_id} armed on {args.market} "
        f"(max position ${args.max_position_usd}{daily}).\n"
        f"caps + kill switch enforced; stop with `flint live --stop --run-id {run_id}`.\n"
        "the market-data feed loop and real HL order transport land in v1.x "
        "(docs/redesign/STATUS.md); no order was placed by this command.",
    )
    return 0


def cmd_serve(
    args: argparse.Namespace,
    *,
    lab: Lab | None = None,
    runner: Callable[[Any, str, int], None] | None = None,
    out=print,
) -> int:
    """Start the local API, print the per-session token, warn on a non-localhost bind."""
    from flint.api import create_app
    from flint.api.security import generate_token

    lab = lab or _build_lab(args)
    token = generate_token()
    localhost = args.host in ("127.0.0.1", "localhost", "::1")
    if not localhost:
        _emit(
            out,
            f"WARNING: binding to {args.host} exposes a code-executing server beyond "
            "localhost. Only do this on a trusted network; the bearer token below is "
            "the sole guard.",
        )
    app = create_app(
        user_data=lab.user_data,
        data=lab.data,
        tenant=lab.tenant,
        token=token,
    )
    _emit(out, f"flint API on http://{args.host}:{args.port}")
    _emit(out, f"session token: {token}")
    _emit(out, "pass it as `Authorization: Bearer <token>` (or ?token= for the WS).")
    run = runner or _uvicorn_run
    run(app, args.host, args.port)
    return 0


# -- data commands -----------------------------------------------------------


def cmd_data_coverage(
    args: argparse.Namespace, *, lab: Lab | None = None, out=print
) -> int:
    from flint.services import data_coverage

    lab = lab or _build_lab(args)
    payload = data_coverage(data=lab.data, market=args.market, venue=args.venue)
    _emit(out, json.dumps(payload, sort_keys=True, default=str))
    return 0


def cmd_data_cache(
    args: argparse.Namespace, *, lab: Lab | None = None, out=print
) -> int:
    from flint.data.ranges import Kind
    from flint.services import pull_data

    if not args.warm:
        _emit(out, "nothing to do: pass --warm to prefetch a range into the cache.")
        return 0
    lab = lab or _build_lab(args)
    payload = pull_data(
        data=lab.data,
        market=args.market,
        venues=_csv(args.venues),
        kind=Kind(args.kind),
        start_ms=args.start,
        end_ms=args.end,
    )
    _emit(out, json.dumps(payload, sort_keys=True, default=str))
    return 0


def cmd_data_import_legacy(
    args: argparse.Namespace, *, lab: Lab | None = None, out=print
) -> int:
    from flint.services import import_legacy_runs

    lab = lab or _build_lab(args)
    log = get_logger("import-legacy", tenant=lab.tenant.tenant_id)
    run_ids = import_legacy_runs(lab.tenant, user_data=lab.user_data, path=args.path)
    log.info("legacy import complete", extra={"fields": {"imported": len(run_ids)}})
    _emit(out, json.dumps({"imported": run_ids}, sort_keys=True))
    return 0


def cmd_export(args: argparse.Namespace, *, lab: Lab | None = None, out=print) -> int:
    from flint.services import export_run

    lab = lab or _build_lab(args)
    bundle_json = export_run(lab.tenant, args.run_id, user_data=lab.user_data)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(bundle_json)
        _emit(out, f"wrote reproducibility bundle for {args.run_id} to {args.out}")
    else:
        _emit(out, bundle_json)
    return 0


def cmd_recorder_start(args: argparse.Namespace, *, out=print) -> int:
    """Describe the self-hosted capture (a foreground live process; §12/§20).

    Recording is niche self-hosted capture of a live venue feed — an always-on
    foreground process. This command validates the capture config and reports the plan;
    the live connection itself requires a real venue websocket and is not exercised in
    the mocked test suite (D26).
    """
    plan = {
        "action": "recorder.start",
        "venue": args.venue,
        "market": args.market,
        "resolution_s": args.resolution,
        "note": "foreground live capture; connects to the venue websocket on run.",
    }
    _emit(out, json.dumps(plan, sort_keys=True))
    return 0


# -- helpers -----------------------------------------------------------------


def _params_to_overrides(pairs: Sequence[str] | None) -> dict[str, Any]:
    """Parse repeated ``--param name=value`` into a typed overrides dict.

    Values are coerced to int/float where they parse cleanly, else kept as strings —
    so ``--param notional_usd=1000`` reaches the template as a number.
    """
    overrides: dict[str, Any] = {}
    for pair in pairs or ():
        name, sep, raw = pair.partition("=")
        if not sep:
            raise ValueError(f"expected name=value, got {pair!r}")
        overrides[name.strip()] = _coerce(raw.strip())
    return overrides


def _coerce(raw: str) -> Any:
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            continue
    return raw


def _uvicorn_run(app: Any, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)


# -- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flint", description="Flint — perp/DEX lab.")
    parser.add_argument(
        "--verbose", action="store_true", help="human logs (default JSON)"
    )
    parser.add_argument("--tenant", default="local", help="tenant id (default: local)")
    sub = parser.add_subparsers(dest="command", required=True)

    def _range_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--start", type=int, required=True, help="range start (unix ms)")
        p.add_argument("--end", type=int, required=True, help="range end (unix ms)")
        p.add_argument(
            "--resolution", type=int, default=3600, help="bar resolution (s)"
        )

    def _run_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--strategy", required=True, help="template name")
        p.add_argument("--universe", default="SOL-PERP", help="comma-separated markets")
        p.add_argument("--venues", default="hyperliquid", help="comma-separated venues")
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--capital", default="100000", help="initial capital")
        p.add_argument(
            "--param", action="append", default=[], help="name=value override"
        )
        p.add_argument("--json", action="store_true", help="emit the summary JSON")

    p_bt = sub.add_parser("backtest", help="run one backtest")
    _run_args(p_bt)
    _range_args(p_bt)
    p_bt.add_argument("--signal-venues", dest="signal_venues", default="")
    p_bt.set_defaults(func=cmd_backtest)

    p_opt = sub.add_parser("optimize", help="walk-forward parameter search")
    p_opt.add_argument("--strategy", required=True)
    p_opt.add_argument("--universe", default="SOL-PERP")
    p_opt.add_argument("--venues", default="hyperliquid")
    p_opt.add_argument("--seed", type=int, default=0)
    p_opt.add_argument("--capital", default="100000")
    p_opt.add_argument(
        "--param", action="append", default=[], help="name=lo:hi[:step] search range"
    )
    p_opt.add_argument("--windows", type=int, default=3, help="OOS folds")
    p_opt.add_argument("--trials", type=int, default=20, help="Optuna trials per fold")
    p_opt.add_argument("--purge", type=int, default=0, help="purge bars")
    p_opt.add_argument("--embargo", type=int, default=0, help="embargo bars")
    p_opt.add_argument("--label-horizon", dest="label_horizon", type=int, default=1)
    p_opt.add_argument("--json", action="store_true")
    _range_args(p_opt)
    p_opt.set_defaults(func=cmd_optimize)

    p_paper = sub.add_parser("paper", help="start a paper session")
    p_paper.add_argument("--strategy", required=True)
    p_paper.add_argument("--market", required=True)
    p_paper.add_argument("--resolution", type=int, default=3600)
    p_paper.add_argument("--seed", type=int, default=0)
    p_paper.add_argument("--capital", default="100000")
    p_paper.add_argument("--param", action="append", default=[])
    p_paper.set_defaults(func=cmd_paper)

    p_live = sub.add_parser("live", help="HL live executor (D20)")
    p_live.add_argument("--strategy", help="template name")
    p_live.add_argument("--market", help="market")
    p_live.add_argument(
        "--max-position-usd", dest="max_position_usd", type=float, default=None
    )
    p_live.add_argument(
        "--max-daily-loss-usd", dest="max_daily_loss_usd", type=float, default=None
    )
    p_live.add_argument("--run-id", dest="run_id", help="live run id (start or --stop)")
    p_live.add_argument(
        "--stop", action="store_true", help="kill switch: stop live run(s)"
    )
    p_live.add_argument(
        "--all", action="store_true", help="with --stop: stop every running live run"
    )
    p_live.add_argument(
        "--flatten",
        action="store_true",
        help="with --stop: also close open positions (reduce-only)",
    )
    p_live.set_defaults(func=cmd_live)

    p_serve = sub.add_parser("serve", help="start the local API + UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    p_data = sub.add_parser("data", help="data coverage + cache + legacy import")
    data_sub = p_data.add_subparsers(dest="data_command", required=True)

    p_cov = data_sub.add_parser("coverage", help="per-kind covered ranges (no fetch)")
    p_cov.add_argument("--market", required=True)
    p_cov.add_argument("--venue", required=True)
    p_cov.set_defaults(func=cmd_data_coverage)

    p_cache = data_sub.add_parser(
        "cache", help="prefetch a range (optional, offline work)"
    )
    p_cache.add_argument("--warm", action="store_true", help="fetch into the cache")
    p_cache.add_argument("--market", required=True)
    p_cache.add_argument("--venues", default="hyperliquid")
    p_cache.add_argument("--kind", default="candles")
    p_cache.add_argument("--start", type=int, default=0)
    p_cache.add_argument("--end", type=int, default=0)
    p_cache.set_defaults(func=cmd_data_cache)

    p_imp = data_sub.add_parser(
        "import-legacy", help="lift legacy DuckDB runs into the library"
    )
    p_imp.add_argument("--path", required=True, help="path to the legacy .duckdb file")
    p_imp.set_defaults(func=cmd_data_import_legacy)

    p_exp = sub.add_parser("export", help="emit a run's reproducibility bundle")
    p_exp.add_argument("--run-id", dest="run_id", required=True)
    p_exp.add_argument("--out", default=None, help="write to this file (else stdout)")
    p_exp.set_defaults(func=cmd_export)

    p_rec = sub.add_parser("recorder", help="self-hosted capture")
    rec_sub = p_rec.add_subparsers(dest="recorder_command", required=True)
    p_rec_start = rec_sub.add_parser("start", help="start a foreground capture")
    p_rec_start.add_argument("--market", required=True)
    p_rec_start.add_argument("--venue", default="hyperliquid")
    p_rec_start.add_argument("--resolution", type=int, default=3600)
    p_rec_start.set_defaults(func=cmd_recorder_start)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and dispatch. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(getattr(args, "verbose", False))
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001 — surface a clean message, not a traceback
        get_logger("cli").error(str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
