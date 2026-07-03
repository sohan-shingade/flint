"""The sandbox child process (§8.3). Run as ``python -I -m flint.strategy.sandbox._child``.

Contract: read one JSON job from stdin, run the strategy under RLIMIT, write one
JSON result envelope to the *protocol channel*, exit. The child is spawned by the
parent with ``env={}`` — so no ``FLINT_*`` secret is reachable by construction —
and holds no reference to the parent's store, config, or memory.

stdout is fragile (it *is* the protocol channel), so the very first thing we do is
dup the real stdout aside and point fd 1 at stderr. Any ``print`` from strategy
code then lands on stderr and cannot corrupt the result envelope.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _apply_rlimits(quota: dict[str, Any]) -> None:
    """Clamp CPU and address space for the strategy phase (best-effort on macOS).

    Set AFTER the heavy infra imports (pyarrow; the Nautilus wheel when that
    engine is selected) so a low ``RLIMIT_AS`` can't crash interpreter/import
    startup; it then bounds the strategy's own allocations. RLIMIT_CPU counts
    whole-process CPU time (kills busy-loop threads too, unlike wall-clock) —
    and it counts from *process start*, so the budget is armed as
    ``quota + CPU already consumed``: the warm-up (interpreter boot + infra
    imports, ~5s of CPU for the Nautilus wheel) is excluded and the strategy
    phase gets the full quota it was promised (§8.3, plan §A9). The parent's
    wall-clock deadline still spans the whole child lifetime and is sized to
    carry the warm-up (``ResourceQuota.default_nautilus``).
    """
    try:
        import resource
    except ImportError:
        return  # non-POSIX (Windows) — not a supported sandbox platform

    import math

    usage = resource.getrusage(resource.RUSAGE_SELF)
    consumed = usage.ru_utime + usage.ru_stime  # warm-up CPU, excluded from budget
    cpu = max(1, math.ceil(quota["cpu_seconds"] + consumed))
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    except (ValueError, OSError):
        pass

    mem = int(quota["mem_bytes"])
    if mem > 0:
        for name in ("RLIMIT_AS", "RLIMIT_DATA"):
            limit = getattr(resource, name, None)
            if limit is not None:
                try:
                    resource.setrlimit(limit, (mem, mem))
                except (ValueError, OSError):
                    pass  # macOS/allocator may reject RLIMIT_AS — documented floor


def main() -> None:
    # Protect the protocol channel: move real stdout to a private fd, redirect
    # fd 1 to stderr so strategy prints can't corrupt the result envelope.
    result_fd = os.dup(1)
    os.dup2(2, 1)

    try:
        job = json.loads(sys.stdin.buffer.read())
        # Import infra BEFORE clamping RLIMIT_AS (pyarrow is large).
        from flint.strategy.sandbox.policy import build_safe_builtins
        from flint.strategy.sandbox.protocol import decode_value, encode_value

        input_obj = decode_value(job["input"])
        _apply_rlimits(job["quota"])

        strategy_globals: dict[str, Any] = {
            "__builtins__": build_safe_builtins(),
            "__name__": "__strategy__",
        }
        exec(job["source"], strategy_globals)  # noqa: S102 - the whole point

        entry = strategy_globals.get(job["entry"])
        if not callable(entry):
            raise NameError(f"entry {job['entry']!r} is not defined")

        result = entry(input_obj)
        out = {"ok": True, "value": encode_value(result)}
    except BaseException as exc:  # noqa: BLE001 - any failure must be serialized
        out = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}

    os.write(result_fd, json.dumps(out).encode("utf-8"))
    os.close(result_fd)


if __name__ == "__main__":
    main()
