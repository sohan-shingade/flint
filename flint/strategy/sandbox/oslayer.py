"""OS-isolation layer — stubbed hooks for nsjail/bubblewrap + seccomp (§8.3).

On Linux the hosted/agent deployment **requires** wrapping the child in an OS
jail: network namespace off, read-only rootfs, ephemeral scratch, seccomp-bpf,
no-new-privileges. That layer is what contains C-extension native code and a
successful `getattr` chain to `os`. It is **not implemented yet** — this module
is the seam so the runner already routes through it.

On platforms without it (macOS dev laptops), the subprocess + RLIMIT + env-scrub
layer stands alone. Guarantees that still hold on that floor:
  * secrets are unreachable (child env is empty — not filtered, *absent*);
  * the child shares no memory with the parent (no store/config/handle refs);
  * CPU/memory/wall limits bound resource abuse.
Guarantees that DO need the OS layer (and are therefore NOT provided on the
floor): blocking filesystem reads and network access from a `getattr`-chain
escape. The hosted deployment must run the OS layer; this is asserted at that
tier, not here.
"""

from __future__ import annotations

from collections.abc import Sequence

from flint.ports import ResourceQuota


def os_layer_available() -> bool:
    """Whether an OS jail (nsjail/bwrap) is wired up. False until Linux hardening."""
    return False


def wrap_command(cmd: Sequence[str], quota: ResourceQuota) -> list[str]:
    """Wrap ``cmd`` in the OS jail if available; otherwise pass it through.

    The Linux implementation will prepend an ``nsjail``/``bwrap`` invocation with
    ``--disable-clone-newnet`` off, ``--rlimit_*`` from ``quota``, a read-only
    rootfs, and a seccomp profile. For now it is an identity so the seam exists.
    """
    return list(cmd)
