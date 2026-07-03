"""Shared child-process spawn/scrub/limits machinery (§8.3).

Both sandbox entry modes go through this one function so the isolation is defined
in exactly one place: ``run_strategy_sandboxed`` (a single ``entry(input)`` call,
used by validation) and ``run_backtest_in_sandbox`` (a full engine run over inert
inputs, the D25 closure). It spawns an env-scrubbed, isolated (``python -I``)
subprocess, hands it the job as JSON over a pipe, enforces the wall-clock deadline
and output cap, and returns the child's parsed result envelope.

Interpreting that envelope — a decoded value vs. a strategy error — is left to the
caller, because the two modes carry different value shapes (an Arrow/JSON value in
one, an ``{events, rejections, notes}`` dict in the other). What is *not* the
caller's choice is the containment: env-scrub, ``-I``, the RLIMIT wrap, the
deadline, the output cap, and JSON-only decode all live here and nowhere else.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
from typing import Any

from flint.ports import ResourceQuota

from .errors import SandboxTimeout, SandboxViolation
from .oslayer import wrap_command

# Cap on the child's result envelope. A strategy that tries to return more is a
# violation, not a result. 8 MiB comfortably holds real tearsheet-sized output.
OUTPUT_CAP_BYTES = 8 * 1024 * 1024


def spawn_child(
    child_module: str, job: dict[str, Any], quota: ResourceQuota
) -> dict[str, Any]:
    """Run ``child_module`` on ``job`` in an isolated subprocess; return its envelope.

    Raises ``SandboxTimeout`` on a CPU/wall limit and ``SandboxViolation`` if the
    child crashed, blew the output cap, or broke the protocol. Never falls back to
    in-process execution. The returned dict is the child's ``{"ok", ...}`` envelope
    for the caller to interpret.
    """
    payload = json.dumps(job).encode("utf-8")

    # `-I` isolated mode: ignore env vars and user site-packages. `env={}` means
    # no FLINT_* secret can even exist in the child (unreachable by construction).
    cmd = wrap_command([sys.executable, "-I", "-m", child_module], quota)
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
        raise SandboxViolation(f"output exceeded cap of {OUTPUT_CAP_BYTES} bytes")

    if proc.returncode != 0:
        tail = stderr[-2000:].decode("utf-8", "replace")
        if proc.returncode == -signal.SIGXCPU:
            raise SandboxTimeout(f"strategy exceeded CPU limit of {quota.cpu_seconds}s")
        raise SandboxViolation(f"sandbox child exited with {proc.returncode}: {tail}")

    try:
        return json.loads(stdout)  # JSON only — child output is untrusted
    except json.JSONDecodeError as exc:
        raise SandboxViolation(f"malformed child output: {exc}") from None
