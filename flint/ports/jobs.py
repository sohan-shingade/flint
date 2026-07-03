"""``JobRunnerPort`` — run a job under CPU/memory/wall quotas (§2.7).

The engine hands a unit of work to *some* runner without knowing whether it is
an in-process thread pool (laptop) or a worker container off a queue (cloud).
The quota travels with the job so the same call site is safe in both: locally
the sandbox (§8.3) enforces it via ``RLIMIT`` + a wall-clock kill; in the cloud
the orchestrator enforces it. A runner that cannot enforce a quota must still
accept and record it, never silently drop it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from .tenant import TenantContext

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ResourceQuota:
    """The CPU/memory/wall ceiling a job runs under. Enforced by the runner."""

    cpu_seconds: float  # RLIMIT_CPU ceiling (§8.3)
    mem_bytes: int  # RLIMIT_AS ceiling
    wall_seconds: float  # wall-clock kill deadline (catches sleeps/IO stalls)

    @classmethod
    def default(cls) -> "ResourceQuota":
        """A conservative single-backtest ceiling (30s CPU, 2 GiB, 60s wall)."""
        return cls(cpu_seconds=30.0, mem_bytes=2 * 1024**3, wall_seconds=60.0)

    @classmethod
    def default_nautilus(cls) -> "ResourceQuota":
        """The ceiling for a sandbox run on the Nautilus substrate (§8.3, §19.4).

        Measured empirically in N7 (macOS/arm64, sandbox child, 10-bar run on the
        N2 gate fixture): peak child RSS 233 MB on Nautilus vs 89 MB on the
        legacy engine — the wheel's Rust core dominates, the strategy's working
        set rides on top. ``RLIMIT_AS`` bounds address space, not RSS (the Rust
        core's mappings put VA well above RSS), so the default is 4 GiB: ~17x
        the measured baseline RSS, room for real strategy working sets (pandas/
        sklearn frames) on top of the substrate, aligned with the §19.4 4 GB
        peak-RSS budget, and authoritative on Linux. On macOS ``RLIMIT_AS`` is
        advisory-grade (the kernel may ignore the clamp); that posture is
        unchanged from :meth:`default` — best-effort clamp, with the CPU and
        wall limits as the hard backstop (§8.3).

        Wall is raised to 120s: the child pays the ~3-5s ``nautilus_trader``
        import before the engine walks, and the parent's wall deadline spans the
        whole child lifetime, so it carries the warm-up allowance. CPU stays at
        30s — the CPU budget clock is armed *after* the import completes
        (``sandbox._child._apply_rlimits`` offsets already-consumed CPU), so it
        budgets the strategy phase only, warm-up excluded.
        """
        return cls(cpu_seconds=30.0, mem_bytes=4 * 1024**3, wall_seconds=120.0)


class JobRunnerPort(ABC):
    """Run ``fn`` for ``tenant`` under ``quota`` and return its result."""

    @abstractmethod
    def submit(
        self, tenant: TenantContext, fn: Callable[[], T], quota: ResourceQuota
    ) -> T:
        """Execute ``fn`` under ``quota`` and return its result.

        Raises if the job exceeds a quota or fails. The Phase-1 in-process
        adapter runs ``fn`` synchronously and records the quota without
        enforcing it; real enforcement arrives with the sandbox (§8.3).
        """
        ...
