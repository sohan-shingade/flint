"""Async cron loop for recorder/ingestion cadence (D17, §9.1).

D17 chose "a plain async cron loop" over a durable scheduler for v1: recorders
flush, REST gap-fillers poll, and the lag metric exports on fixed intervals. This
is that loop, built so the *scheduling decision* is a pure, synchronously-testable
function (``tick``) and the async driver (``run``) is a thin wrapper around an
injected clock + sleep — no wall-clock read, no real ``asyncio.sleep`` in tests.

Placement note: this lives under ``data/ingest`` (its only v1 caller is the data
layer, and it sits inside this package's ownership boundary). If it is ever needed
by non-data workers it graduates to a general ``adapters/scheduler`` behind a port
— a move, not a rewrite (routed through team-lead, since ``adapters/`` is outside
this package).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass
class _Job:
    name: str
    interval_s: float
    fn: Callable[[], None]
    next_due: float


@dataclass
class CronScheduler:
    """Fire registered jobs on fixed intervals against an injected clock.

    ``tick(now)`` runs every job whose next-due time has passed and returns the
    names that fired — the whole scheduling policy, pure and testable. ``run``
    drives ``tick`` on a poll cadence until a stop condition; it is the only part
    that touches ``asyncio``.
    """

    _jobs: list[_Job] = field(default_factory=list)

    def add(
        self,
        name: str,
        interval_s: float,
        fn: Callable[[], None],
        *,
        first_at: float = 0.0,
    ) -> None:
        """Register ``fn`` to fire every ``interval_s``, first due at ``first_at``."""
        if interval_s <= 0:
            raise ValueError(f"interval_s must be positive, got {interval_s}")
        self._jobs.append(_Job(name, interval_s, fn, first_at))

    def tick(self, now: float) -> list[str]:
        """Run every job due at ``now``; return the names that fired.

        A job that fell multiple intervals behind fires once and its schedule
        catches up to the next future slot (no unbounded burst of catch-up runs).
        """
        fired: list[str] = []
        for job in self._jobs:
            if now >= job.next_due:
                job.fn()
                fired.append(job.name)
                # Advance to the next slot strictly after ``now``.
                missed = int((now - job.next_due) // job.interval_s) + 1
                job.next_due += missed * job.interval_s
        return fired

    async def run(
        self,
        *,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_s: float = 1.0,
        until: Callable[[], bool] | None = None,
    ) -> None:
        """Drive ``tick`` every ``poll_s`` until ``until()`` is true (or forever).

        ``clock``/``sleep`` are injected so a test can run the loop deterministically
        with a fake clock and a sleep that advances it and trips ``until``.
        """
        while until is None or not until():
            self.tick(clock())
            await sleep(poll_s)
