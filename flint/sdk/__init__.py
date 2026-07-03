"""sdk — the Lab object and the ``flint`` CLI; delegates to services/ (§17).

``Lab`` is the notebook/script surface (D8): ``lab.backtest(...)`` / ``lab.optimize(...)``
/ ``lab.paper(...)`` return result objects whose ``tearsheet()`` renders the §11 trust
report. The CLI (``flint ...``, entry point :func:`flint.sdk.cli.main`) is the everyday
driver over the same services. Both talk only to ``services/`` under a ``TenantContext``.
"""

from __future__ import annotations

from .lab import BacktestResult, Lab, OptimizeResult, PaperHandle

__all__ = [
    "Lab",
    "BacktestResult",
    "OptimizeResult",
    "PaperHandle",
]
