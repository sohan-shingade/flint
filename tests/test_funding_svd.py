"""FundingSvdStrategy — rank-1 funding factor + residual book (§8.4).

All funding streams are hand-authored (D26): four markets carry an identical
common pattern (scaled copies — exactly rank-1 after per-stream z-scoring) and
one market tracks it until a final-bar dislocation. The strategy must short
the dislocated market, leave the factor-conforming ones flat, and stay silent
until the panel window is full.
"""

from __future__ import annotations

from flint.core.models import Candle, FundingRate
from flint.strategy.templates import FundingSvdStrategy

HL = "hyperliquid"
HOUR_MS = 3_600_000
T0 = 1_700_000_000_000

# common hourly-funding factor pattern (typical 1e-5 scale), 8 bars
_FACTOR = [1.0, 2.0, 3.0, 4.0, 3.0, 2.0, 1.0, 2.0]

# market -> hand-authored stream. A–D are scaled copies of the factor; E follows
# it for 7 bars then dislocates hard upward on the last panel bar.
STREAMS: dict[str, list[float]] = {
    "AAA-PERP": [1e-5 * x for x in _FACTOR],
    "BBB-PERP": [2e-5 * x for x in _FACTOR],
    "CCC-PERP": [0.5e-5 * x for x in _FACTOR],
    "DDD-PERP": [1.5e-5 * x for x in _FACTOR],
    "EEE-PERP": [1e-5 * x for x in _FACTOR[:-1]] + [25e-5],
}
MARKETS = sorted(STREAMS)
WINDOW = len(_FACTOR)


class _PanelCtx:
    """Engine-ctx double serving per-market funding for the current bar index."""

    def __init__(self) -> None:
        self.bar = 0

    def funding_rate(self, market: str, venue=None) -> FundingRate | None:
        stream = STREAMS[market]
        idx = min(self.bar, len(stream) - 1)  # hold the last reading past the panel
        return FundingRate(
            market=market, ts=T0 + self.bar * HOUR_MS - 1, rate_hourly=stream[idx],
            interval_s=3600, price_basis="oracle", rate_type="predicted", venue=venue or HL,
        )

    def position(self, market, venue=None):
        return None

    def submit_order(self, *a, **k):
        raise AssertionError("templates must emit Signals, never call ctx.submit_order")


def _candle(market: str, bar: int) -> Candle:
    return Candle(
        ts=T0 + bar * HOUR_MS, open=100.0, high=100.0, low=100.0, close=100.0,
        volume=1000.0, market=market, resolution_s=3600, venue=HL,
    )


def _drive(strategy: FundingSvdStrategy, bars: int) -> dict[int, dict[str, list]]:
    """Run every market's candle through ``bars`` bars; return signals per bar/market."""
    ctx = _PanelCtx()
    out: dict[int, dict[str, list]] = {}
    for bar in range(bars):
        ctx.bar = bar
        out[bar] = {
            m: strategy.on_candle(_candle(m, bar), [], ctx) for m in MARKETS
        }
    return out


def test_flat_until_panel_window_is_full():
    strategy = FundingSvdStrategy(window=WINDOW, min_markets=4, n_legs=1)
    signals = _drive(strategy, bars=WINDOW)  # decision bars 0..7 see < WINDOW prior readings
    assert all(sigs == [] for per_bar in signals.values() for sigs in per_bar.values())


def test_shorts_the_dislocated_market_only():
    strategy = FundingSvdStrategy(window=WINDOW, min_markets=4, n_legs=1)
    signals = _drive(strategy, bars=WINDOW + 1)  # bar 8's panel = readings 0..7
    final = signals[WINDOW]
    assert [s.action for s in final["EEE-PERP"]] == ["short"]
    assert final["EEE-PERP"][0].market == "EEE-PERP"
    for market in MARKETS:
        if market != "EEE-PERP":
            assert final[market] == [], f"{market} conforms to the factor — must stay flat"


def test_factor_conforming_panel_produces_no_book():
    # E replaced by another pure scaled copy: nothing dislocates, nothing trades
    original = STREAMS["EEE-PERP"]
    STREAMS["EEE-PERP"] = [3e-5 * x for x in _FACTOR]
    try:
        strategy = FundingSvdStrategy(window=WINDOW, min_markets=4, n_legs=1)
        signals = _drive(strategy, bars=WINDOW + 1)
        assert all(sigs == [] for sigs in signals[WINDOW].values())
    finally:
        STREAMS["EEE-PERP"] = original
