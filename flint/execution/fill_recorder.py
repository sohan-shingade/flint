"""FillRecorder — owns the recorded-fill list and the diagnostic log.

D-2.1.b Step 3 (Wave 2): pulls the two append-only ledgers off
BacktestContext into one place. Together with PositionManager (Step 1)
and CashManager (Step 2), the next slice (Step 4 = OrderQueue) leaves
BacktestContext as a thin orchestrator over four small components.

Both fields are intentionally exposed as the underlying mutable lists
so existing call sites (`self._fills.append(...)`,
`self._log_messages.append(...)`) keep working through the
BacktestContext property aliases. Steps 5–7 migrate to explicit
`self._fr.record(...)` / `self._fr.log(...)` once every call site is
auditable in one PR.
"""
from __future__ import annotations

from typing import List

from ..models import Fill


class FillRecorder:
    """Append-only ledgers for recorded fills + diagnostic log messages."""

    __slots__ = ("_fills", "_logs")

    def __init__(self) -> None:
        self._fills: List[Fill] = []
        self._logs: List[str] = []

    # --- fills -------------------------------------------------------------

    @property
    def fills(self) -> List[Fill]:
        """Direct list access — used by `_apply_fill` etc. Treat as
        private; new call sites should prefer `record(fill)`."""
        return self._fills

    def record(self, fill: Fill) -> None:
        self._fills.append(fill)

    def all_fills(self) -> List[Fill]:
        """Snapshot copy — what `BacktestContext.all_fills` returns."""
        return list(self._fills)

    # --- logs --------------------------------------------------------------

    @property
    def logs(self) -> List[str]:
        return self._logs

    def log(self, message: str) -> None:
        self._logs.append(message)

    def messages(self) -> List[str]:
        """Snapshot copy — what `BacktestContext.log_messages` returns."""
        return list(self._logs)
