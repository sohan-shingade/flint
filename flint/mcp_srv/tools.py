"""The agent tool layer — JSON-in/JSON-out callables over ``services/`` (§13.2).

These are the eight agent tools of §13.2 as plain Python methods on
:class:`AgentTools`, independent of any transport. The MCP stdio server
(:mod:`flint.mcp_srv.server`) is a thin adapter that registers each of these;
the same methods back the REST contract and are what the tests drive. Everything
routes through ``services/`` with a :class:`TenantContext` — there is no path
that reaches the engine or a store directly (§4, §13.4).

Two invariants make this an *agent* surface rather than a human one:

* **Structured everything (§13.3).** A funding gap is ``{rejected: "funding_gap",
  ...}``; an unrunnable strategy is ``{valid: false, ...}`` with line-precise
  issues; a failure is an enum with detail — never a stack trace an agent must
  parse. Faults still raise :class:`ServiceError`, whose ``to_payload`` is the
  same machine-readable shape.
* **Bounded concurrency (§13.5).** Every run is submitted through the
  ``JobRunnerPort`` under a per-tenant in-flight cap (serial / low-concurrency in
  v1); exceeding it is a structured ``concurrency_limit`` rejection, not a crash.
  The 50-way parallel fan-out is deferred until per-run quotas soak (M15/M18).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from typing import Any

from flint.data import DataManager
from flint.data.ranges import Kind
from flint.ports import JobRunnerPort, ResourceQuota, TenantContext, UserDataPort
from flint.research import ParamSpace
from flint.strategy import list_templates
from flint.venues import HYPERLIQUID

from flint import services
from flint.services import (
    BacktestRequest,
    OptimizeRequest,
    new_run_id,
)


class _ConcurrencyLimit(Exception):
    """Raised when a tenant's in-flight run cap is hit — surfaced as a rejection."""


