"""User-strategy-source services — validate + backtest submitted code (§13, D25).

This is the front door for the agent loop (§13.1): an agent authors Python
strategy *source* (untrusted), Flint validates it, backtests it, and hands back
structured results the agent reasons over. Templates (7.1's ``run_backtest``) are
trusted built-ins keyed by registry name; this module adds the **user-source**
path carry-forward (n) requires — its own services entry that sandboxes and
lint-screens submitted code *before* any engine run.

The boundary (D25, §8.3): every user-source run is gated by
:func:`validate_strategy`, which runs the OS-isolated sandbox
(``run_strategy_sandboxed(..., screen=True)``) plus the static leak lint
(``research.lookahead.analyze``) and returns **structured, line-precise errors
before a backtest ever starts**. The static AST screen is lint-grade UX; the
subprocess is the actual containment. But validation only probes a single entry —
a strategy can behave there and misbehave once the engine walks it — so the OS
boundary must contain the *whole* run, not just validation. A validated strategy
is therefore walked by the honest engine (§6) **inside the sandbox**
(:func:`_run_source_in_sandbox` → ``run_backtest_in_sandbox``): data resolution,
the funding gate, persistence, and the trust-report fold stay on this trusted
side, and only inert input copies cross to the untrusted engine walk. In-process
execution remains solely for trusted registry templates (``services.backtest``).
The truncation probe's ``feature_fn`` likewise runs **inside** the sandbox
(carry-forward (d)), so a look-ahead re-run never executes untrusted code
in-process either.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from flint.data import (
    CoverageMode,
    DataManager,
    FundingCoverageError,
    GranularityUnavailableError,
)
from flint.engine.portfolio import EventLog
from flint.ports import TenantContext, UserDataPort
from flint.research import (
    analyze,
    build_report,
    equity_points_from_events,
    equity_series_from_events,
)
from flint.strategy.sandbox import (
    SandboxError,
    SandboxTimeout,
    SandboxViolation,
    StrategyError,
    StrategyScreenError,
    run_strategy_sandboxed,
    screen_source,
)
from flint.strategy.sandbox.backtest_runner import run_backtest_in_sandbox

from ._engine_inputs import build_engine_inputs
from .backtest import (
    _KINDS,
    BacktestOutcome,
    BacktestRequest,
    _manifest,
    _persist,
    _rejection_from_exc,
    _stamp_engine,
    _summary,
    _timed,
    validate_engine,
)
from .errors import ValidationError

# A tiny, hand-authored candle frame (D26 — a fixed unit input, never generated
# market-like data). Four bars with distinct closes so a full-window
# normalization leak actually diverges when the last bar is truncated (§8.5).
_PROBE_CLOSES = (100.0, 103.0, 99.0, 102.0)
_PROBE_MARKET = "SOL-PERP"
_PROBE_VENUE = "hyperliquid"
_PROBE_H = 3_600_000


def _probe_frame() -> list[dict[str, Any]]:
    """The hand-authored candle rows the validation sandbox probe runs over."""
    return [
        {
            "ts": i * _PROBE_H,
            "open": c,
            "high": c + 1.0,
            "low": c - 1.0,
            "close": c,
            "volume": 1000.0,
            "market": _PROBE_MARKET,
            "resolution_s": 3600,
            "venue": _PROBE_VENUE,
        }
        for i, c in enumerate(_PROBE_CLOSES)
    ]


# The validation harness appended to user source before the sandbox runs it. It
# exposes one stable entry, ``_flint_features(frame)``, dispatching to the
# author's ``features`` seam (the leak-prone §8.5 surface the truncation probe
# targets) or, failing that, a ``Strategy`` subclass walked causally. Everything
# here stays inside the sandbox allowlist (flint imports, safe builtins) so the
# combined source still passes ``screen=True``.
_HARNESS_TEMPLATE = '''

def _flint_net_signal(_sigs):
    if _sigs is None:
        return 0.0
    if not isinstance(_sigs, list):
        _sigs = [_sigs]
    _net = 0.0
    for _s in _sigs:
        _sz = float(getattr(_s, "size_usd", 0.0)) or float(getattr(_s, "size", 0.0))
        _act = getattr(_s, "action", "")
        if _act == "long":
            _net += _sz
        elif _act == "short":
            _net -= _sz
    return _net


class _FlintProbeCtx:
    """A read-only ctx stub for the validation probe — carries no engine state."""

    now = 0

    def position(self, *args, **kwargs):
        return None

    def equity(self, *args, **kwargs):
        return 0.0


def _flint_features(_frame):
    from flint.core.models import Candle

    _rows = [Candle(**_r) for _r in _frame]
{dispatch}
'''

_DISPATCH_FEATURES = "    return list(features(_rows))\n"

_DISPATCH_STRATEGY = """    _strat = {name}()
    _ctx = _FlintProbeCtx()
    _out = []
    for _i in range(len(_rows)):
        _out.append(_flint_net_signal(_strat.on_candle(_rows[_i], _rows[: _i + 1], _ctx)))
    return _out
