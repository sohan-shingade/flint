"""The REST + WebSocket API — the only channel between any UI and the backend (§12).

FastAPI talks to ``services/`` and nothing else: a surface never reaches into the
engine, a store, or research directly (§4, §17). Every handler resolves a
``TenantContext`` and calls a service; the service owns the domain. The uniform
error schema ``{"error": {code, message, detail, hint}}`` is emitted for every
fault (§13.3/§19.1), while expected data-scarcity outcomes (funding gaps) ride in
the result body as structured ``rejected`` payloads, never as HTTP errors.

``create_app`` is the composition root: it is handed the ports + a DataManager and
wires the job lifecycle, run library, data, alert, and paper-monitor services. A
per-session bearer token + Origin check guard the code-executing endpoints (§12).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocket

from flint.adapters import EnvSecrets, InMemoryUserData, InProcessJobRunner
from flint.data import DataManager
from flint.data.ranges import Kind
from flint.ports import JobRunnerPort, SecretsPort, TenantContext, UserDataPort
from flint.venues.hyperliquid import hyperliquid_client_factory
from flint.services import (
    AlertConfigStore,
    BacktestJobs,
    BacktestRequest,
    ServiceError,
    ValidationError,
    compare_runs,
    data_coverage,
    funding_lab,
    list_runs,
    new_run_id,
    paper_snapshot,
    pull_data,
    stop_live,
)

from .schemas import AlertBody, BacktestBody, DataPullBody
from .security import check_origin, check_token, generate_token

# code -> HTTP status for the uniform error schema (§19.1).
_STATUS = {
    "validation": 400,
    "unauthorized": 401,
    "forbidden_origin": 403,
    "not_found": 404,
    "venue_unavailable": 503,
    "internal": 500,
}


@dataclass
class Deps:
    """The composed services + security context an app instance serves."""

    user_data: UserDataPort
    data: DataManager
    jobs: BacktestJobs
    alerts: AlertConfigStore
    tenant: TenantContext
    token: str
    allowed_origins: frozenset[str] = field(default_factory=frozenset)
    # Live kill switch: the SecretsPort resolves the venue key server-side and the
    # factory builds the (production or, in tests, mocked) live venue client.
    secrets: SecretsPort = field(default_factory=EnvSecrets)
    live_client_factory: object = hyperliquid_client_factory


def _now_ms() -> int:
    return int(time.time() * 1000)


def create_app(
    *,
    user_data: UserDataPort | None = None,
    data: DataManager | None = None,
    job_runner: JobRunnerPort | None = None,
    tenant: TenantContext | None = None,
    token: str | None = None,
    allowed_origins: frozenset[str] | None = None,
    secrets: SecretsPort | None = None,
    live_client_factory=None,
) -> FastAPI:
    """Build the API app with its services composed and security wired.

    Ports default to the in-memory local adapters and a fresh session token, so a
    bare ``create_app()`` is a runnable local server; ``flint serve`` (7.2) injects
    the durable adapters + the token it prints. The token is exposed on
    ``app.state.token`` so the serve command can auto-inject it into the local UI.
    ``secrets``/``live_client_factory`` back the live kill switch; they default to
    the server-side ``.env`` secrets and the production HL client factory, and are
    injected with a fake venue in tests (no real key/order, D26).
    """
    user_data = user_data or InMemoryUserData()
    data = data or DataManager()
    job_runner = job_runner or InProcessJobRunner()
    deps = Deps(
        user_data=user_data,
        data=data,
        jobs=BacktestJobs(user_data=user_data, data=data, job_runner=job_runner),
        alerts=AlertConfigStore(),
        tenant=tenant or TenantContext.local(),
        token=token or generate_token(),
        allowed_origins=allowed_origins or frozenset(),
        secrets=secrets or EnvSecrets(),
        live_client_factory=live_client_factory or hyperliquid_client_factory,
    )

    app = FastAPI(title="Flint API", version="1")
    app.state.deps = deps
    app.state.token = deps.token

    _install_error_handlers(app)
    _install_routes(app)
    return app


# -- dependencies (security) -------------------------------------------------


def _require_token(request: Request) -> None:
    check_token(request, request.app.state.deps.token)


def _require_origin(request: Request) -> None:
    check_origin(request, request.app.state.deps.allowed_origins)


# state-changing routes require both; read-only routes require the token only.
_READ = [Depends(_require_token)]
_WRITE = [Depends(_require_token), Depends(_require_origin)]


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=_STATUS.get(exc.code, 500), content=exc.to_payload()
        )

    @app.exception_handler(RequestValidationError)
    async def _bad_body(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "validation",
                    "message": "request body failed validation",
                    "detail": str(exc.errors()),
                    "hint": "check the field types and required fields",
                }
            },
        )


def _install_routes(app: FastAPI) -> None:
    api = "/api/v1"

    @app.post(f"{api}/backtests", status_code=201, dependencies=_WRITE)
    def submit_backtest(request: Request, body: BacktestBody) -> dict:
        deps: Deps = request.app.state.deps
        req = BacktestRequest(
            run_id=new_run_id(),
            strategy=body.strategy,
            universe=tuple(body.universe),
            venues=tuple(body.venues),
            start_ms=body.range.start_ms,
            end_ms=body.range.end_ms,
            resolution_s=body.resolution_s,
            fill_mode=body.fill_mode,
            seed=body.seed,
            initial_capital=body.initial_capital,
            overrides=body.overrides,
            signal_venues=tuple(body.signal_venues),
        )
        run_id = deps.jobs.submit(
            deps.tenant, req, idempotency_key=body.idempotency_key, now_ms=_now_ms()
        )
        return {"run_id": run_id}

    @app.get(f"{api}/backtests/{{run_id}}/status", dependencies=_READ)
    def backtest_status(request: Request, run_id: str) -> dict:
        deps: Deps = request.app.state.deps
        return deps.jobs.status(deps.tenant, run_id)

    @app.post(f"{api}/backtests/{{run_id}}/cancel", dependencies=_WRITE)
    def cancel_backtest(request: Request, run_id: str) -> dict:
        deps: Deps = request.app.state.deps
        return deps.jobs.cancel(deps.tenant, run_id)

    @app.get(f"{api}/backtests/{{run_id}}", dependencies=_READ)
    def backtest_result(request: Request, run_id: str) -> dict:
        deps: Deps = request.app.state.deps
        return deps.jobs.result(deps.tenant, run_id)

    @app.get(f"{api}/runs", dependencies=_READ)
    def runs(request: Request, strategy: str | None = None) -> dict:
        deps: Deps = request.app.state.deps
        return {"runs": list_runs(deps.tenant, user_data=deps.user_data, strategy=strategy)}

    @app.get(f"{api}/runs/compare", dependencies=_READ)
    def runs_compare(
        request: Request, run_id: list[str] = Query(default_factory=list)
    ) -> dict:
        deps: Deps = request.app.state.deps
        return compare_runs(deps.tenant, run_id, user_data=deps.user_data)

    @app.post(f"{api}/data/pull", dependencies=_WRITE)
    def data_pull(request: Request, body: DataPullBody) -> dict:
        deps: Deps = request.app.state.deps
        return pull_data(
            data=deps.data,
            market=body.market,
            venues=body.venues,
            kind=_kind(body.kind),
            start_ms=body.range.start_ms,
            end_ms=body.range.end_ms,
        )

    @app.get(f"{api}/data/coverage", dependencies=_READ)
    def coverage(request: Request, market: str, venue: str) -> dict:
        deps: Deps = request.app.state.deps
        return data_coverage(data=deps.data, market=market, venue=venue)

    @app.get(f"{api}/lab/funding", dependencies=_READ)
    def lab_funding(
        request: Request,
        market: list[str] = Query(default_factory=list),
        venue: list[str] = Query(default_factory=list),
        start_ms: int = 0,
        end_ms: int | None = None,
        rate_type: str | None = "predicted",
    ) -> dict:
        # Read-only funding/basis lab (§10): markets×venues carry for the heatmap.
        # All venues are lab legs — degrade-not-reject, never funding-gated (§6.4).
        deps: Deps = request.app.state.deps
        return funding_lab(
            deps.tenant,
            data=deps.data,
            markets=market,
            venues=venue,
            start_ms=start_ms,
            end_ms=end_ms,
            rate_type=rate_type,
        )

    @app.post(f"{api}/alerts", status_code=201, dependencies=_WRITE)
    def create_alert(request: Request, body: AlertBody) -> dict:
        deps: Deps = request.app.state.deps
        return deps.alerts.create(
            deps.tenant, rule=body.rule, threshold=body.threshold, channel=body.channel
        )

    @app.post(f"{api}/live/{{run_id}}/stop", dependencies=_WRITE)
    def stop_live_run(
        request: Request, run_id: str, flatten: bool = False
    ) -> dict:
        """The UI-callable kill switch — cancel a live run's orders, optionally flatten."""
        deps: Deps = request.app.state.deps
        return stop_live(
            deps.tenant,
            run_id,
            store=deps.user_data,
            secrets=deps.secrets,
            client_factory=deps.live_client_factory,
            flatten=flatten,
        )

    @app.websocket(f"{api}/paper/{{run_id}}/stream")
    async def paper_stream(ws: WebSocket, run_id: str) -> None:
        deps: Deps = ws.app.state.deps
        # Guard BEFORE accepting: a bad origin/token never gets a live socket.
        try:
            check_origin(ws, deps.allowed_origins)
            check_token(ws, deps.token)
        except ServiceError:
            await ws.close(code=1008)  # policy violation
            return
        await ws.accept()
        try:
            snapshot = paper_snapshot(deps.tenant, run_id, user_data=deps.user_data)
        except ServiceError as exc:
            await ws.send_json(exc.to_payload())
            await ws.close(code=1008)
            return
        await ws.send_json(snapshot)
        await ws.close()


def _kind(raw: str) -> Kind:
    try:
        return Kind(raw)
    except ValueError:
        raise ValidationError(
            f"unknown data kind {raw!r}",
            detail=f"valid kinds: {', '.join(k.value for k in Kind)}",
        ) from None
