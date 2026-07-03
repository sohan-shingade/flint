"""Build a parity feed from the committed real Tardis fragments (D26, §19.3).

The §19.3 golden set calls for *one real recorded fragment* alongside the
hand-authored goldens. This assembles one from the committed truncated real
first-of-month Tardis day files for Hyperliquid SOL, 2026-06-01
(``tests/fixtures/tardis/``) — the same fragments the D2 vendor tests run on.

**Honesty note (D26).** The real ``derivative_ticker`` fragment carries only
*predicted* funding rows (``funding_rate`` observations tagged
``rate_type="predicted"``, capture-timestamped) and **no populated
``funding_timestamp``** — Hyperliquid's CSV files ship it empty, so there is no
honest way to derive a *final* settlement series from this fragment. Per the N6
brief, the real-fragment golden therefore runs as **candles + predicted-only
funding, with settlement absent**: no funding is settled, so the parity it
proves is the fill / order / per-bar-equity path on real recorded prices. The
candles are aggregated from the real trade prints via the production
``CandleAggregator`` (aggregation of real prints, ``source="derived:trades"`` —
D26-compatible), exactly as the aggregator-parity test does.

The two fragments were captured in different windows (trades ≈00:00–00:03,
ticker ≈01:54–02:03 UTC), so the predicted rows fall outside the 3-candle span
and never reach the strategy; they are attached anyway because both engines
treat them identically, so their presence is itself a parity assertion that the
predicted-only path is inert on both lanes.
"""

from __future__ import annotations

import gzip
from pathlib import Path

from flint.core.models import Candle, FundingRate
from flint.data.ingest.vendors import TardisCsvClient
from flint.data.livefeed.aggregator import CandleAggregator
from flint.data.normalize import TradePrint as NormalizedTradePrint
from flint.engine.api import EngineFeed

from .builders import VENUE

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tardis"
MARKET = "SOL"  # the fixtures' raw symbol (Tardis files are per-coin, not per-perp)
RES_S = 60


def _fx(name: str) -> bytes:
    return gzip.decompress((FIXTURES / name).read_bytes())


def real_fragment_candles() -> list[Candle]:
    """Candles aggregated from the real SOL trade prints (60 s bars)."""
    from flint.data import Kind

    tables = TardisCsvClient.parse(
        "trades",
        _fx("hyperliquid_trades_2026-06-01_SOL.csv.gz"),
        market=MARKET,
        venue=VENUE,
    )
    trades = tables[Kind.TRADES].to_pylist()
    agg = CandleAggregator(MARKET, VENUE, RES_S)
    candles: list[Candle] = []
    for row in trades:
        for closed in agg.add_trade(
            NormalizedTradePrint(
                ts=row["ts"],
                market=MARKET,
                venue=VENUE,
                price=row["price"],
                size=row["size"],
                side=row["side"],
                trade_id=row["trade_id"],
            )
        ):
            candles.append(closed.candle)
    # The final bar is left open (no later crossing) and discarded, matching the
    # aggregator's live/paper behavior — never fabricated, never force-flushed.
    return candles


def real_fragment_predicted_funding() -> list[FundingRate]:
    """The real predicted funding rows from ``derivative_ticker`` (no finals)."""
    from flint.data import Kind

    tables = TardisCsvClient.parse(
        "derivative_ticker",
        _fx("hyperliquid_derivative_ticker_2026-06-01_SOL.csv.gz"),
        market=MARKET,
        venue=VENUE,
    )
    rows = tables[Kind.FUNDING].to_pylist()
    out: list[FundingRate] = []
    for r in rows:
        assert r["rate_type"] == "predicted", "real HL fragment must be predicted-only (D26)"
        out.append(
            FundingRate(
                market=MARKET,
                ts=r["ts"],
                rate_hourly=r["rate_hourly"],
                interval_s=r["interval_s"],
                price_basis=r["price_basis"],
                rate_type=r["rate_type"],
                venue=VENUE,
            )
        )
    return out


def feed_real_fragment() -> EngineFeed:
    """Candles-from-real-trades + real predicted funding (settlement absent)."""
    candles = real_fragment_candles()
    funding = real_fragment_predicted_funding()
    return EngineFeed(candles=candles, marks={}, funding={MARKET: funding})