class AgentTools:
    """The §13.2 agent tools for one tenant, wired to the local services front door.

    Inject the same ports the API composition root uses; defaults give a runnable
    out-of-box local instance (in-memory user data + bare DataManager + in-process
    job runner) so an agent can drive it with no setup. ``max_concurrent_runs`` is
    the §13.5 per-tenant cap enforced around the JobRunner.
    """

    def __init__(
        self,
        *,
        tenant: TenantContext | None = None,
        user_data: UserDataPort | None = None,
        data: DataManager | None = None,
        job_runner: JobRunnerPort | None = None,
        quota: ResourceQuota | None = None,
        max_concurrent_runs: int = 2,
    ) -> None:
        from flint.adapters import InMemoryUserData, InProcessJobRunner

        self.tenant = tenant or TenantContext.local()
        self._user_data = user_data or InMemoryUserData()
        self._data = data if data is not None else DataManager(sources=[])
        self._job_runner = job_runner or InProcessJobRunner()
        self._quota = quota or ResourceQuota.default()
        self._max_concurrent = max_concurrent_runs
        self._inflight = 0
        self._lock = threading.Lock()

    # -- discovery -----------------------------------------------------------

    def list_universe(self) -> dict[str, Any]:
        """Templates + the executable venue an agent can target (§13.2).

        Lists the built-in strategy templates (name, category, tunable knobs) so
        an agent can pick a starting point, and states the one executable venue
        (Hyperliquid, D28) — a signal to any other venue is structurally inert.
        """
        return {
            "templates": [
                {
                    "name": spec.name,
                    "category": spec.category,
                    "summary": spec.summary,
                    "is_ml": spec.is_ml,
                    "params": spec.param_spec(),
                }
                for spec in list_templates()
            ],
            "executable_venues": [HYPERLIQUID.name],
            "note": (
                "User strategy source is accepted by validate_strategy/run_backtest "
                "in addition to template names."
            ),
        }

    def data_coverage(self, *, market: str, venue: str) -> dict[str, Any]:
        """Covered ranges for ``(venue, market)`` — no fetch, no gate (§9, §13.2)."""
        return services.data_coverage(data=self._data, market=market, venue=venue)

    # -- validation (the boundary, carry-forwards c/d/n) ---------------------

    def validate_strategy(self, code: str) -> dict[str, Any]:
        """Sandbox + static-lint submitted ``code`` — structured errors before a run.

        The OS-isolated boundary (D25): line-precise screen violations, sandbox
        runtime errors, and a look-ahead report (static AST pass + the truncation
        probe run *inside* the sandbox) — the "no leak detected" honesty and blind
        spots included. This is the gate every user-source run passes first.
        """
        return services.validate_strategy(code).to_payload()

    # -- runs ----------------------------------------------------------------

    def run_backtest(
        self,
        *,
        code: str | None = None,
        strategy: str | None = None,
        universe: Sequence[str] = ("SOL-PERP",),
        venues: Sequence[str] = (HYPERLIQUID.name,),
        start_ms: int = 0,
        end_ms: int = 0,
        resolution_s: int = 3600,
        fill_mode: str = "auto",
        seed: int = 0,
        initial_capital: str = "100000",
        overrides: Mapping[str, Any] | None = None,
        signal_venues: Sequence[str] = (),
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Backtest user ``code`` or a ``strategy`` template — returns a run id (§13.2).

        Exactly one of ``code`` (untrusted user source, validated + sandboxed) or
        ``strategy`` (a trusted template name) is required. A validation failure
        returns ``{run_id, verdict:"invalid", validation:{...}}`` with no engine
        run; a funding gap returns ``verdict:"rejected"`` carrying the structured
        payload; success returns ``verdict:"ok"``. Fetch the full result with
        :meth:`get_results`. The run executes under the JobRunner concurrency cap.
        """
        if (code is None) == (strategy is None):
            raise services.ValidationError(
                "provide exactly one of `code` or `strategy`",
                hint="`code` is user source; `strategy` is a template name",
            )
        rid = run_id or new_run_id()

        if code is not None:

            def _job() -> dict[str, Any]:
                out = services.run_backtest_source(
                    self.tenant,
                    source=code,
                    run_id=rid,
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
                    user_data=self._user_data,
                    data=self._data,
                )
                return self._outcome_payload(out)
        else:
            request = BacktestRequest(
                run_id=rid,
                strategy=strategy or "",
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
            )

            def _job() -> dict[str, Any]:
                out = services.run_backtest(
                    self.tenant, request, user_data=self._user_data, data=self._data
                )
                return self._outcome_payload(out)

        try:
            return self._submit(_job)
        except _ConcurrencyLimit as exc:
            return {
                "rejected": {
                    "code": "concurrency_limit",
                    "message": str(exc),
                    "hint": "wait for an in-flight run to finish and retry",
                }
            }

    def get_results(self, run_id: str) -> dict[str, Any]:
        """Structured metrics + equity curve + per-trade log + cost attribution.

        Adds a per-segment fill-fidelity block (§13.3) so an agent can weight its
        confidence by how the fills were modeled (Tier A queue-aware … Tier C
        parametric). Raises :class:`NotFoundError` for an unknown/foreign run.
        """
        return services.run_results(self.tenant, run_id, user_data=self._user_data)

    def explain_failure(self, run_id: str) -> dict[str, Any]:
        """Why a run did poorly, as an enum + detail an agent can act on (§13.3).

        Reasons: ``funding_dominated`` (funding overwhelmed trading PnL),
        ``liquidated`` (a liquidation event fired), ``no_trades`` (nothing filled),
        ``overfit_suspected`` (keyed off the Deflated Sharpe over the trial family),
        ``lookahead_detected`` (the validator flagged a leak). A rejected/invalid
        run is explained as such. Returns the reasons found (possibly several) with
        their detail fields, never a bare "it lost money".
        """
        results = self.get_results(run_id)
        return {"run_id": run_id, "reasons": _diagnose(results)}

    # -- search + compare ----------------------------------------------------

    def optimize(
        self,
        *,
        strategy: str,
        params: Sequence[str],
        universe: Sequence[str] = ("SOL-PERP",),
        venues: Sequence[str] = (HYPERLIQUID.name,),
        start_ms: int = 0,
        end_ms: int = 0,
        resolution_s: int = 3600,
        n_windows: int = 3,
        n_trials: int = 20,
        purge_bars: int = 0,
        embargo_bars: int = 0,
        label_horizon_bars: int = 1,
        seed: int = 0,
        initial_capital: str = "100000",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Walk-forward param sweep for a ``strategy`` template (§13.2).

        ``params`` are ``name=lo:hi[:step]`` strings (the §3.3 CLI form). The
        result carries the trial count and out-of-sample (walk-forward) scores plus
        the Deflated Sharpe over the trial family, so an agent cannot fool itself
        with in-sample overfit — and can be told it is overfit. Runs under the
        JobRunner concurrency cap. (User-source optimize is template-parity work
        deferred past 7.3; source strategies validate + backtest today.)
        """
        rid = run_id or new_run_id()
        request = OptimizeRequest(
            run_id=rid,
            strategy=strategy,
            params=tuple(ParamSpace.parse(p) for p in params),
            universe=tuple(universe),
            venues=tuple(venues),
            start_ms=start_ms,
            end_ms=end_ms,
            resolution_s=resolution_s,
            n_windows=n_windows,
            n_trials=n_trials,
            purge_bars=purge_bars,
            embargo_bars=embargo_bars,
            label_horizon_bars=label_horizon_bars,
            seed=seed,
            initial_capital=initial_capital,
        )

        def _job() -> dict[str, Any]:
            out = services.run_optimization(
                self.tenant, request, user_data=self._user_data, data=self._data
            )
            return {
                "run_id": out.run_id,
                "verdict": out.verdict,
                "summary": out.summary,
            }

        try:
            return self._submit(_job)
        except _ConcurrencyLimit as exc:
            return {
                "rejected": {
                    "code": "concurrency_limit",
                    "message": str(exc),
                    "hint": "wait for an in-flight run to finish and retry",
                }
            }

    def compare(self, run_ids: Sequence[str]) -> dict[str, Any]:
        """Side-by-side metric table for ``run_ids`` with runlib's honesty warnings.

        Most important warning: two Sharpes measured over *different effective
        ranges* are not the same measurement (§6.3) — carried so an agent never
        ranks on an apples-to-oranges comparison.
        """
        return services.compare_runs(
            self.tenant, list(run_ids), user_data=self._user_data
        )

    # -- internals -----------------------------------------------------------

    def _submit(self, fn: Any) -> dict[str, Any]:
        """Run ``fn`` through the JobRunner under the per-tenant in-flight cap."""
        with self._lock:
            if self._inflight >= self._max_concurrent:
                raise _ConcurrencyLimit(
                    f"tenant {self.tenant.tenant_id!r} has {self._inflight} run(s) "
                    f"in flight (cap {self._max_concurrent}) — v1 is serial/low-concurrency"
                )
            self._inflight += 1
        try:
            return self._job_runner.submit(self.tenant, fn, self._quota)
        finally:
            with self._lock:
                self._inflight -= 1

    @staticmethod
    def _outcome_payload(out: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"run_id": out.run_id, "verdict": out.verdict}
        if out.verdict == "rejected" and out.rejection is not None:
            payload.update(out.rejection.to_payload())
        if out.verdict == "invalid":
            payload["validation"] = out.summary.get("validation")
        return payload


# -- failure diagnosis (§13.3) ----------------------------------------------

# Funding is "dominant" when its magnitude dwarfs the trading PnL — the canonical
# "funding was 90% of PnL" case an agent must see to re-choose its market/side.
_FUNDING_DOMINANCE = 0.5
# A Deflated Sharpe at/under this, over a real trial family, reads as overfit: the
# raw edge does not survive the multiple-comparisons haircut (§11.1).
_OVERFIT_DSR = 0.0


def _diagnose(results: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Turn a results blob into the §13.3 failure enums with detail fields."""
    reasons: list[dict[str, Any]] = []

    if results.get("rejected"):
        rej = results["rejected"]
        reasons.append(
            {
                "reason": "rejected",
                "code": rej.get("code"),
                "detail": rej.get("message"),
            }
        )
        return reasons
    if results.get("verdict") == "invalid":
        reasons.append(
            {
                "reason": "invalid_strategy",
                "detail": "did not pass validation",
                "validation": results.get("validation"),
            }
        )
        return reasons

    validation = results.get("validation") or {}
    if validation.get("leak_detected"):
        reasons.append(
            {
                "reason": "lookahead_detected",
                "detail": validation.get("lookahead", {}).get("summary", ""),
            }
        )

    trades = results.get("trades") or []
    if not trades:
        reasons.append(
            {"reason": "no_trades", "detail": "no fills — the strategy never traded"}
        )

    for t in trades:
        if t.get("liquidity") == "liquidation" or t.get("side") == "liquidation":
            reasons.append(
                {
                    "reason": "liquidated",
                    "at_ts": t.get("ts"),
                    "mark_price": t.get("price"),
                }
            )
            break

    cost = results.get("cost_attribution") or {}
    funding = abs(float(cost.get("funding", 0.0)))
    trading = abs(float(cost.get("trading_pnl", 0.0)))
    if funding > 0 and funding >= _FUNDING_DOMINANCE * (funding + trading):
        reasons.append(
            {
                "reason": "funding_dominated",
                "detail": f"funding {cost.get('funding')} vs trading pnl "
                f"{cost.get('trading_pnl')} — funding drove the result",
            }
        )

    dsr = results.get("deflated_sharpe")
    n_trials = results.get("n_trials") or 0
    dsr_value = _dsr_value(dsr)
    if dsr_value is not None and n_trials > 1 and dsr_value <= _OVERFIT_DSR:
        reasons.append(
            {
                "reason": "overfit_suspected",
                "detail": f"deflated Sharpe {dsr_value} over {n_trials} trials — the "
                "edge does not survive the multiple-comparisons haircut",
                "n_trials": n_trials,
            }
        )

    return reasons


def _dsr_value(dsr: Any) -> float | None:
    """Extract a scalar Deflated Sharpe from the summary's dict-or-scalar shape."""
    if dsr is None:
        return None
    if isinstance(dsr, Mapping):
        for key in ("deflated_sharpe", "value", "dsr"):
            if key in dsr:
                try:
                    return float(dsr[key])
                except (TypeError, ValueError):
                    return None
        return None
    try:
        return float(dsr)
    except (TypeError, ValueError):
        return None


# The default kinds a coverage query reports (candles + funding + OI), re-exported
# so the server module and tests share one source of truth.
DEFAULT_COVERAGE_KINDS: tuple[Kind, ...] = (Kind.CANDLES, Kind.FUNDING, Kind.OI)
