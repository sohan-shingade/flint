"""Sandbox failure taxonomy (§8.3).

Three failure shapes, kept distinct because callers react differently:

* ``StrategyError`` — the user's code raised (including a denied import). A bug
  in the strategy, surfaced back with the child-side exception type/message.
* ``SandboxTimeout`` — a CPU/wall limit tripped (busy loop, runaway). Not a code
  bug per se; the run is abandoned.
* ``SandboxViolation`` — the child died unexpectedly, blew the output cap, or the
  protocol broke. The containment boundary did its job.
"""

from __future__ import annotations


class SandboxError(Exception):
    """Base class for every sandbox outcome that isn't a clean result."""


class StrategyError(SandboxError):
    """The sandboxed strategy raised (or a denied import failed inside it)."""

    def __init__(self, error_type: str, message: str) -> None:
        self.error_type = error_type
        self.message = message
        super().__init__(f"{error_type}: {message}")


class SandboxTimeout(SandboxError):
    """A CPU (RLIMIT_CPU) or wall-clock limit killed the run."""


class SandboxViolation(SandboxError):
    """The child crashed, exceeded the output cap, or broke the protocol."""
