"""The parity harness — Nautilus vs frozen legacy goldens (§19.4, N10).

The legacy bar engine was deleted in N10, so parity is no longer engine-vs-engine.
Each scenario now runs **only** the Nautilus bar lane and asserts its stripped
event log equals the frozen golden recorded from the legacy engine at commit
``300e9b2`` (see :mod:`golden_store` and ``README.md``). The byte-exact contract
(§19.4b) is unchanged — zero tolerances — only its second operand moved from a live
second engine to a committed artifact.

Layer (a) input-parity died with the second engine: it was a *localizer* that
answered "did the same numbers reach the shared pure functions?" to distinguish a
math bug from an ordering bug across two live engines. With one engine held to a
frozen log there is nothing to localize between, so only the full byte diff (layer
(b), now :func:`golden_store.assert_matches_golden`) remains.
"""

from __future__ import annotations

from flint.adapters import InMemoryUserData
from flint.engine.api import EngineFeed, EngineRunSpec
from flint.engine.portfolio import EventLog
from flint.engine.select import engine_for
from flint.ports import TenantContext

from .golden_store import assert_matches_golden  # noqa: F401  (re-exported for tests)

NAUTILUS = "nautilus"


def run_nautilus(
    make_strategy,
    feed: EngineFeed,
    spec: EngineRunSpec,
    *,
    run_id: str = "nautilus",
) -> EventLog:
    """Drive one feed + a fresh strategy through the Nautilus bar lane; return its log.

    ``make_strategy`` is a factory (the strategies are stateful, fire once), matching
    how the goldens were recorded — one fresh instance per run.
    """
    log = EventLog(InMemoryUserData(), TenantContext.local(), run_id=run_id)
    engine_for(NAUTILUS)().run(feed, make_strategy(), event_log=log, spec=spec)
    return log
