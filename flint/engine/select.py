"""Engine dispatch — pick the simulation substrate by name (§6.0, D29, plan §A1).

:func:`engine_for` maps an engine name to its :class:`SimulationEngine` factory. The
legacy bar loop is imported eagerly — it is the default and its cost is already
paid — while the Nautilus engine is imported **lazily** inside its branch so a
candle-only user never loads the 443 MB / ~5 s dependency. Consequently
``flint/engine/__init__`` (which imports this module) never pulls anything
Nautilus-related into the process; that import lives only inside the ``"nautilus"``
branch, which N2 fills in.
"""

from __future__ import annotations

from .api import EngineFeed, EngineRunSpec
from .loop import BacktestEngine
from .money import money
from .portfolio import EventLog
from .state import PortfolioState


class UnknownEngineError(ValueError):
    """An engine name that this build cannot provide (unbuilt or unrecognized, §6.0)."""


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

    ``"auto"`` resolves to the legacy bar loop for now; N9 flips ``"auto"`` to
    Nautilus once parity is green. ``"legacy-bar"`` selects it explicitly.
    ``"nautilus"`` raises until N2 builds the engine — the import is structured
    lazily inside the branch so this module (and therefore ``flint/engine``) never
    imports Nautilus.
    """
    if name in ("auto", "legacy-bar"):
        return LegacyBarEngine
    if name == "nautilus":
        # N2 fills this in with a lazy ``from .nautilus import NautilusEngine``;
        # until then, selecting it is a clear, structured failure — never a silent
        # fallback to the legacy engine.
        raise UnknownEngineError(
            "engine 'nautilus' is not built yet — it arrives in N2 of the D29 "
            "tick-driven migration; use 'legacy-bar' (or 'auto') for now"
        )
    raise UnknownEngineError(
        f"unknown engine {name!r} — expected one of 'auto', 'legacy-bar', 'nautilus'"
    )
