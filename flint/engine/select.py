"""Engine dispatch — pick the simulation substrate by name (§6.0, D29, plan §A1).

:func:`engine_for` maps an engine name to its :class:`SimulationEngine` factory. The
legacy bar loop is imported eagerly — its cost is already paid and it stays the
paper-lane substrate until N10 — while the Nautilus engine (the default backtest
substrate as of the N9 flip) is imported **lazily** inside its branch so a
candle-only user never loads the 443 MB / ~5 s dependency until a run actually
selects it. Consequently ``flint/engine/__init__`` (which imports this module)
never pulls anything Nautilus-related into the process; that import lives only
inside the ``"nautilus"`` branch.
"""

from __future__ import annotations

from .api import EngineFeed, EngineRunSpec
from .loop import BacktestEngine
from .money import money
from .portfolio import EventLog
from .state import PortfolioState


class UnknownEngineError(ValueError):
    """An engine name that this build cannot provide (unbuilt or unrecognized, §6.0)."""


#: The wire vocabulary for ``BacktestRequest.engine`` (§6.0, D29). Surfaces
#: enum-validate against this; services resolve through :func:`resolve_engine_name`.
KNOWN_ENGINES: tuple[str, ...] = ("auto", "legacy-bar", "nautilus")


def resolve_engine_name(name: str) -> str:
    """Map a requested engine name to the substrate that will actually run (§6.0).

    Pure string resolution — imports nothing, so services can validate and stamp
    the resolved engine (§19.6) without paying the Nautilus import. As of the N9
    default flip (2026-07-04, parity 18/18 zero-tolerance) ``"auto"`` resolves to
    ``"nautilus"`` — the Nautilus core is the default backtest substrate.
    ``"legacy-bar"`` stays explicitly selectable (deprecated for backtests; it
    remains the paper-lane substrate until N10). Raises :class:`UnknownEngineError`
    on anything outside :data:`KNOWN_ENGINES` — services surface that as the
    uniform §19.1 validation error.
    """
    if name in ("auto", "nautilus"):
        return "nautilus"
    if name == "legacy-bar":
        return "legacy-bar"
    raise UnknownEngineError(
        f"unknown engine {name!r} — expected one of 'auto', 'legacy-bar', 'nautilus'"
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


class LegacyBarEngine:
    """The legacy per-bar loop as a :class:`SimulationEngine` — zero behavior change.

    Unpacks an :class:`EngineFeed`/:class:`EngineRunSpec`, funds a fresh
    ``PortfolioState``, and delegates to :meth:`BacktestEngine.run` exactly as
    ``services.backtest._execute`` did before the seam existed — same construction
    order, same kwargs. This is the bar lane's substrate today and the parity oracle
    the Nautilus engine is held to during the migration (§6.0, §19.4).
    """

    name = "legacy-bar"

    def run(
        self,
        feed: EngineFeed,
        strategy: object,
        *,
        event_log: EventLog,
        spec: EngineRunSpec,
    ) -> PortfolioState:
        state = PortfolioState()
        state.fund(spec.fund_venue, money(spec.initial_capital))
        engine = BacktestEngine(
            event_log,
            config=spec.config,
            state=state,
            venue_spec=spec.venue_spec,
        )
        engine.run(
            feed.candles,
            marks=feed.marks,
            funding=feed.funding,
            books=feed.books,
            trades=feed.trades,
            oi=feed.oi,
            strategy=strategy,
        )
        return engine.state


def engine_for(name: str) -> type:
    """Return the :class:`SimulationEngine` factory for ``name`` (§6.0, D29).

    Resolves ``name`` through :func:`resolve_engine_name` — the single source of
    truth for the ``"auto"`` default (Nautilus as of the N9 flip) and for the
    ``UnknownEngineError`` on an unrecognized name. ``"legacy-bar"`` returns the
    legacy bar loop (deprecated for backtests, retained as the paper-lane substrate
    until N10); the resolved-Nautilus branch structures its import lazily so this
    module (and therefore ``flint/engine``) never imports Nautilus at load time.
    """
    resolved = resolve_engine_name(name)
    if resolved == "legacy-bar":
        return LegacyBarEngine
    # resolved == "nautilus" — lazy import: the 156 MB wheel / ~5 s cold start is
    # paid only when the Nautilus engine is actually selected. Importing here (never
    # at module load) is what keeps ``flint/engine`` Nautilus-free for candle-only
    # users. A missing/mispinned extra surfaces as an actionable ImportError from
    # ``_compat`` rather than a silent fallback to the legacy engine.
    from .nautilus import NautilusEngine

    return NautilusEngine
