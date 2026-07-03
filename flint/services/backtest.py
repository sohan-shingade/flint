"""The backtest front door — drives the real engine under a TenantContext (§4, §6).

This is the one place a backtest is orchestrated, and every surface (api/sdk/mcp)
reaches it here, never into the engine or a store (§4, §17). It composes the
pieces the earlier phases built, in one direction only:

* **data** (§9): :class:`~flint.data.DataManager` resolves the run's candles +
  funding + marks over the requested range and applies the **funding hard gate**
  (§6.4). A gap is not an error — it is a structured :class:`Rejection` carrying
  the ranges available and the fix (§19.1), returned in the result body.
* **strategy** (§8.4): the template name resolves through the registry and is
  wrapped by :func:`flint.live.build_adapter` — classic → ``EngineStrategy``,
  ML → ``MLEngineStrategy`` + tenant-scoped ``ModelStore`` + seed. The exact
  runner contract paper uses (5.6), reused here rather than re-implemented.
* **engine** (§6): :class:`~flint.engine.BacktestEngine` walks the candles; after
  the run the adapter's ``drain_rejections()`` / ``tearsheet_notes()`` are drained
  into the result, and the per-bar EQUITY event stream folds into the trust report
  (§11) via :func:`flint.research.build_report`.

The run's durable head is a tenant-scoped ``RunRecord`` (via ``UserDataPort``);
the authoritative per-bar/per-fill history is the event log, folded on read.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from flint.adapters import InMemoryUserData
from flint.data import CoverageMode, DataManager, FundingCoverageError
from flint.data.ranges import Kind, TimeRange
from flint.engine import EngineConfig
from flint.engine.portfolio import EventLog
from flint.live import build_adapter
from flint.ports import RunRecord, TenantContext, UserDataPort
from flint.research import (
    DataSource,
    RunManifest,
    build_report,
    engine_version,
    equity_series_from_events,
)
from flint.strategy.base import StrategyRejection
from flint.strategy.templates.registry import (
    TemplateNotFound,
    TemplateSpec,
    get_template,
)
from flint.venues import HYPERLIQUID

from .errors import Rejection, ValidationError

# Candles + funding are the executable-venue backbone; OI carries real mark/index
# prices when the lake has them (else marks fall back to the candle close).
_KINDS: tuple[Kind, ...] = (Kind.CANDLES, Kind.FUNDING, Kind.OI)


@dataclass(frozen=True)
class BacktestRequest:
    """What to run: a template name over a universe/venue set and a time range."""

    run_id: str
    strategy: str  # template name (registry key)
    universe: tuple[str, ...] = ("SOL-PERP",)
    venues: tuple[str, ...] = (HYPERLIQUID.name,)
    start_ms: int = 0
    end_ms: int = 0
    resolution_s: int = 3600
    fill_mode: str = "auto"
    seed: int = 0
    initial_capital: str = "100000"
    overrides: Mapping[str, Any] = field(default_factory=dict)
    signal_venues: tuple[str, ...] = ()
    # Which simulation substrate to run on (§6.0, D29). "auto" resolves to the
    # legacy bar loop today; N9 flips "auto" to Nautilus once parity is green.
    engine: str = "auto"

    @property
    def range(self) -> TimeRange:
        return TimeRange(self.start_ms, self.end_ms)


@dataclass(frozen=True)
class BacktestOutcome:
    """The head of a finished run: an ``ok`` report or a structured ``rejected``.

    ``verdict`` is the *backtest verdict* (``ok`` | ``rejected``), a separate axis
    from the job-execution state (queued/running/done/…): a funding-gap rejection
    is a run that completed with a "rejected" verdict, carried as data (§19.1).
    """

    run_id: str
    verdict: str  # "ok" | "rejected"
    summary: Mapping[str, Any]
    rejection: Rejection | None = None


@dataclass(frozen=True)
class _Run:
    """Internal engine-run result before persistence (shared by the WF runner)."""

    summary: dict[str, Any]
    equity: list[float]


def run_backtest(
    tenant: TenantContext,
    request: BacktestRequest,
    *,
    user_data: UserDataPort,
    data: DataManager,
    now_ms: int = 0,
) -> BacktestOutcome:
    """Run ``request`` for ``tenant`` end-to-end and persist its head record.

    Raises :class:`ValidationError` for an unknown template (user error). A
    funding hard-gate gap returns a ``verdict="rejected"`` outcome (data, not an
    error). Otherwise returns ``verdict="ok"`` with the trust report in the
    summary. The run is always recorded and closed — never left dangling.
    """
    if not _template_exists(request.strategy):
        raise ValidationError(
            f"unknown strategy template {request.strategy!r}",
            detail="strategy must be a registered template name",
            hint="call list_universe/list_templates for valid names",
        )

    user_data.save_run(
        tenant,
        RunRecord(
            run_id=request.run_id, kind="backtest", status="running", created_ts=now_ms
        ),
    )

    try:
        run = _execute(tenant, request, data=data, event_store=user_data)
    except FundingCoverageError as exc:
        rejection = _rejection_from_gate(exc)
        manifest = _manifest(request, now_ms=now_ms, note=f"rejected: {rejection.code}")
        summary = {
            **manifest.to_summary(),
            "verdict": "rejected",
            **rejection.to_payload(),
        }
        _persist(user_data, tenant, request.run_id, now_ms, summary)
        return BacktestOutcome(request.run_id, "rejected", summary, rejection)

    manifest = _manifest(
        request,
        now_ms=now_ms,
        effective=run.summary["effective_range"],
        metrics=run.summary["metrics"],
        fidelity_lines=run.summary["fidelity_lines"],
    )
    summary = {**manifest.to_summary(), **run.summary}
    _persist(user_data, tenant, request.run_id, now_ms, summary)
    return BacktestOutcome(request.run_id, "ok", summary)


def make_backtest_runner(
    tenant: TenantContext,
    *,
    data: DataManager,
    strategy: str,
    universe: Sequence[str],
    venues: Sequence[str],
    resolution_s: int = 3600,
    initial_capital: str = "100000",
    seed: int = 0,
) -> Callable[[Mapping[str, Any], int, int], float]:
    """Production wiring for ``research.walkforward``'s injected ``BacktestRunner``.

    Returns a ``(params, start, end) -> float`` closure that runs a real backtest
    for ``tenant`` over ``[start, end)`` with ``params`` as strategy overrides and
    scores it by (in-sample/OOS) Sharpe. Trial event logs go to a throwaway store
    so walk-forward search never pollutes the tenant's Run Library.
    """

    def runner(params: Mapping[str, Any], start: int, end: int) -> float:
        request = BacktestRequest(
            run_id=f"wf-{start}-{end}",
            strategy=strategy,
            universe=tuple(universe),
            venues=tuple(venues),
            start_ms=start,
            end_ms=end,
            resolution_s=resolution_s,
            seed=seed,
            initial_capital=initial_capital,
            overrides=dict(params),
        )
        run = _execute(tenant, request, data=data, event_store=InMemoryUserData())
        return float(run.summary["metrics"]["sharpe"])

    return runner


# -- internals ---------------------------------------------------------------


def _template_exists(name: str) -> bool:
    try:
        get_template(name)
    except TemplateNotFound:
        return False
    return True


def _execute(
    tenant: TenantContext,
    request: BacktestRequest,
    *,
    data: DataManager,
    event_store: UserDataPort,
    adapter_spec: "TemplateSpec | None" = None,
) -> _Run:
    """Resolve data (funding-gated), run the engine, fold the trust report.

    Import-local to keep this module's import graph shallow at load time. Each
    phase is wall-clock timed so the tearsheet can answer "why is my backtest
    slow" (§19.2). The split is at the granularity the front door can observe:
    ``engine_run`` is the whole per-bar loop (fills + strategy + funding +
    liquidation), which is one opaque call into the engine — a finer fill/funding
    split awaits engine-internal counters and is not fabricated here (D26).
    """
    from flint.engine.api import EngineRunSpec
    from flint.engine.select import engine_for

    from ._engine_inputs import build_engine_feed

    timing: dict[str, float] = {}
    executable = set(request.venues)
    with _timed(timing, "data_fetch_ms"):
        prepared = data.prepare(
            request.universe,
            request.venues,
            _KINDS,
            request.range,
            mode=CoverageMode.STRICT,  # funding hard gate (§6.4); raises on a gap
            signal_venues=request.signal_venues,
        )
    with _timed(timing, "input_build_ms"):
        # Bar lane: close-derived mark synthesis is the OHLCV-only path today, so the
        # feed carries it explicitly (§6.0 mark_policy) — same marks as before.
        feed = build_engine_feed(
            prepared.tables, executable, mark_policy="close_derived"
        )

    # A prebuilt spec (a compiled *user* strategy, §13.2) resolves directly;
    # otherwise ``request.strategy`` is a trusted template registry name. Either
    # way ``build_adapter`` applies the runner contract (§8.4) — the untrusted
    # path's boundary is the sandbox screen run *before* we ever reach here.
    adapter = build_adapter(
        adapter_spec if adapter_spec is not None else request.strategy,
        tenant,
        seed=request.seed,
        strategy_id=request.run_id,
        overrides=dict(request.overrides),
    )

    log = EventLog(event_store, tenant, request.run_id)
    spec = EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        initial_capital=request.initial_capital,
        fund_venue=request.venues[0],
        mark_policy="close_derived",
    )
    engine = engine_for(request.engine)()
    with _timed(timing, "engine_run_ms"):
        engine.run(feed, adapter, event_log=log, spec=spec)

    rejections = adapter.drain_rejections()
    notes = adapter.tearsheet_notes()
    with _timed(timing, "report_ms"):
        events = log.read()
        equity = equity_series_from_events(events)
        report = build_report(equity, resolution_s=request.resolution_s, events=events)

    summary = _summary(request, prepared, report, equity, rejections, notes, timing)
    return _Run(summary=summary, equity=equity)


@contextmanager
def _timed(sink: dict[str, float], key: str) -> Iterator[None]:
    """Record the wall-clock ms a phase takes into ``sink[key]`` (§19.2)."""
    start = perf_counter()
    try:
        yield
    finally:
        sink[key] = round((perf_counter() - start) * 1000.0, 3)


def _summary(
    request: BacktestRequest,
    prepared: Any,
    report: Any,
    equity: list[float],
    rejections: list[StrategyRejection],
    notes: list[str],
    timing: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    m = report.metrics
    cost = report.cost
    return {
        "verdict": "ok",
        "strategy": request.strategy,
        "universe": list(request.universe),
        "venues": list(request.venues),
        "fill_mode": request.fill_mode,
        "requested_range": _range(prepared.requested),
        "effective_range": _range(prepared.effective_range),
        "clipped": prepared.clipped,
        "metrics": {
            "sharpe": m.sharpe,
            "annualized_sharpe": m.annualized_sharpe,
            "sortino": m.sortino,
            "annualized_sortino": m.annualized_sortino,
            "max_drawdown": m.max_drawdown,
            "mean_bar_return": m.mean_bar_return,
            "n_returns": m.n_returns,
            "annualization_factor": m.annualization_factor,
            "evaluated_start_ts": m.evaluated_start_ts,
            "evaluated_end_ts": m.evaluated_end_ts,
        },
        # A single un-tuned backtest has no trial family: DSR is n/a with N=0
        # trials, shown honestly by the tearsheet (carry-forward (i)).
        "deflated_sharpe": None,
        "n_trials": report.n_trials,
        "win_rate": report.win_rate,
        "cost": _cost(cost),
        "equity_curve": equity,
        "rejections": [_rej(r) for r in rejections],
        "fidelity_lines": prepared.fidelity.lines(),
        "notes": list(notes),
        # Per-run timing breakdown (§19.2): data fetch / input build / the engine
        # per-bar loop / trust-report fold — the phases the front door can observe.
        "timing": dict(timing or {}),
    }


def _persist(
    user_data: UserDataPort,
    tenant: TenantContext,
    run_id: str,
    now_ms: int,
    summary: dict[str, Any],
) -> None:
    user_data.save_run(
        tenant,
        RunRecord(
            run_id=run_id,
            kind="backtest",
            status="done",
            created_ts=now_ms,
            summary=summary,
        ),
    )


def _manifest(
    request: BacktestRequest,
    *,
    now_ms: int,
    effective: dict[str, int] | None = None,
    metrics: Mapping[str, Any] | None = None,
    fidelity_lines: Sequence[str] = (),
    note: str = "",
    source: str = "",
) -> RunManifest:
    """Build the §11.2 Run-Library head so GET /runs (runlib) can list + compare it.

    Templates carry no user source, so ``strategy_source`` is empty; ``params`` are
    the run's overrides and ``metrics`` come from the tearsheet. The manifest is
    merged with the full result blob under one ``RunRecord.summary`` — ``from_record``
    reads only the manifest keys and ignores the result extras.
    """
    data_manifest = tuple(
        DataSource(
            venue=v,
            market=m,
            kind=kind.value,
            start_ts=effective["start_ms"] if effective else request.start_ms,
            end_ts=effective["end_ms"] if effective else request.end_ms,
        )
        for v in request.venues
        for m in request.universe
        for kind in (Kind.CANDLES, Kind.FUNDING)
    )
    return RunManifest(
        run_id=request.run_id,
        strategy_name=request.strategy,
        strategy_source=source,
        params=dict(request.overrides),
        effective_start_ts=effective["start_ms"] if effective else None,
        effective_end_ts=effective["end_ms"] if effective else None,
        fidelity={"lines": list(fidelity_lines)},
        metrics=dict(metrics or {}),
        engine_version=engine_version(),
        seed=request.seed,
        data_manifest=data_manifest,
        note=note,
        kind="backtest",
        created_ts=now_ms,
    )


def _range(tr: TimeRange) -> dict[str, int]:
    return {"start_ms": tr.start_ms, "end_ms": tr.end_ms}


def _cost(cost: Any) -> dict[str, Any] | None:
    if cost is None:
        return None
    return {
        "funding": float(cost.funding),
        "trading_pnl": float(cost.trading_pnl),
        "fees": float(cost.fees),
        "slippage": float(cost.slippage_cost),
        "funding_settlements": cost.funding_settlements,
    }


def _rej(r: StrategyRejection) -> dict[str, Any]:
    return {
        "reason": r.reason,
        "detail": r.detail,
        "ts": r.ts,
        "venue": r.signal.venue,
        "market": r.signal.market,
        "action": r.signal.action,
    }


def _rejection_from_gate(exc: FundingCoverageError) -> Rejection:
    missing = tuple(leg.label() for leg in exc.gaps)
    available: dict[str, Any] = {}
    for leg, rs in exc.available.items():
        bounds = rs.bounds()
        available[leg.label()] = (
            {"start_ms": bounds.start_ms, "end_ms": bounds.end_ms}
            if bounds is not None
            else None
        )
    return Rejection(
        code="funding_gap",
        message="Funding data missing — backtest rejected (funding is a hard requirement).",
        missing=missing,
        available=available,
        hint="Re-run over a covered range, or pass a clip-to-coverage mode.",
    )
