"""Granularity tiers — what data a backtest runs on, as a request-path axis (§B7).

One core, three fidelity rungs (§2 tier ladder): ``candles`` (free OHLCV +
funding — the floor every run can fall back to), ``ticks`` (trade prints drive
fills; quotes sharpen them when present), and ``book`` (full L2 replay).
``FUNDING`` is required by *every* tier — a perp's PnL is wrong without it —
and always means the **final/settled** series for coverage purposes (§6.4);
the predicted series is a fidelity concern, never a tier or gate one.

The DataManager resolves a request's ``granularity`` against these tiers with
coverage queries only (no fetching to decide): ``auto`` picks the highest tier
whose required kinds fully cover the requested range for every executable leg,
falling back to the ``candles`` floor; an *explicit* tier with a coverage gap
is a structured §19.1 rejection carrying the per-leg per-kind coverage and the
machine-readable ways out (run at bars / clip / vendor backfill / record
forward) — never an exception surfaced as a fault.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ranges import Kind

#: The wire vocabulary for the request field (``"auto"`` resolves to a tier).
GRANULARITIES: tuple[str, ...] = ("auto", "candles", "ticks", "book")


@dataclass(frozen=True, slots=True)
class Tier:
    """One granularity rung: the kinds it needs, and the kinds it merely wants.

    ``required`` kinds must fully cover the requested range for the tier to be
    available (FUNDING meaning the final series); a gap in a ``flagged`` kind
    degrades fidelity with a flag instead (ticks runs without recorded quotes
    still run — fills just lean on trades alone).
    """

    name: str
    required: tuple[Kind, ...]
    flagged: tuple[Kind, ...] = ()

    @property
    def market_data_kinds(self) -> tuple[Kind, ...]:
        """The tier's kinds beyond the funding backbone — what it adds to a run."""
        return tuple(
            k for k in (*self.required, *self.flagged) if k is not Kind.FUNDING
        )


CANDLES_TIER = Tier("candles", required=(Kind.CANDLES, Kind.FUNDING))
TICKS_TIER = Tier(
    "ticks", required=(Kind.TRADES, Kind.FUNDING), flagged=(Kind.QUOTES,)
)
BOOK_TIER = Tier(
    "book", required=(Kind.TRADES, Kind.QUOTES, Kind.BOOK_DELTA, Kind.FUNDING)
)

#: Lowest → highest; auto-resolution walks this from the top.
TIERS: tuple[Tier, ...] = (CANDLES_TIER, TICKS_TIER, BOOK_TIER)

TIER_BY_NAME: dict[str, Tier] = {t.name: t for t in TIERS}
