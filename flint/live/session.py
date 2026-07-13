"""The paper session runner — the same engine, fed live, that survives restart (§6.7).

Paper trading is *not* a second engine; it is the backtest engine fed by the
:class:`~flint.data.livefeed.LiveFeed` instead of historical replay. That is the
parity promise, and this runner keeps it by construction: it assembles the
LiveFeed's bars into an :class:`~flint.engine.api.EngineFeed` and runs them through
the shared engine seam (``engine_for("auto")``, the Nautilus bar lane as of N10),
the identical per-bar loop (fills, funding, liquidation) a backtest runs.

Restart is a **warm start** on that seam (§6.7): the reconstructed portfolio (folded
from the persisted event log) is handed to the engine as ``EngineRunSpec.initial_state``
so each connection resumes from the exact carried book — cash, open positions, and
accumulators — rather than a fresh fund. A connection that closes no bar has nothing
to run, so the engine is skipped and the reconstructed book is the final state.

The runner is where the Phase-5 carry-forwards compose:

* **strategy** (5.1/5.3/5.4): a template name resolves through the registry to a
  class; a classic template is wrapped in ``EngineStrategy``, an ML template in
  ``MLEngineStrategy`` + a tenant-scoped ``ModelStore`` + the run seed. The
  adapter is passed to ``engine.run(strategy=...)`` and its
  ``drain_rejections()`` / ``tearsheet_notes()`` are drained after each run. Paper
  is bar-lane only in N10 — a tick-native strategy is rejected at construction.
* **persistence / restart**: the append-only event log *is* the persisted state
  (via ``UserDataPort``, tenant-scoped). A restart reconstructs the portfolio by
  folding that log — never a stored snapshot — and resumes the LiveFeed cursor
  from the last processed bar. Because a processed bar is never re-fed and the
  fold applies each fill's position delta exactly once, a kill/restart +
  reconnect cannot double-fill (§6.2/§6.7).
* **drift + alerts**: after each run the log is folded into a
  :class:`~flint.live.drift.DriftReport` and the rule engine is evaluated,
  dispatching any structural-drift / liquidation-proximity / funding-flip alert
  to the channel and persisting it in the run summary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from flint.core.models import Signal
from flint.data.ingest.recorders import WsMessageSource
from flint.data.livefeed import (
    GapRecovery,
    GapSource,
    LiveBar,
    LiveFeed,
    assemble_engine_inputs,
)
from flint.engine import EngineConfig, PortfolioState
from flint.engine.api import EngineFeed, EngineRunSpec
from flint.engine.liquidation import liquidation_price
from flint.engine.money import Money, money
from flint.engine.portfolio import EventLog, warm_state
from flint.engine.portfolio.events import EQUITY
from flint.engine.select import engine_for
from flint.ports import ResourceQuota, TenantContext, UserDataPort
from flint.ports.records import RunRecord
from flint.strategy.base import EngineStrategy, StrategyRejection
from flint.strategy.ml import MLEngineStrategy, MLStrategy
from flint.strategy.model_store import ModelStore
from flint.strategy.templates.registry import TemplateSpec, get_template
from flint.strategy.tick import TickEngineStrategy, TickStrategy
from flint.venues import HYPERLIQUID, VenueSpec

from .alerts import (
    Alert,
    AlertChannel,
    AlertContext,
    AlertEngine,
    AlertRule,
    CollectingChannel,
    default_rules,
)
from .drift import DriftReport, SlippageBaseline, build_drift_report


def build_adapter(
    template: TemplateSpec | str,
    tenant: TenantContext,
    *,
    seed: int = 0,
    strategy_id: str | None = None,
    overrides: dict | None = None,
    model_backend=None,
) -> EngineStrategy:
    """Resolve a template to the engine adapter, per the runner contract (§8.4).

    A classic template → ``EngineStrategy``; an ML template (``spec.is_ml``) →
    ``MLEngineStrategy`` with a tenant-scoped ``ModelStore`` and the run seed; a
    tick-native template (a :class:`TickStrategy` subclass) → ``TickEngineStrategy``,
    whose ``lane == "tick"`` routes it onto the Nautilus core's native-matching lane
    (§8.6) — services reject a tick strategy on any other engine (§19.1). The optional
    ``model_backend`` lets a restart share one model store across runs.
    """
    spec = get_template(template) if isinstance(template, str) else template
    strat = spec.strategy_cls(**(overrides or {}))
    if spec.is_ml:
        assert isinstance(strat, MLStrategy)
        store = ModelStore(tenant, strategy_id or spec.name, backend=model_backend)
        return MLEngineStrategy(strat, store, seed=seed)
    if isinstance(strat, TickStrategy):
        return TickEngineStrategy(strat)
    return EngineStrategy(strat)


@dataclass(frozen=True)
class RunResult:
    """What one ``feed`` produced — bars, drains, drift, alerts, final state."""

    bars: list[LiveBar]
    processed: int
    rejections: list[StrategyRejection]
    notes: list[str]
    recoveries: list[GapRecovery]
    drift: DriftReport
    alerts: list[Alert]
    final_state: PortfolioState


class PaperSession:
    """A restart-safe paper run of one strategy over a LiveFeed (§6.7).

    Construct with :meth:`create` for a fresh session or :meth:`resume` to pick a
    prior one back up from its persisted event log.
    """

    def __init__(
        self,
        *,
        tenant: TenantContext,
        store: UserDataPort,
        run_id: str,
        adapter: EngineStrategy | None,
        market: str,
        resolution_s: int,
        venue: str = HYPERLIQUID.name,
        venue_spec: VenueSpec = HYPERLIQUID,
        initial_capital: Money,
        gap_source: GapSource | None = None,
        slippage_baseline: SlippageBaseline | None = None,
        expected_funding: Money | None = None,
        alert_rules: Sequence[AlertRule] | None = None,
        channel: AlertChannel | None = None,
        resume_bar_start: int | None = None,
    ) -> None:
        # Paper runs the bar lane only (N10): a tick-native strategy needs native L2
        # matching, which the paper LiveFeed does not carry — reject it up front rather
        # than fail deep in the engine on the first connection (§8.6/§19.1).
        # ``adapter`` is None only for a subclass that overrides ``_run_engine`` /
        # ``_drain`` (the sandboxed source session, which holds source, not a class).
        if adapter is not None and getattr(adapter, "lane", "bar") == "tick":
            raise ValueError(
                "paper trading runs the bar lane only — a tick-native strategy "
                "(lane='tick') is not supported in a paper session (N10)"
            )
        self._tenant = tenant
        self._store = store
        self._run_id = run_id
        self._adapter = adapter
        self._market = market
        self._venue = venue
        self._spec = venue_spec
        self._resolution_s = resolution_s
        self._initial_capital = initial_capital
        self._baseline = slippage_baseline or SlippageBaseline(0.0, 0.0)
        self._expected_funding = expected_funding
        self._feed = LiveFeed(
            market,
            venue=venue,
            resolution_s=resolution_s,
            gap_source=gap_source,
            resume_bar_start=resume_bar_start,
        )
        self._channel = channel or CollectingChannel()
        self._alerts = AlertEngine(alert_rules or default_rules(), self._channel)
        self._prev_funding_spread: float | None = None

    # -- construction ----------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        tenant: TenantContext,
        store: UserDataPort,
        run_id: str,
        template: TemplateSpec | str,
        market: str,
        resolution_s: int,
        initial_capital: Money | str = "100000",
        seed: int = 0,
        overrides: dict | None = None,
        **kwargs,
    ) -> "PaperSession":
        """Start a fresh paper session and persist its head (§6.2)."""
        cap = money(initial_capital)
        adapter = build_adapter(
            template, tenant, seed=seed, strategy_id=run_id, overrides=overrides
        )
        store.save_run(
            tenant,
            RunRecord(
                run_id=run_id,
                kind="paper",
                status="running",
                summary={"initial_capital": str(cap)},
            ),
        )
        return cls(
            tenant=tenant,
            store=store,
            run_id=run_id,
            adapter=adapter,
            market=market,
            resolution_s=resolution_s,
            initial_capital=cap,
            **kwargs,
        )

    @classmethod
    def resume(
        cls,
        *,
        tenant: TenantContext,
        store: UserDataPort,
        run_id: str,
        adapter: EngineStrategy,
        market: str,
        resolution_s: int,
        **kwargs,
    ) -> "PaperSession":
        """Pick up a killed session: read its head + cursor from the event log.

        The strategy adapter must be rebuilt by the caller (in-memory strategy
        state — e.g. an ML retrain timer — does not persist); the *portfolio*
        state is reconstructed by folding the log on the first ``feed``. The
        LiveFeed cursor resumes at the last processed bar so the next connection
        is a reconnect and cannot re-process a bar.
        """
        record = store.load_run(tenant, run_id)  # KeyError if unknown/other-tenant
        cap = money(record.summary.get("initial_capital", "0"))
        resume_bar_start = cls._resume_cursor(store, tenant, run_id, record)
        return cls(
            tenant=tenant,
            store=store,
            run_id=run_id,
            adapter=adapter,
            market=market,
            resolution_s=resolution_s,
            initial_capital=cap,
            resume_bar_start=resume_bar_start,
            **kwargs,
        )

    @staticmethod
    def _resume_cursor(
        store: UserDataPort, tenant: TenantContext, run_id: str, record: RunRecord
    ) -> int | None:
        """The bar start a restarted session resumes its LiveFeed cursor from.

        The last processed bar is the newest EQUITY event's ts; the persisted
        ``cursor_bar_start`` head field covers the one state the event log cannot:
        a headless session whose first ``catch_up`` anchored the cursor without
        processing a bar (§6.7). The max of the two is always the true cursor —
        every processed bar writes both, so they only diverge on that anchor.
        """
        events = store.load_events(tenant, run_id)
        processed = [int(e.get("ts", 0)) for e in events if e.get("kind") == EQUITY]
        stored = record.summary.get("cursor_bar_start")
        candidates = [
            c for c in (max(processed, default=None), stored) if c is not None
        ]
        return max(candidates) if candidates else None

    # -- the run ---------------------------------------------------------------

    def feed(self, source: WsMessageSource) -> RunResult:
        """Consume one WS connection: aggregate → run the engine → drift + alerts."""
        return self._advance(self._feed.connect(source))

    def catch_up(self, now_ms: int) -> RunResult:
        """Advance the session to venue time ``now_ms`` with no live connection.

        The poll-driven entry point (§6.7): the bars closed since the last
        processed one are replayed from the lake (``LiveFeed.replay_to``, the
        same gap-replay machinery a reconnect uses) and run through the same
        engine loop as a live connection — a headless scheduler can tick a paper
        session forward without ever holding a WS pump. Requires a ``gap_source``.

        One engine contract to plan cadence around: working orders are cancelled
        at run end (``run_ended``, §6.2) and never carry across runs, so a T+1
        intent decided on the *last* bar of a step is re-decided (not filled) on
        the next step. Poll so a step usually spans ≥ 2 closed bars — a
        catch-up cadence equal to the bar resolution never fills anything.
        """
        return self._advance(self._feed.replay_to(now_ms))

    def _advance(self, bars: list[LiveBar]) -> RunResult:
        """Run one batch of closed bars through the engine → drift + alerts."""
        inputs = assemble_engine_inputs(bars)

        state = self._reconstruct_state()
        log = EventLog(self._store, self._tenant, self._run_id)
        if inputs.candles:
            final_state = self._run_engine(inputs, state, log)
        else:
            # A batch that closed no bar has nothing to run (the Nautilus bar lane
            # rejects an empty feed): the reconstructed book is the final state, and
            # drift / alerts / summary below run on the same path as a bar-bearing run.
            final_state = state

        rejections, notes = self._drain()

        events = log.read()
        drift = build_drift_report(
            events,
            slippage_baseline=self._baseline,
            expected_funding=self._expected_funding,
        )

        ctx = self._alert_context(bars, drift, final_state, inputs)
        alerts = self._alerts.evaluate(ctx)

        self._persist_summary(final_state, drift)
        return RunResult(
            bars=bars,
            processed=len(inputs.candles),
            rejections=rejections,
            notes=notes,
            recoveries=list(self._feed.recoveries),
            drift=drift,
            alerts=alerts,
            final_state=final_state,
        )

    def heartbeat(self, now_ts: int) -> list[Alert]:
        """Watchdog seam: fire the heartbeat rule if the venue has gone silent.

        ``now_ts`` is a probe time (a watchdog's current venue-time estimate); the
        last event is the session clock. Silence > 2× the bar interval fires (§6.7).
        """
        ctx = AlertContext(
            now_ts=now_ts,
            bar_interval_s=self._resolution_s,
            last_event_ts=self._feed.clock.now,
        )
        return self._alerts.evaluate(ctx)

    # -- internals -------------------------------------------------------------

    def _run_engine(
        self, inputs, state: PortfolioState, log: EventLog
    ) -> PortfolioState:
        """One in-process engine run over ``inputs``, warm-started from ``state``.

        The shared engine seam (Nautilus bar lane as of N10) — the same substrate
        a backtest runs, warm-started from the reconstructed book so this batch
        resumes from the exact carried state (§6.7). The engine adopts, mutates,
        and returns that book as the final state. The sandboxed source session
        overrides this: its engine walk happens in the OS-isolated child instead.
        """
        assert self._adapter is not None  # base sessions are adapter-driven
        spec = EngineRunSpec(
            config=EngineConfig(),
            venue_spec=self._spec,
            initial_capital=str(self._initial_capital),
            fund_venue=self._venue,
            mark_policy="recorded",
            initial_state=state,
        )
        feed = EngineFeed(
            candles=inputs.candles,
            marks=inputs.marks,
            funding=inputs.funding,
            books=inputs.books,
            trades=inputs.trades,
            oi=inputs.oi,
        )
        return engine_for("auto")().run(feed, self._adapter, event_log=log, spec=spec)

    def _drain(self) -> tuple[list[StrategyRejection], list[str]]:
        """Drain the run's rejections + tearsheet notes from the adapter."""
        assert self._adapter is not None
        return self._adapter.drain_rejections(), self._adapter.tearsheet_notes()

    def _reconstruct_state(self) -> PortfolioState:
        """Fold the event log into the §6.7 warm-start book (see ``warm_state``).

        Rebuilt fresh every run from the single source of truth, so nothing
        double-counts.
        """
        return warm_state(
            self._store_events(),
            initial_capital=self._initial_capital,
            venue=self._venue,
        )

    def _store_events(self):
        return EventLog(self._store, self._tenant, self._run_id).read()

    def _alert_context(
        self, bars: list[LiveBar], drift: DriftReport, state: PortfolioState, inputs
    ) -> AlertContext:
        latest_marks = {
            m: marks[-1].mark_price for m, marks in inputs.marks.items() if marks
        }
        liq = self._liq_distances(state, latest_marks)

        spread = None
        for bar in reversed(bars):
            if bar.funding:
                spread = bar.funding[-1].rate_hourly
                break
        prev_spread = self._prev_funding_spread
        if spread is not None:
            self._prev_funding_spread = spread

        now = self._feed.clock.now or 0
        return AlertContext(
            now_ts=now,
            bar_interval_s=self._resolution_s,
            last_event_ts=self._feed.clock.now,
            liq_distances_pct=liq,
            drift=drift,
            funding_spread=spread,
            prev_funding_spread=prev_spread,
        )

    def _liq_distances(
        self, state: PortfolioState, marks: dict[str, float]
    ) -> dict[str, float]:
        """Percent distance of each open position's mark from its liq price (§6.5)."""
        out: dict[str, float] = {}
        for (venue, market), pos in state.positions.items():
            if venue != self._venue:
                continue
            mark = marks.get(market)
            if mark is None or mark <= 0:
                continue
            backing = (
                float(state.account(venue).cash)
                if pos.margin_mode == "cross"
                else getattr(pos, "isolated_margin", 0.0)
            )
            maint_frac = self._spec.liquidation.maint_frac(pos.notional(mark))
            lp = liquidation_price(pos, backing, maint_frac)
            out[market] = abs(mark - lp) / mark * 100.0
        return out

    def _persist_summary(self, state: PortfolioState, drift: DriftReport) -> None:
        """Persist the run head + summary (final equity, drift, alerts) (§6.2)."""
        cash = sum((a.cash for a in state.accounts.values()), start=money("0"))
        summary: dict = {
            "initial_capital": str(self._initial_capital),
            "cash": str(cash),
            "structural_breaches": drift.structural_breaches(),
            "alerts": [
                {"rule": a.rule, "severity": a.severity, "message": a.message}
                for a in self._alerts.fired
            ],
        }
        # The feed cursor rides in the head so a restart resumes it even when no
        # bar has been processed yet (the headless catch-up anchor, §6.7).
        if self._feed.cursor is not None:
            summary["cursor_bar_start"] = self._feed.cursor
        self._store.save_run(
            self._tenant,
            RunRecord(
                run_id=self._run_id,
                kind="paper",
                status="running",
                summary=summary,
            ),
        )


class SandboxedPaperSession(PaperSession):
    """A paper session over untrusted user *source* — engine walks stay sandboxed (D25).

    The paper counterpart of ``services.run_backtest_source``: instead of an
    in-process adapter, the session holds the submitted source (plus its
    AST-derived Strategy class name), and every engine step —
    each :meth:`feed` / :meth:`catch_up` batch — runs inside the OS-isolated
    sandbox child (:func:`~flint.strategy.sandbox.paper_runner.run_paper_step_in_sandbox`).
    Untrusted code never executes in this process; only inert input copies cross
    the boundary, and the child's event rows are re-persisted on this side.

    The warm start crosses as *data*, not object state: the child receives the
    run's prior event rows and folds them into the carried book itself
    (``warm_state`` — the identical reconstruction this class uses), so the
    sandboxed path resumes from the same state, by construction.

    Two step semantics to know (both are the §6.7 restart contract, applied
    per step): the strategy object is rebuilt from source each step, so
    in-memory strategy state never carries between steps (portfolio state does,
    via the event log); and an ML template's model store is child-local, so ML
    strategies retrain per step. Poll-cadence note: see :meth:`PaperSession.catch_up`.
    """

    def __init__(
        self,
        *,
        source: str,
        class_name: str,
        seed: int = 0,
        overrides: dict | None = None,
        quota: ResourceQuota | None = None,
        **kwargs,
    ) -> None:
        super().__init__(adapter=None, **kwargs)
        self._source = source
        self._class_name = class_name
        self._seed = seed
        self._overrides = dict(overrides or {})
        self._quota = quota
        # Per-step drains, filled by the child's result (the in-process session
        # drains its adapter instead).
        self._step_rejections: list[StrategyRejection] = []
        self._step_notes: list[str] = []
        # The pre-run gate's ValidationReport, stamped by the services front door
        # (services.start_paper_source) so callers can read leak warnings.
        self.validation: object | None = None

    # -- construction ----------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        tenant: TenantContext,
        store: UserDataPort,
        run_id: str,
        source: str,
        class_name: str,
        market: str,
        resolution_s: int,
        initial_capital: Money | str = "100000",
        seed: int = 0,
        overrides: dict | None = None,
        **kwargs,
    ) -> "SandboxedPaperSession":
        """Start a fresh sandboxed-source paper session and persist its head (§6.2)."""
        cap = money(initial_capital)
        store.save_run(
            tenant,
            RunRecord(
                run_id=run_id,
                kind="paper",
                status="running",
                summary={"initial_capital": str(cap)},
            ),
        )
        return cls(
            tenant=tenant,
            store=store,
            run_id=run_id,
            source=source,
            class_name=class_name,
            market=market,
            resolution_s=resolution_s,
            initial_capital=cap,
            seed=seed,
            overrides=overrides,
            **kwargs,
        )

    @classmethod
    def resume(
        cls,
        *,
        tenant: TenantContext,
        store: UserDataPort,
        run_id: str,
        source: str,
        class_name: str,
        market: str,
        resolution_s: int,
        seed: int = 0,
        overrides: dict | None = None,
        **kwargs,
    ) -> "SandboxedPaperSession":
        """Pick a killed sandboxed-source session back up from its event log (§6.7).

        Same contract as :meth:`PaperSession.resume` — the portfolio folds from
        the log, the cursor resumes from the last processed bar (or the persisted
        headless anchor) — with the strategy rebuilt from ``source`` in the child
        on the next step rather than by the caller.
        """
        record = store.load_run(tenant, run_id)  # KeyError if unknown/other-tenant
        cap = money(record.summary.get("initial_capital", "0"))
        resume_bar_start = cls._resume_cursor(store, tenant, run_id, record)
        return cls(
            tenant=tenant,
            store=store,
            run_id=run_id,
            source=source,
            class_name=class_name,
            market=market,
            resolution_s=resolution_s,
            initial_capital=cap,
            seed=seed,
            overrides=overrides,
            resume_bar_start=resume_bar_start,
            **kwargs,
        )

    # -- the sandboxed engine step ----------------------------------------------

    def _run_engine(
        self, inputs, state: PortfolioState, log: EventLog
    ) -> PortfolioState:
        """Run one engine step in the sandbox child; re-persist its events here.

        ``state`` (reconstructed by the caller) is deliberately unused: the child
        folds the prior rows itself, so no live object crosses the boundary. The
        returned book is re-folded from the store after the append — the event
        log stays the single source of truth on both sides.
        """
        from flint.strategy.sandbox.paper_runner import run_paper_step_in_sandbox

        prior = self._store.load_events(self._tenant, self._run_id)
        result = run_paper_step_in_sandbox(
            self._source,
            self._class_name,
            candles=inputs.candles,
            funding=inputs.funding,
            marks=inputs.marks,
            oi=inputs.oi,
            books=inputs.books,
            trades=inputs.trades,
            prior_events=prior,
            seed=self._seed,
            initial_capital=str(self._initial_capital),
            fund_venue=self._venue,
            run_id=self._run_id,
            overrides=self._overrides,
            quota=self._quota,
        )
        self._store.append_events(self._tenant, self._run_id, result.events)
        self._step_rejections = [_rejection_from_row(r) for r in result.rejections]
        self._step_notes = list(result.notes)
        return self._reconstruct_state()

    def _drain(self) -> tuple[list[StrategyRejection], list[str]]:
        rejections, notes = self._step_rejections, self._step_notes
        self._step_rejections, self._step_notes = [], []
        return rejections, notes


def _rejection_from_row(row: dict) -> StrategyRejection:
    """Revive a child's ``services.backtest._rej``-shaped row as a StrategyRejection."""
    return StrategyRejection(
        reason=row.get("reason", ""),
        signal=Signal(
            market=row.get("market", ""),
            venue=row.get("venue", ""),
            action=row.get("action", "close"),
        ),
        detail=row.get("detail", ""),
        ts=int(row.get("ts", 0)),
    )
