"""strategy.sandbox — the OS-isolated strategy runner (D25, §8.3).

One entrypoint, no in-process fallback: ``run_strategy_sandboxed`` spawns an
env-scrubbed subprocess with RLIMIT + minimal ``__builtins__`` + an import
allowlist, exchanging Arrow/dicts over a pipe. nsjail/seccomp is the Linux
hardening (stubbed in ``oslayer``); subprocess + RLIMIT + env-scrub is the
documented floor on macOS.
"""

from __future__ import annotations

from .errors import SandboxError, SandboxTimeout, SandboxViolation, StrategyError
from .oslayer import os_layer_available
from .runner import OUTPUT_CAP_BYTES, run_strategy_sandboxed

__all__ = [
    "run_strategy_sandboxed",
    "OUTPUT_CAP_BYTES",
    "os_layer_available",
    "SandboxError",
    "StrategyError",
    "SandboxTimeout",
    "SandboxViolation",
]
