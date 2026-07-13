"""Paper front door + monitor read service (§6.7, §12, §13.2).

Two halves. The *start/resume* functions are the front door for paper sessions —
templates via :func:`start_paper` (trusted registry names, in-process adapter)
and untrusted user **source** via :func:`start_paper_source` /
:func:`resume_paper_source` (the paper counterpart of
``services.run_backtest_source``: validated first, then every engine step runs
inside the OS sandbox, D25). The *monitor* half (:func:`paper_snapshot`) reads
the persisted head back for a tenant — the data behind ``WS /paper/{id}/stream``.
The paper runner (``flint.live.PaperSession``, 5.6) produces that head and
persists it in ``RunRecord.summary`` via the tenant-scoped ``UserDataPort``;
always tenant-scoped so one tenant never streams another's run (§2.7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flint.ports import TenantContext, UserDataPort

from .errors import NotFoundError, ValidationError

if TYPE_CHECKING:
    from flint.data.livefeed import GapSource
    from flint.live import PaperSession, SandboxedPaperSession


def start_paper(
    tenant: TenantContext,
    *,
    user_data: UserDataPort,
    run_id: str,
    strategy: str,
    market: str,
    resolution_s: int = 3600,
    initial_capital: str = "100000",
    seed: int = 0,
    overrides: dict[str, Any] | None = None,
) -> "PaperSession":
    """Start (and persist the head of) a fresh paper session for ``tenant`` (§6.7).

    The paper session is the *same* engine fed live rather than by historical replay
    (the parity promise) — this is the front door the SDK/CLI use to create one. The
    returned :class:`~flint.live.PaperSession` is fed a live-feed connection by the
    caller's run loop; its monitor head is streamed by :func:`paper_snapshot`. Raises
    :class:`ValidationError` for an unknown template (user error), mirroring the
    backtest front door.
    """
    from flint.live import PaperSession
    from flint.strategy.templates.registry import TemplateNotFound, get_template

    try:
        get_template(strategy)
    except TemplateNotFound:
        raise ValidationError(
            f"unknown strategy template {strategy!r}",
            detail="strategy must be a registered template name",
            hint="call list_templates for valid names",
        ) from None

    return PaperSession.create(
        tenant=tenant,
        store=user_data,
        run_id=run_id,
        template=strategy,
        market=market,
        resolution_s=resolution_s,
        initial_capital=initial_capital,
        seed=seed,
        overrides=overrides,
    )


def start_paper_source(
    tenant: TenantContext,
    *,
    user_data: UserDataPort,
    run_id: str,
    source: str,
    market: str,
    resolution_s: int = 3600,
    initial_capital: str = "100000",
    seed: int = 0,
    overrides: dict[str, Any] | None = None,
    gap_source: "GapSource | None" = None,
    **session_kwargs: Any,
) -> "SandboxedPaperSession":
    """Start a paper session over submitted user ``source`` (§13.2, D25).

    The paper counterpart of :func:`~flint.services.run_backtest_source`: the
    sandbox/lint gate (:func:`~flint.services.validate_strategy`) runs first, and
    the returned :class:`~flint.live.SandboxedPaperSession` keeps every engine
    step — each ``feed``/``catch_up`` batch — inside the OS-isolated sandbox
    child, never in this process. An invalid source raises
    :class:`~flint.services.strategy_source.SourceValidationError` carrying the
    structured :class:`ValidationReport` (and nothing is persisted); a
    valid-with-leak-warning report is a warning, not a refusal, readable on the
    returned session as ``session.validation``.

    Pass ``gap_source`` (the lake adapter) to enable headless
    :meth:`~flint.live.PaperSession.catch_up` ticks — a poll-driven scheduler
    advances the session without ever holding a WS pump.
    """
    from flint.live import SandboxedPaperSession

    class_name, report = _validated_source_class(source)
    session = SandboxedPaperSession.create(
        tenant=tenant,
        store=user_data,
        run_id=run_id,
        source=source,
        class_name=class_name,
        market=market,
        resolution_s=resolution_s,
        initial_capital=initial_capital,
        seed=seed,
        overrides=overrides,
        gap_source=gap_source,
        **session_kwargs,
    )
    session.validation = report
    return session


def resume_paper_source(
    tenant: TenantContext,
    *,
    user_data: UserDataPort,
    run_id: str,
    source: str,
    market: str,
    resolution_s: int = 3600,
    seed: int = 0,
    overrides: dict[str, Any] | None = None,
    gap_source: "GapSource | None" = None,
    **session_kwargs: Any,
) -> "SandboxedPaperSession":
    """Pick a killed sandboxed-source paper session back up (§6.7).

    Same contract as :meth:`~flint.live.PaperSession.resume` — portfolio state
    folds from the persisted event log, the feed cursor resumes from the last
    processed bar (or the persisted headless anchor) — with the strategy rebuilt
    from ``source`` in the sandbox child on the next step. The source is
    re-validated on every resume (it is caller-held and may have changed since
    the run started); raises the same structured errors as
    :func:`start_paper_source`, and ``KeyError`` if the tenant has no such run.
    """
    from flint.live import SandboxedPaperSession

    class_name, report = _validated_source_class(source)
    session = SandboxedPaperSession.resume(
        tenant=tenant,
        store=user_data,
        run_id=run_id,
        source=source,
        class_name=class_name,
        market=market,
        resolution_s=resolution_s,
        seed=seed,
        overrides=overrides,
        gap_source=gap_source,
        **session_kwargs,
    )
    session.validation = report
    return session


def _validated_source_class(source: str) -> tuple[str, Any]:
    """Gate user ``source`` for a paper session; return (class name, report).

    The identical pre-run gate ``run_backtest_source`` applies — sandbox probe +
    static lint via ``validate_strategy``, class picked out statically (AST, the
    source is never exec'd here) — plus the paper-only rule: the bar lane is the
    only paper lane (N10), so a ``TickStrategy`` subclass is rejected up front.
    """
    from .strategy_source import (
        SourceValidationError,
        _declares_tick_strategy,
        _strategy_class_name,
        validate_strategy,
    )

    # The paper-only lane rule first (a cheap AST look, before any sandbox
    # spawn): a tick-native source gets the precise bar-lane rejection, not a
    # generic validation failure. A syntax error falls through — the validation
    # screen below reports it line-precisely.
    try:
        declares_tick = _declares_tick_strategy(source)
    except SyntaxError:
        declares_tick = False
    if declares_tick:
        raise ValidationError(
            "paper trading runs the bar lane only — a tick-native strategy "
            "(TickStrategy) is not supported in a paper session (N10)",
            detail="tick strategies need native L2 matching, which the paper "
            "LiveFeed does not carry",
            hint="submit a bar Strategy subclass, or backtest the tick strategy "
            "via run_backtest_source",
        )
    report = validate_strategy(source)
    if not report.valid:
        raise SourceValidationError(
            "strategy source failed validation",
            report=report,
            detail="the sandbox/lint gate rejected the source before any run",
            hint="fix the violations in error.validation and resubmit",
        )
    class_name = _strategy_class_name(source)
    if class_name is None:
        raise ValidationError(
            "strategy source defines no Strategy subclass",
            detail="the engine runs a subclass of flint.strategy.Strategy",
            hint="define `class MyStrategy(Strategy): ...`",
        )
    return class_name, report


def paper_snapshot(
    tenant: TenantContext, run_id: str, *, user_data: UserDataPort
) -> dict[str, Any]:
    """The current monitor snapshot for ``tenant``'s paper run ``run_id``.

    Raises :class:`NotFoundError` if the tenant has no such run (absent, or owned
    by another tenant — indistinguishable by design, §2.7).
    """
    try:
        record = user_data.load_run(tenant, run_id)
    except KeyError:
        raise NotFoundError(f"unknown paper run {run_id!r}") from None
    summary = dict(record.summary)
    return {
        "run_id": record.run_id,
        "kind": record.kind,
        "status": record.status,
        "positions": summary.get("positions", []),
        "funding_accrued": summary.get("funding_accrued"),
        "liq_distances_pct": summary.get("liq_distances_pct", {}),
        "drift": summary.get("drift", {}),
        "alerts": summary.get("alerts", []),
        "final_equity": summary.get("final_equity"),
    }
