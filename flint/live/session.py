"""The paper session runner — the same engine, fed live, that survives restart (§6.7).

Paper trading is *not* a second engine; it is the backtest engine fed by the
:class:`~flint.data.livefeed.LiveFeed` instead of historical replay. That is the
parity promise, and this runner keeps it by construction: it assembles the
LiveFeed's bars into the exact keyword feeds ``BacktestEngine.run`` takes and
runs them through the identical per-bar loop (fills, funding, liquidation).

The runner is where the Phase-5 carry-forwards compose:

* **strategy** (5.1/5.3/5.4): a template name resolves through the registry to a
  class; a classic template is wrapped in ``EngineStrategy``, an ML template in
  ``MLEngineStrategy`` + a tenant-scoped ``ModelStore`` + the run seed. The
  adapter is passed to ``engine.run(strategy=...)`` and its
  ``drain_rejections()`` / ``tearsheet_notes()`` are drained after each run.
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

from flint.data.ingest.recorders import WsMessageSource
from flint.data.livefeed import (
    GapRecovery,
    GapSource,
    LiveBar,
    LiveFeed,
    assemble_engine_inputs,
)
from flint.engine import BacktestEngine, EngineConfig, PortfolioState
from flint.engine.liquidation import liquidation_price
from flint.engine.money import Money, money
from flint.engine.portfolio import EventLog, fold
from flint.engine.portfolio.events import EQUITY
from flint.ports import TenantContext, UserDataPort
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
        adapter: EngineStrategy,
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
        events = store.load_events(tenant, run_id)
        processed = [int(e.get("ts", 0)) for e in events if e.get("kind") == EQUITY]
        resume_bar_start = max(processed) if processed else None
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

    # -- the run ---------------------------------------------------------------

    def feed(self, source: WsMessageSource) -> RunResult:
        """Consume one WS connection: aggregate → run the engine → drift + alerts."""
        bars = self._feed.connect(source)
        inputs = assemble_engine_inputs(bars)

        state = self._reconstruct_state()
        log = EventLog(self._store, self._tenant, self._run_id)
        engine = BacktestEngine(
            log, config=EngineConfig(), state=state, venue_spec=self._spec
        )
        engine.run(inputs.candles, strategy=self._adapter, **inputs.run_kwargs())

        rejections = self._adapter.drain_rejections()
        notes = self._adapter.tearsheet_notes()

        events = log.read()
        drift = build_drift_report(
            events,
            slippage_baseline=self._baseline,
            expected_funding=self._expected_funding,
        )

        ctx = self._alert_context(bars, drift, engine.state, inputs)
        alerts = self._alerts.evaluate(ctx)

        self._persist_summary(engine.state, drift)
        return RunResult(
            bars=bars,
            processed=len(inputs.candles),
            rejections=rejections,
            notes=notes,
            recoveries=list(self._feed.recoveries),
            drift=drift,
            alerts=alerts,
            final_state=engine.state,
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

    def _reconstruct_state(self) -> PortfolioState:
        """Fold the event log into the portfolio, then add the initial capital.

        ``fold`` rebuilds cash/positions as *deltas from zero* (fills, funding,
        liquidation); true state is ``initial_capital + deltas``. Rebuilt fresh
        every run from the single source of truth, so nothing double-counts.
        """
        book = fold(self._store_events())
        state = PortfolioState()
        state.account(self._venue).cash = self._initial_capital
        for venue, acct in book.accounts.items():
            a = state.account(venue)
            a.cash = a.cash + acct.cash
            a.fees_paid = acct.fees_paid
            a.funding_paid = acct.funding_paid
            a.realized_pnl = acct.realized_pnl
        state.positions.update(book.positions)
        return state

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
        self._store.save_run(
            self._tenant,
            RunRecord(
                run_id=self._run_id,
                kind="paper",
                status="running",
                summary={
                    "initial_capital": str(self._initial_capital),
                    "cash": str(cash),
                    "structural_breaches": drift.structural_breaches(),
                    "alerts": [
                        {"rule": a.rule, "severity": a.severity, "message": a.message}
                        for a in self._alerts.fired
                    ],
                },
            ),
        )
