"""Turn the DataManager's Arrow tables into the engine's in-memory fixtures (§9→§6).

The engine is pure domain: the Nautilus core consumes an :class:`EngineFeed` built
from already-loaded ``list[Candle]`` + per-market ``dict``s of funding/marks, never
Arrow and never I/O (§6). The DataManager, one layer down, resolves a run's data needs into
``PreparedData.tables`` keyed ``(venue, market, Kind)`` (§9). This module is the
thin seam between them — it lives in ``services/`` (the composition layer) because
that is the only place allowed to touch both.

Only **executable-venue** legs feed the engine; ``signal_venues`` legs are the
read-only funding/basis lab's and never settle a position (D28), so they are
dropped here. Marks default to the candle close (the honest Tier-C approximation
when no oracle snapshot was recorded) and are overridden by real OI/mark rows
when the lake carried them — a recorded close is a real price, never fabricated
data (D26).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flint.core.models.market import Candle, FundingRate, MarkSnapshot
from flint.data.ranges import Kind
from flint.engine.api import EngineFeed


@dataclass(frozen=True)
class EngineInputs:
    """The inert per-market feeds a backtest consumes, assembled from Arrow.

    Callers read the fields directly (``candles`` / ``funding`` / ``marks``) —
    ``build_engine_feed`` folds them into the :class:`EngineFeed` the Nautilus core
    takes, and the sandbox path serializes them across the process boundary.
    """

    candles: list[Candle] = field(default_factory=list)
    funding: dict[str, list[FundingRate]] = field(default_factory=dict)
    marks: dict[str, list[MarkSnapshot]] = field(default_factory=dict)


def _rows(table) -> list[dict]:
    # ``to_pylist`` gives column-name -> value dicts; an empty table yields [].
    return table.to_pylist() if table.num_rows else []


def _assemble(
    tables: dict[tuple[str, str, Kind], object], executable_venues: set[str]
) -> tuple[list[Candle], dict[str, list[FundingRate]], dict[str, list[MarkSnapshot]]]:
    """Read executable legs into (ascending candles, funding, OI-derived marks).

    Candles from every executable leg are merged into one ascending stream (the
    engine walks a single time-ordered candle list across markets); funding and
    marks are keyed by market. ``marks`` here carries only *recorded* mark/index
    rows (from the OI kind) — close-derived synthesis is a separate, policy-gated
    step (§6.0 ``mark_policy``), never folded into assembly.
    """
    candles: list[Candle] = []
    funding: dict[str, list[FundingRate]] = {}
    marks: dict[str, list[MarkSnapshot]] = {}

    for (venue, market, kind), table in tables.items():
        if venue not in executable_venues:
            continue  # signal-only lab legs never feed the engine (D28)
        if kind is Kind.CANDLES:
            candles.extend(
                Candle(
                    ts=r["ts"],
                    open=r["open"],
                    high=r["high"],
                    low=r["low"],
                    close=r["close"],
                    volume=r["volume"],
                    market=r["market"],
                    resolution_s=r["resolution_s"],
                    venue=r["venue"],
                )
                for r in _rows(table)
            )
        elif kind is Kind.FUNDING:
            funding.setdefault(market, []).extend(
                FundingRate(
                    market=r["market"],
                    ts=r["ts"],
                    rate_hourly=r["rate_hourly"],
                    interval_s=r["interval_s"],
                    price_basis=r["price_basis"],
                    rate_type=r["rate_type"],
                    venue=r["venue"],
                )
                for r in _rows(table)
            )
        elif kind is Kind.OI:
            marks.setdefault(market, []).extend(
                MarkSnapshot(
                    market=r["market"],
                    ts=r["ts"],
                    mark_price=r["mark_price"],
                    index_price=r["index_price"],
                    venue=r["venue"],
                )
                for r in _rows(table)
            )

    candles.sort(key=lambda c: (c.ts, c.market))
    return candles, funding, marks


def _synthesize_close_marks(
    candles: list[Candle], marks: dict[str, list[MarkSnapshot]]
) -> None:
    """Add a close-derived ``MarkSnapshot`` for any market with no recorded mark.

    The OHLCV-only path (§6.0 ``mark_policy="close_derived"``): a market whose lake
    carried no mark/index rows gets one synthesized mark at its first candle so
    funding still settles against a real price — a recorded close is a real price,
    never fabricated (D26). Mutates ``marks`` in place.
    """
    for c in candles:
        if not marks.get(c.market):
            marks.setdefault(c.market, []).append(
                MarkSnapshot(
                    market=c.market,
                    ts=c.ts,
                    mark_price=c.close,
                    index_price=c.close,
                    venue=c.venue,
                )
            )


def _sort_series(
    funding: dict[str, list[FundingRate]], marks: dict[str, list[MarkSnapshot]]
) -> None:
    for series in marks.values():
        series.sort(key=lambda m: m.ts)
    for series in funding.values():
        series.sort(key=lambda f: f.ts)


def build_engine_inputs(
    tables: dict[tuple[str, str, Kind], object], executable_venues: set[str]
) -> EngineInputs:
    """Assemble ``EngineInputs`` from ``prepared.tables``, executable legs only.

    The legacy accessor (still used by the sandboxed source path): assembles the
    feed and always applies close-derived mark synthesis, preserving today's
    behavior. New bar-lane callers should prefer :func:`build_engine_feed`, which
    gates synthesis on ``mark_policy`` (§6.0).
    """
    candles, funding, marks = _assemble(tables, executable_venues)
    _synthesize_close_marks(candles, marks)
    _sort_series(funding, marks)
    return EngineInputs(candles=candles, funding=funding, marks=marks)


def build_engine_feed(
    tables: dict[tuple[str, str, Kind], object],
    executable_venues: set[str],
    *,
    mark_policy: str = "recorded",
) -> EngineFeed:
    """Assemble an :class:`EngineFeed` from ``prepared.tables``, executable legs only.

    Close-derived per-bar mark synthesis is fenced behind ``mark_policy`` (§6.0):
    ``"close_derived"`` reproduces the bar-lane's OHLCV-only synthesis exactly (the
    legacy behavior); ``"recorded"`` leaves marks to the recorded OI rows and never
    synthesizes one from a close (the tick lane's requirement). ``books``/``trades``/
    ``oi`` stay empty here — the bar lane feeds only candles + funding + marks.
    """
    candles, funding, marks = _assemble(tables, executable_venues)
    if mark_policy == "close_derived":
        _synthesize_close_marks(candles, marks)
    _sort_series(funding, marks)
    return EngineFeed(candles=candles, funding=funding, marks=marks)