"""


@dataclass(frozen=True)
class ValidationReport:
    """The structured verdict of :func:`validate_strategy` (§13.2/§13.3).

    ``valid`` means the source passed the static screen and ran cleanly in the
    sandbox — it is safe to backtest. ``leak_detected`` is reported separately: a
    look-ahead finding is a warning an agent should act on, not a reason the run
    cannot execute. Every field is JSON-safe so an agent never parses prose.
    """

    valid: bool
    stage: str  # "ok" | "screen" | "sandbox"
    screen_violations: tuple[dict[str, Any], ...] = ()
    sandbox_error: dict[str, Any] | None = None
    leak_detected: bool = False
    lookahead_summary: str = ""
    lookahead_findings: tuple[dict[str, Any], ...] = ()
    checks_run: tuple[str, ...] = ()
    blind_spots: tuple[str, ...] = ()
    dynamic_ran: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "stage": self.stage,
            "screen_violations": list(self.screen_violations),
            "sandbox_error": self.sandbox_error,
            "leak_detected": self.leak_detected,
            "lookahead": {
                "summary": self.lookahead_summary,
                "findings": list(self.lookahead_findings),
                "checks_run": list(self.checks_run),
                "blind_spots": list(self.blind_spots),
                "dynamic_probe_ran": self.dynamic_ran,
            },
        }


def validate_strategy(source: str) -> ValidationReport:
    """Sandbox + static-lint a user strategy — structured errors *before* a run.

    Order (fail-fast): (1) the static AST/import screen returns line-precise
    violations; if any, we stop — nothing runs. (2) the OS-isolated sandbox runs
    the strategy over a hand-authored probe frame (``screen=True``), surfacing a
    runtime error as a structured ``sandbox_error`` rather than a stack trace. (3)
    the look-ahead lint (static AST pass + the truncation probe, whose
    ``feature_fn`` executes *inside* the sandbox) reports leaks with the honest
    "no leak detected" wording and its blind spots (carry-forwards (c)/(d)/(n)).
    """
    violations = screen_source(source)
    if violations:
        return ValidationReport(
            valid=False,
            stage="screen",
            screen_violations=tuple(_violation(v) for v in violations),
        )

    entry = _dispatch_for(source)
    if entry is None:
        # Nothing runnable to sandbox — static lint only, said plainly (§13.3).
        result = analyze(source=source)
        return ValidationReport(
            valid=True,
            stage="ok",
            leak_detected=result.leak_detected,
            lookahead_summary=result.summary(),
            lookahead_findings=tuple(_finding(f) for f in result.findings),
            checks_run=result.checks_run,
            blind_spots=result.blind_spots,
            dynamic_ran=False,
        )

    combined = source + _HARNESS_TEMPLATE.format(dispatch=entry)
    frame = _probe_frame()

    def _sandboxed_features(rows: Sequence) -> Sequence:
        return run_strategy_sandboxed(
            combined, "_flint_features", list(rows), screen=True
        )

    # Liveness first: one full-frame run in the sandbox catches import/runtime
    # errors deterministically before the truncation probe re-runs it.
    try:
        _sandboxed_features(frame)
    except (
        StrategyError,
        SandboxTimeout,
        SandboxViolation,
        StrategyScreenError,
        SandboxError,
    ) as exc:
        return ValidationReport(
            valid=False, stage="sandbox", sandbox_error=_sandbox_error(exc)
        )

    result = analyze(source=source, feature_fn=_sandboxed_features, frame=frame)
    return ValidationReport(
        valid=True,
        stage="ok",
        leak_detected=result.leak_detected,
        lookahead_summary=result.summary(),
        lookahead_findings=tuple(_finding(f) for f in result.findings),
        checks_run=result.checks_run,
        blind_spots=result.blind_spots,
        dynamic_ran=True,
    )


def run_backtest_source(
    tenant: TenantContext,
    *,
    source: str,
    run_id: str,
    universe: Sequence[str] = ("SOL-PERP",),
    venues: Sequence[str] = ("hyperliquid",),
    start_ms: int = 0,
    end_ms: int = 0,
    resolution_s: int = 3600,
    fill_mode: str = "auto",
    seed: int = 0,
    initial_capital: str = "100000",
    overrides: Mapping[str, Any] | None = None,
    signal_venues: Sequence[str] = (),
    granularity: str = "auto",
    engine: str = "auto",
    user_data: UserDataPort,
    data: DataManager,
    now_ms: int = 0,
) -> BacktestOutcome:
    """Validate + backtest submitted user ``source`` end-to-end (§13.2, carry (n)).

    The sandbox/lint gate runs first; an invalid strategy returns a
    ``verdict="invalid"`` outcome carrying the :class:`ValidationReport` — data
    the agent revises against, never an exception. A funding hard-gate gap returns
    ``verdict="rejected"`` (data, §19.1). Otherwise the validated strategy is
    walked by the real engine **inside the OS sandbox** (D25 closure — the boundary
    contains the whole run, not just pre-run validation), and the run is persisted
    like any other (its Run-Library head carries the actual source for reproduction).
    A strategy that passed validation but misbehaves during the run is contained and
    surfaced as ``verdict="invalid"`` with a structured ``sandbox_error`` — never a
    stack trace, and nothing is persisted.
    """
    validate_engine(engine)  # unknown engine → uniform §19.1 error, before anything runs
    report = validate_strategy(source)
    if not report.valid:
        summary = {
            "verdict": "invalid",
            "run_id": run_id,
            "validation": report.to_payload(),
        }
        return BacktestOutcome(run_id, "invalid", summary)

    # The engine walk of the validated (but still untrusted) source runs INSIDE the
    # OS sandbox (D25 closure) — never in this process. The class is picked out
    # statically (AST) so we never exec the source here just to name it.
    class_name = _strategy_class_name(source)
    if class_name is None:
        raise ValidationError(
            "strategy source defines no Strategy subclass",
            detail="the engine runs a subclass of flint.strategy.Strategy",
            hint="define `class MyStrategy(Strategy): ...`",
        )

    request = BacktestRequest(
        run_id=run_id,
        strategy=class_name,
        universe=tuple(universe),
        venues=tuple(venues),
        start_ms=start_ms,
        end_ms=end_ms,
        resolution_s=resolution_s,
        fill_mode=fill_mode,
        seed=seed,
        initial_capital=initial_capital,
        overrides=dict(overrides or {}),
        signal_venues=tuple(signal_venues),
        granularity=granularity,
        engine=engine,
    )

    # No "running" head is written up front: an invalid verdict (validation OR a
    # contained runtime failure) must persist nothing, matching the pre-run gate.
    # The success and funding-rejection paths create the closed record via _persist.
    try:
        run_summary = _run_source_in_sandbox(
            tenant,
            request,
            source=source,
            class_name=class_name,
            data=data,
            user_data=user_data,
        )
    except (FundingCoverageError, GranularityUnavailableError) as exc:
        rejection = _rejection_from_exc(exc)
        manifest = _manifest(
            request, now_ms=now_ms, note=f"rejected: {rejection.code}", source=source
        )
        summary = {
            **manifest.to_summary(),
            "verdict": "rejected",
            "validation": report.to_payload(),
            **rejection.to_payload(),
        }
        _persist(user_data, tenant, run_id, now_ms, summary)
        return BacktestOutcome(run_id, "rejected", summary, rejection)
    except (StrategyError, SandboxTimeout, SandboxViolation, SandboxError) as exc:
        # Passed pre-run validation but misbehaved (or blew a limit) DURING the real
        # run: the OS boundary contained it (D25). Surface it as the same structured,
        # stack-trace-free data an invalid strategy yields (§19.1) — nothing persisted.
        failed = ValidationReport(
            valid=False, stage="sandbox", sandbox_error=_sandbox_error(exc)
        )
        summary = {
            "verdict": "invalid",
            "run_id": run_id,
            "validation": failed.to_payload(),
        }
        return BacktestOutcome(run_id, "invalid", summary)

    manifest = _manifest(
        request,
        now_ms=now_ms,
        effective=run_summary["effective_range"],
        metrics=run_summary["metrics"],
        fidelity_lines=run_summary["fidelity_lines"],
        source=source,
    )
    summary = {
        **manifest.to_summary(),
        **run_summary,
        "validation": report.to_payload(),
    }
    _persist(user_data, tenant, run_id, now_ms, summary)
    return BacktestOutcome(run_id, "ok", summary)


def _run_source_in_sandbox(
    tenant: TenantContext,
    request: BacktestRequest,
    *,
    source: str,
    class_name: str,
    data: DataManager,
    user_data: UserDataPort,
) -> dict[str, Any]:
    """Resolve data (funding-gated), run the engine IN the sandbox, fold the report.

    The D25-closing counterpart to ``services.backtest._execute``: data resolution,
    the funding hard gate, event persistence, and the trust-report fold all stay on
    the trusted side, while the untrusted engine walk happens in an OS-isolated
    child (:func:`run_backtest_in_sandbox`). Only inert input copies cross the
    boundary; the returned events are re-persisted and folded exactly as the
    in-process template path does, so the two paths are bit-for-bit identical.
    """
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
            granularity=request.granularity,
        )
    with _timed(timing, "input_build_ms"):
        inputs = build_engine_inputs(prepared.tables, executable)

    # Resolve the substrate before the child spawns (§6.0): the child receives a
    # concrete name (never "auto") and dispatches through the same engine/select.py
    # seam as the in-process template path; the engine-aware default quota (§8.3,
    # measured in N7) is picked inside run_backtest_in_sandbox from this name.
    from flint.engine.select import resolve_engine_name

    resolved_engine = resolve_engine_name(request.engine)
    if resolved_engine != "nautilus" and _declares_tick_strategy(source):
        raise ValidationError(
            f"a tick-native strategy requires engine='nautilus', not "
            f"{resolved_engine!r}",
            detail="tick strategies use native L2 matching, which only the Nautilus "
            "core provides",
            hint="pass engine='nautilus' (or 'auto', which resolves to it)",
        )
    with _timed(timing, "engine_run_ms"):
        sandboxed = run_backtest_in_sandbox(
            source,
            class_name,
            candles=inputs.candles,
            funding=inputs.funding,
            marks=inputs.marks,
            seed=request.seed,
            initial_capital=request.initial_capital,
            fund_venue=request.venues[0],
            run_id=request.run_id,
            overrides=dict(request.overrides),
            engine=resolved_engine,
        )

    # Persist the sandbox's event stream on the trusted side, then fold the report
    # from the persisted log — identical to the in-process path, so the tearsheet
    # and the per-trade log (get_results) read the exact same events.
    user_data.append_events(tenant, request.run_id, sandboxed.events)
    with _timed(timing, "report_ms"):
        events = EventLog(user_data, tenant, request.run_id).read()
        equity = equity_series_from_events(events)
        report = build_report(equity, resolution_s=request.resolution_s, events=events)

    equity_curve = equity_points_from_events(events)
    summary = _summary(
        request, prepared, report, equity_curve, [], sandboxed.notes, timing
    )
    summary["rejections"] = sandboxed.rejections
    # Attribution (§19.4/§19.6): the child reports the substrate that actually ran
    # (it resolved through the same select.py seam) and the exact Nautilus pin.
    _stamp_engine(summary, sandboxed.engine, sandboxed.engine_versions or None)
    return summary


# -- internals ---------------------------------------------------------------


def _dispatch_for(source: str) -> str | None:
    """The harness dispatch body for ``source``, or ``None`` if nothing runnable.

    Prefers a module-level ``features`` function (the §8.5 leak seam); else a
    ``Strategy`` subclass walked bar-by-bar. Both are referenced by name so the
    harness never needs ``globals()`` (a forbidden sandbox builtin).
    """
    if _has_features(source):
        return _DISPATCH_FEATURES
    name = _strategy_class_name(source)
    if name is not None:
        return _DISPATCH_STRATEGY.format(name=name)
    return None


def _has_features(source: str) -> bool:
    tree = ast.parse(source)
    return any(
        isinstance(node, ast.FunctionDef) and node.name == "features"
        for node in tree.body
    )


def _strategy_class_name(source: str) -> str | None:
    """The name of the last module-level class whose bases name ``Strategy``."""
    tree = ast.parse(source)
    found: str | None = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(
            _base_names_strategy(base) for base in node.bases
        ):
            found = node.name
    return found


def _base_names_strategy(base: ast.expr) -> bool:
    if isinstance(base, ast.Name):
        return base.id.endswith("Strategy")
    if isinstance(base, ast.Attribute):
        return base.attr.endswith("Strategy")
    return False


def _base_names_tick_strategy(base: ast.expr) -> bool:
    if isinstance(base, ast.Name):
        return base.id == "TickStrategy"
    if isinstance(base, ast.Attribute):
        return base.attr == "TickStrategy"
    return False


def _declares_tick_strategy(source: str) -> bool:
    """Whether ``source`` declares a ``TickStrategy`` subclass (§8.6 tick-native).

    A tick-native user strategy runs only on the Nautilus core's native L2 lane, so
    the front door must reject it on any other engine (§19.1) before spawning the
    sandbox child. Detection is by base name (``class X(TickStrategy)``), matching how
    :func:`_strategy_class_name` recognizes a bar ``Strategy`` — the sandbox screen
    already parses the same tree, so this adds no import of user code.
    """
    tree = ast.parse(source)
    return any(
        isinstance(node, ast.ClassDef)
        and any(_base_names_tick_strategy(base) for base in node.bases)
        for node in tree.body
    )


def _violation(v: Any) -> dict[str, Any]:
    return {"line": v.line, "col": v.col, "code": v.code, "message": v.message}


def _finding(f: Any) -> dict[str, Any]:
    return {"category": f.category, "message": f.message, "line": f.line}


def _sandbox_error(exc: Exception) -> dict[str, Any]:
    return {"type": type(exc).__name__, "message": str(exc)}
