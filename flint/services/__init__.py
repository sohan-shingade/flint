"""services — the front door; every function takes TenantContext (§4, §17).

Surfaces (api/sdk/mcp/agent) drive the domain only through here — never into the
engine or a store directly. Phase 7 upgrades the Phase-1 walking skeleton into
the real front door: ``run_backtest`` drives the honest engine under a
``TenantContext`` (funding-gated data → engine → trust report), backed by the job
lifecycle, Run Library, data, alert, and paper-monitor services the API surface
composes.
"""

from __future__ import annotations

from .alerts import AlertConfigStore
from .backtest import (
    BacktestOutcome,
    BacktestRequest,
    make_backtest_runner,
    run_backtest,
)
from .data import data_coverage, pull_data
from .errors import (
    NotFoundError,
    Rejection,
    ServiceError,
    ValidationError,
    VenueUnavailableError,
)
from .jobs import BacktestJobs, JobRecord, new_run_id
from .optimize import OptimizeOutcome, OptimizeRequest, run_optimization
from .paper import paper_snapshot, start_paper
from .runs import compare_runs, export_run, import_legacy_runs, list_runs

__all__ = [
    # backtest front door
    "run_backtest",
    "make_backtest_runner",
    "BacktestRequest",
    "BacktestOutcome",
    # optimize front door
    "run_optimization",
    "OptimizeRequest",
    "OptimizeOutcome",
    # job lifecycle
    "BacktestJobs",
    "JobRecord",
    "new_run_id",
    # data
    "data_coverage",
    "pull_data",
    # run library
    "list_runs",
    "compare_runs",
    "export_run",
    "import_legacy_runs",
    # alerts
    "AlertConfigStore",
    # paper monitor
    "paper_snapshot",
    "start_paper",
    # error taxonomy
    "ServiceError",
    "ValidationError",
    "NotFoundError",
    "VenueUnavailableError",
    "Rejection",
]
