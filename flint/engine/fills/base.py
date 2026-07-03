"""The ``FillModel`` seam — one interface, tiered implementations (§6.3).

Both backtest and paper route every order through a ``FillModel``: it turns an
``Order`` plus the market context at the fill's effective time into a
``FillResult`` (or ``None`` = rejected). Slice 3.1 shipped the seam and a naive
Tier-C model; slice 3.2 adds the real CLOB tier-A/B/C model (effective-time
latency, spread cross, book-walk VWAP, queue position, the Hyperliquid oracle
band) and the ``ORACLE_POOL`` stub behind this *same* interface.

``FillContext`` carries what the model needs at the moment of execution: the
reference price, the recorded book and trade prints at the effective time, the
oracle price and band, and the Tier-C parametric inputs. Fields are optional with
honest defaults so a caller that supplies only OHLCV gets a Tier-C fill and one
that supplies a fresh book + trades gets Tier A.

A ``FillResult`` pairs the ``Fill`` with **attribution flags** (``stale_book``,
``uncalibrated``, ``oracle_band_clipped``, ``estimated``) that the ``Fill``
dataclass has no field for but the tearsheet must show — the loop copies them
onto the FILL event so nobody mistakes a degraded fill for a clean one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from flint.core.models import Fill, Order, OrderbookSnapshot


@dataclass(frozen=True)
class TradePrint:
    """One executed trade on the tape — the evidence a maker queue cleared (§6.3).

    Tier A needs trade prints (not just book snapshots) to know how much volume
    traded through a resting order's price. Engine-local for now; if the Phase-2
    recorder promotes it to a core model, that swap goes through team-lead.
    """

    price: float
    size: float
    ts: int


@dataclass(frozen=True)
class FillContext:
    """What a ``FillModel`` sees at the effective time of a fill.

    ``reference_price`` anchors a Tier-C fill (the next bar's open for a T+1
    market order) or a resting order's trigger price. ``book``/``trades`` are the
    recorded microstructure at ``effective_ts``; absent or ``stale_book`` → the
    model degrades to Tier C. ``oracle_price`` + ``oracle_band_bps`` drive the
    Hyperliquid band clip. The Tier-C block (``bar_dollar_volume``,
    ``half_spread_bps``, ``impact_k``) parametrises the coarse model.
    """

    reference_price: float
    ts: int
    taker_fee_rate: float = 0.00045  # HL Tier 0 (verified) — set from VenueSpec
    maker_fee_rate: float = 0.00015
    # --- CLOB tier-A/B inputs (absent → Tier C) ---
    book: OrderbookSnapshot | None = None
    trades: tuple[TradePrint, ...] = ()
    oracle_price: float = 0.0
    oracle_band_bps: float = 0.0  # 0 = no band
    stale_book: bool = False  # book older than the venue staleness threshold
    queue_ahead: float = 0.0  # resting size ahead of a maker order when posted
    volume_at_price: float = 0.0  # Tier-B probabilistic queue proxy
    effective_ts: int = 0  # submit_ts + latency (0 → use ts)
    # --- Tier-C parametric inputs ---
    bar_dollar_volume: float = 0.0  # for the sqrt-impact law; 0 = no volume evidence
    half_spread_bps: float = 1.0  # market-cap bucket (majors 1 / mid 3 / tail 10)
    impact_k: float = 0.1  # sqrt-law coefficient (uncalibrated default)
    price_sig_figs: int = 0  # venue price rounding (0 = none)


@dataclass(frozen=True)
class FillResult:
    """A ``Fill`` plus attribution flags the loop records on the FILL event."""

    fill: Fill
    flags: tuple[str, ...] = field(default_factory=tuple)


class FillModel(Protocol):
    """Turn an ``Order`` into a ``FillResult`` at its effective time, or reject it."""

    def fill(self, order: Order, ctx: FillContext) -> FillResult | None:
        """Return the resulting ``FillResult``, or ``None`` if rejected."""
        ...
