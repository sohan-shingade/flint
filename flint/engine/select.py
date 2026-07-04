"""Engine dispatch — pick the simulation substrate by name (§6.0, D29, plan §A1).

:func:`engine_for` maps an engine name to its :class:`SimulationEngine` factory.
As of N10 there is exactly one executable substrate — the Nautilus core (the
default backtest engine since the N9 flip, and the paper-lane substrate since
N10.1) — imported **lazily** inside its branch so a candle-only user never loads
the 443 MB / ~5 s dependency until a run actually selects it. Consequently
``flint/engine/__init__`` (which imports this module) never pulls anything
Nautilus-related into the process; that import lives only inside the
``"nautilus"`` branch. The legacy bar loop was deleted in N10, so its name now
rejects like any other unknown engine.
"""

from __future__ import annotations


class UnknownEngineError(ValueError):
    """An engine name that this build cannot provide (unbuilt or unrecognized, §6.0)."""


#: The wire vocabulary for ``BacktestRequest.engine`` (§6.0, D29). Surfaces
#: enum-validate against this; services resolve through :func:`resolve_engine_name`.
KNOWN_ENGINES: tuple[str, ...] = ("auto", "nautilus")


def resolve_engine_name(name: str) -> str:
    """Map a requested engine name to the substrate that will actually run (§6.0).

    Pure string resolution — imports nothing, so services can validate and stamp
    the resolved engine (§19.6) without paying the Nautilus import. As of the N9
    default flip (2026-07-04, parity 18/18 zero-tolerance) ``"auto"`` resolves to
    ``"nautilus"`` — the Nautilus core is the only backtest substrate. The legacy
    bar engine was removed in N10, so ``"legacy-bar"`` rejects like any other
    unknown name. Raises :class:`UnknownEngineError` on anything outside
    :data:`KNOWN_ENGINES` — services surface that as the uniform §19.1 validation
    error.
    """
    if name in ("auto", "nautilus"):
        return "nautilus"
    if name == "legacy-bar":
        raise UnknownEngineError(
            "legacy bar engine was removed in N10 — every backtest now runs on the "
            "Nautilus core; use engine='auto'"
        )
    raise UnknownEngineError(
        f"unknown engine {name!r} — expected one of 'auto', 'nautilus'"
    )


def installed_nautilus_version() -> str:
    """The exact ``nautilus_trader`` version this process runs (§19.4/§19.6).

    Imported lazily through the ``_compat`` churn firewall, which asserts the
    exact pin — so by construction this equals the pinned version whenever a
    Nautilus run is possible at all. Callers stamp it into the run manifest so a
    churn-induced numeric change is attributable, never silent (§19.4). Only call
    this when the Nautilus engine was actually selected (the import is then
    already paid); calling it without the extra raises ``_compat``'s actionable
    ImportError.
    """
    from .nautilus import _compat

    return _compat.NAUTILUS_REQUIRED


def engine_for(name: str) -> type:
    """Return the :class:`SimulationEngine` factory for ``name`` (§6.0, D29).

    Resolves ``name`` through :func:`resolve_engine_name` — the single source of
    truth for the ``"auto"`` default (Nautilus as of the N9 flip) and for the
    ``UnknownEngineError`` on an unrecognized name (including the legacy bar engine
    removed in N10). The resolved-Nautilus branch structures its import lazily so
    this module (and therefore ``flint/engine``) never imports Nautilus at load
    time.
    """
    resolve_engine_name(name)  # "auto"/"nautilus" pass; everything else raises.
    # Lazy import: the 156 MB wheel / ~5 s cold start is paid only when the Nautilus
    # engine is actually selected. Importing here (never at module load) is what keeps
    # ``flint/engine`` Nautilus-free for candle-only users. A missing/mispinned extra
    # surfaces as an actionable ImportError from ``_compat``.
    from .nautilus import NautilusEngine

    return NautilusEngine
