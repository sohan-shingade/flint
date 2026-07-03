"""Async cron scheduler — pure tick policy + deterministic async run (2.5, D17)."""

from __future__ import annotations

import asyncio

import pytest

from flint.data.ingest import CronScheduler


def test_add_rejects_non_positive_interval():
    sched = CronScheduler()
    with pytest.raises(ValueError):
        sched.add("bad", 0, lambda: None)


def test_tick_fires_jobs_at_their_intervals():
    log: list[str] = []
    sched = CronScheduler()
    sched.add("flush", 5.0, lambda: log.append("flush"))
    sched.add("lag", 10.0, lambda: log.append("lag"))

    assert sched.tick(0.0) == ["flush", "lag"]  # both due at first_at=0
    assert sched.tick(3.0) == []
    assert sched.tick(5.0) == ["flush"]
    assert sched.tick(9.0) == []
    assert sched.tick(10.0) == ["flush", "lag"]
    assert log == ["flush", "lag", "flush", "flush", "lag"]


def test_tick_catches_up_without_bursting():
    # A job 4 intervals behind fires once and re-slots into the future.
    fires: list[float] = []
    sched = CronScheduler()
    sched.add("j", 5.0, lambda: fires.append(1.0))
    sched.tick(0.0)  # fire at 0, next due 5
    assert sched.tick(21.0) == ["j"]  # 5,10,15,20 all passed -> fire once
    assert len(fires) == 2
    # Next slot is strictly after 21 (25), so an immediate re-tick does nothing.
    assert sched.tick(21.0) == []


def test_first_at_defers_the_initial_fire():
    log: list[str] = []
    sched = CronScheduler()
    sched.add("j", 5.0, lambda: log.append("j"), first_at=100.0)
    assert sched.tick(0.0) == []
    assert sched.tick(100.0) == ["j"]


def test_async_run_drives_tick_until_stop():
    log: list[str] = []
    sched = CronScheduler()
    sched.add("tick", 1.0, lambda: log.append("tick"))

    clock = [0.0]
    ticks = [0]

    async def fake_sleep(_seconds: float) -> None:
        clock[0] += 1.0
        ticks[0] += 1

    def until() -> bool:
        return ticks[0] >= 3

    asyncio.run(sched.run(clock=lambda: clock[0], sleep=fake_sleep, poll_s=1.0, until=until))
    # Loop ran tick at t=0,1,2 then stop condition tripped.
    assert log == ["tick", "tick", "tick"]
