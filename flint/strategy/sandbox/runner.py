"""``run_strategy_sandboxed`` — the ONE way strategy code ever executes (§8.3).

There is exactly one entry and **no in-process fallback for any argument
combination**. Round-1's hole was a service path that skipped sandbox routing
precisely when funding/orderbook args were present (the normal perp case); the
lesson is that isolation must be unconditional. Every caller — CLI, REST, MCP,
optimizer workers — comes through here.

The parent spawns an env-scrubbed, isolated (`python -I`) subprocess, hands it
the job over a pipe, enforces the wall-clock deadline and output cap, and decodes
the result with JSON/Arrow only (never pickle).
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
from typing import Any

from flint.ports import ResourceQuota

from .errors import SandboxTimeout, SandboxViolation, StrategyError
from .oslayer import wrap_command
from .protocol import decode_value, encode_value
from .screen import screen_or_raise

_CHILD_MODULE = "flint.strategy.sandbox._child"

# Cap on the child's result envelope. A strategy that tries to return more is a
# violation, not a result. 8 MiB comfortably holds real tearsheet-sized output.
OUTPUT_CAP_BYTES = 8 * 1024 * 1024


def run_strategy_sandboxed(
    source: str,
    entry: str = "run",
    input_value: Any = None,
    *,
    quota: ResourceQuota | None = None,
    screen: bool = False,
) -> Any:
    """Run ``source``'s ``entry(input_value)`` in the sandbox; return its result.

    Raises ``StrategyError`` if the strategy raised (incl. a denied import),
    ``SandboxTimeout`` on a CPU/wall limit, ``SandboxViolation`` if the child
    crashed or blew the output cap. Never falls back to in-process execution.

    ``screen=True`` runs the static AST/import screen first (§8.3) and raises
    ``StrategyScreenError`` with line-precise issues *before* a subprocess is
    spawned — the fast, lint-grade UX a surface (CLI/API/MCP/optimizer) turns on.
    It is opt-in, never load-bearing: the boundary contains code the screen would
    reject, so the security guarantee never depends on it (which is why the
    escape-suite runs with ``screen=False``).
    """
    if screen:
        screen_or_raise(source)
    quota = quota or ResourceQuota.default()
    job = {
        "source": source,
        "entry": entry,
        "input": encode_value(input_value),
        "quota": {
            "cpu_seconds": quota.cpu_seconds,
            "mem_bytes": quota.mem_bytes,
            "wall_seconds": quota.wall_seconds,
        },
    }
    payload = json.dumps(job).encode("utf-8")

    # `-I` isolated mode: ignore env vars and user site-packages. `env={}` means
    # no FLINT_* secret can even exist in the child (unreachable by construction).
    cmd = wrap_command([sys.executable, "-I", "-m", _CHILD_MODULE], quota)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={},
    )

    try:
        stdout, stderr = proc.communicate(payload, timeout=quota.wall_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise SandboxTimeout(
            f"strategy exceeded wall-clock limit of {quota.wall_seconds}s"
        ) from None

    if len(stdout) > OUTPUT_CAP_BYTES:
        raise SandboxViolation(
            f"output exceeded cap of {OUTPUT_CAP_BYTES} bytes"
        )

    if proc.returncode != 0:
        tail = stderr[-2000:].decode("utf-8", "replace")
        if proc.returncode == -signal.SIGXCPU:
            raise SandboxTimeout(f"strategy exceeded CPU limit of {quota.cpu_seconds}s")
        raise SandboxViolation(
            f"sandbox child exited with {proc.returncode}: {tail}"
        )

    try:
        out = json.loads(stdout)  # JSON only — child output is untrusted
    except json.JSONDecodeError as exc:
        raise SandboxViolation(f"malformed child output: {exc}") from None

    if out.get("ok"):
        return decode_value(out["value"])
    raise StrategyError(out.get("error_type", "Error"), out.get("error", ""))
