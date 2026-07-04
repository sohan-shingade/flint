"""The sandbox child for a FULL user-source backtest (§8.3, D25 closure).

Run as ``python -I -m flint.strategy.sandbox._child_backtest``. Where ``_child``
runs a single ``entry(input)`` call (validation's liveness/leak probe), this child
runs the *entire* engine over a user strategy, so the OS boundary contains the
whole run and not merely pre-run validation — the ruling that closes D25 after
7.3. It receives inert, serialized engine inputs (candles/funding/marks: copied
bytes, no live handle), compiles the user source under the restricted builtins,
wraps it with the trusted ``build_adapter``, walks the substrate the job selects
through the same ``engine/select.py`` seam as the in-process path (§6.0 — the
legacy bar loop or the Nautilus engine; N7), and writes the resulting event log
+ drained rejections/notes + engine attribution back over the protocol channel.
The parent persists those events through services; this child touches no store
of the parent's — it logs to an in-process throwaway. When Nautilus is selected,
its wheel is imported before the user source is exec'd and before the RLIMIT
clamp, so the CPU budget clock starts after warm-up (plan §A9).

Only the *user source* runs under ``build_safe_builtins`` (import allowlist +
minimal builtins); the engine, adapter, and models are trusted platform code and
import normally. Containment is identical to ``_child``: the child is spawned with
``env={}`` so no ``FLINT_*`` secret is reachable by construction, it holds no
reference to parent memory, and RLIMIT bounds it. As in ``_child``, stdout is the
protocol channel, so fd 1 is redirected to stderr first and heavy infra is
imported *before* ``RLIMIT_AS`` is clamped (pyarrow/engine are large).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from ._child import _apply_rlimits  # reuse the exact RLIMIT floor


def _run(job: dict[str, Any]) -> dict[str, Any]:
    """Compile + run the user backtest inside the child; return its result data."""
    from flint.adapters import InMemoryUserData
    from flint.core.models.market import Candle, FundingRate, MarkSnapshot
    from flint.engine import EngineConfig
    from flint.engine.api import EngineFeed, EngineRunSpec
    from flint.engine.portfolio import EventLog
    from flint.engine.select import engine_for, resolve_engine_name
    from flint.live import build_adapter
    from flint.ports import TenantContext
    from flint.strategy.ml import MLStrategy
    from flint.strategy.templates.registry import TemplateSpec
    from flint.venues import HYPERLIQUID

    from .policy import build_safe_builtins

    # Engine selection happens through the same seam as the in-process template
    # path (flint/engine/select.py, §6.0) — the child never hand-rolls a substrate.
    resolved = resolve_engine_name(job.get("engine", "nautilus"))
    engine_versions: dict[str, str] = {}
    if resolved == "nautilus":
        # Import the Nautilus wheel NOW — before the RLIMIT clamp (a low memory
        # floor must bound the strategy, not crash the 443 MB import) and before
        # the untrusted source is exec'd (warm-up is trusted platform time). The
        # CPU budget clock is armed after this completes: ``_apply_rlimits``
        # offsets already-consumed CPU, so the ~5s import never eats the
        # strategy's budget (plan §A9). ``_compat`` asserts the exact pin here.
        from flint.engine.nautilus import _compat

        engine_versions["nautilus_trader"] = _compat.NAUTILUS_REQUIRED

    engine_cls = engine_for(job.get("engine", "nautilus"))

    # Clamp RLIMIT_AS/CPU only AFTER the heavy infra above is imported (pyarrow/
    # engine/nautilus are large), so a low memory floor bounds the strategy's own
    # allocations rather than crashing interpreter/import startup, and the CPU
    # budget excludes warm-up — same ordering as ``_child``.
    _apply_rlimits(job["quota"])

    raw = job["inputs"]
    candles = [Candle(**d) for d in raw["candles"]]
    funding = {m: [FundingRate(**d) for d in lst] for m, lst in raw["funding"].items()}
    marks = {m: [MarkSnapshot(**d) for d in lst] for m, lst in raw["marks"].items()}

    # Compile the UNTRUSTED source under the restricted builtins — the same
    # containment the validation probe uses — and pick the Strategy subclass by the
    # AST-derived name the parent passed (never ``globals()``, a denied builtin).
    strategy_globals: dict[str, Any] = {
        "__builtins__": build_safe_builtins(),
        "__name__": "__strategy__",
    }
    exec(job["source"], strategy_globals)  # noqa: S102 - the whole point of the sandbox
    cls = strategy_globals.get(job["class_name"])
    if not isinstance(cls, type):
        raise NameError(f"strategy class {job['class_name']!r} is not defined")

    spec = TemplateSpec(
        name=cls.__name__,
        strategy_cls=cls,
        summary="user-submitted strategy",
        category="user",
        is_ml=issubclass(cls, MLStrategy),
    )
    # A throwaway tenant + store: the child's event log is not the parent's store,
    # and event rows carry no tenant, so this never touches parity.
    tenant = TenantContext.local()
    adapter = build_adapter(
        spec,
        tenant,
        seed=job["seed"],
        strategy_id=job["run_id"],
        overrides=dict(job["overrides"]),
    )
    log = EventLog(InMemoryUserData(), tenant, job["run_id"])
    # The engine seam (§6.0): the substrate takes the feed/spec and funds the
    # portfolio itself. As of N10 the only substrate is the Nautilus core; the child
    # dispatches through ``engine_for`` exactly as the in-process path does.
    feed = EngineFeed(candles=candles, funding=funding, marks=marks)
    spec = EngineRunSpec(
        config=EngineConfig(),
        venue_spec=HYPERLIQUID,
        initial_capital=job["initial_capital"],
        fund_venue=job["fund_venue"],
        # The parent already applied close-derived mark synthesis while building
        # the inert inputs (services._engine_inputs.build_engine_inputs), so the
        # feed's marks are complete here and no engine-side synthesis exists.
    )
    engine_cls().run(feed, adapter, event_log=log, spec=spec)

    return {
        # Raw stored rows, so the parent re-persists them bit-for-bit (parity).
        "events": log.read_raw(),
        "rejections": [_rejection_row(r) for r in adapter.drain_rejections()],
        "notes": list(adapter.tearsheet_notes()),
        # Attribution (§19.4/§19.6): the substrate that actually ran, and the
        # exact Nautilus pin when that substrate is Nautilus.
        "engine": resolved,
        "engine_versions": engine_versions,
    }


def _rejection_row(r: Any) -> dict[str, Any]:
    """The exact shape ``services.backtest._rej`` produces, so the parent slots it in."""
    return {
        "reason": r.reason,
        "detail": r.detail,
        "ts": r.ts,
        "venue": r.signal.venue,
        "market": r.signal.market,
        "action": r.signal.action,
    }


def main() -> None:
    # Protect the protocol channel: move real stdout to a private fd, redirect
    # fd 1 to stderr so strategy prints can't corrupt the result envelope.
    result_fd = os.dup(1)
    os.dup2(2, 1)

    try:
        job = json.loads(sys.stdin.buffer.read())
        out = {"ok": True, "value": _run(job)}  # _run clamps RLIMIT after its imports
    except BaseException as exc:  # noqa: BLE001 - any failure must be serialized
        out = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}

    os.write(result_fd, json.dumps(out).encode("utf-8"))
    os.close(result_fd)


if __name__ == "__main__":
    main()
