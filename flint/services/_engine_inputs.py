"""Turn the DataManager's Arrow tables into the engine's in-memory fixtures (§9→§6).

The engine is pure domain: :meth:`BacktestEngine.run` consumes already-loaded
``list[Candle]`` + per-market ``dict``s of funding/marks, never Arrow and never
I/O (§6). The DataManager, one layer down, resolves a run's data needs into
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


@dataclass(frozen=True)
class EngineInputs:
    """The exact keyword feeds ``BacktestEngine.run`` takes, assembled from Arrow."""

    candles: list[Candle] = field(default_factory=list)
    funding: dict[str, list[FundingRate]] = field(default_factory=dict)
    marks: dict[str, list[MarkSnapshot]] = field(default_factory=dict)

    def run_kwargs(self) -> dict:
        return {"marks": self.marks, "funding": self.funding}


def _rows(table) -> list[dict]:
    # ``to_pylist`` gives column-name -> value dicts; an empty table yields [].
    return table.to_pylist() if table.num_rows else []


def build_engine_inputs(
    tables: dict[tuple[str, str, Kind], object], executable_venues: set[str]
) -> EngineInputs:
    """Assemble ``EngineInputs`` from ``prepared.tables``, executable legs only.

    Candles from every executable leg are merged into one ascending stream (the
    engine walks a single time-ordered candle list across markets). Funding and
    marks are keyed by market. When a market has candles but no recorded marks,
    a close-derived ``MarkSnapshot`` is synthesized per bar so funding still
    settles against a real price (Tier-C).
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
    for series in marks.values():
        series.sort(key=lambda m: m.ts)
    for series in funding.values():
        series.sort(key=lambda f: f.ts)
    return EngineInputs(candles=candles, funding=funding, marks=marks)
