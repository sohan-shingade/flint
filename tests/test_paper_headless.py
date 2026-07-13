"""Headless (poll-driven) paper advance — ``replay_to`` / ``catch_up`` (§6.7).

A poll-driven scheduler cannot hold a WS pump: it must advance a paper session
from lake data alone. These tests prove the headless entry points reuse the
exact gap-replay machinery a reconnect uses — bars are never skipped, never
forward-filled (D26), never double-processed — and that the headless cursor
anchor survives a kill/restart via the persisted run head. All inputs are
hand-authored recorded-shape fragments (D26).
"""

from __future__ import annotations

import pytest

from flint.adapters import InMemoryUserData
from flint.core.models import Candle, FundingRate, MarkSnapshot, Signal
from flint.data.livefeed import InMemoryGapSource, LiveFeed
from flint.engine.portfolio import EventLog
from flint.engine.portfolio.events import FILL
from flint.live import PaperSession, SlippageBaseline, build_adapter
from flint.ports import TenantContext
from flint.strategy import Strategy
from flint.strategy.templates.registry import TemplateSpec

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
HOUR_MS = HOUR_S * 1000
BASE_HOUR = 472_223


def bar(n: int) -> int:
    return (BASE_HOUR + n) * HOUR_MS


def _candle(n: int, px: float) -> Candle:
    return Candle(
        ts=bar(n),
        open=px,
        high=px,
        low=px,
        close=px,
        volume=1.0,
        market=MARKET,
        resolution_s=HOUR_S,
        venue=VENUE,
    )


def _mark(n: int, px: float) -> MarkSnapshot:
    return MarkSnapshot(MARKET, bar(n) + 500, px, px, VENUE)


def _lake(first: int, last: int, px: float = 100.0) -> InMemoryGapSource:
    """A lake holding flat bars ``first..last`` inclusive, with per-bar marks."""
    key = (VENUE, MARKET)
    return InMemoryGapSource(
        candles={key: [_candle(n, px) for n in range(first, last + 1)]},
        marks={key: [_mark(n, px) for n in range(first, last + 1)]},
    )


class _LongIfFlat(Strategy):
    params = dict(venue=VENUE, notional_usd=1000.0)

    def on_candle(self, candle, history, ctx):
        if ctx.position(candle.market, self.params["venue"]) is None:
            return Signal.long(
                candle.market,
                self.params["venue"],
                size_usd=self.params["notional_usd"],
            )
        return []


_LONG_SPEC = TemplateSpec(
    name="_test_long", strategy_cls=_LongIfFlat, summary="test", category="technical"
)


def _session(store, run_id, *, gap_source, resume=False):
    tenant = TenantContext.local()
    common = dict(
        tenant=tenant,
        store=store,
        run_id=run_id,
        market=MARKET,
        resolution_s=HOUR_S,
        gap_source=gap_source,
        slippage_baseline=SlippageBaseline(0.0, 1.0),
    )
    if resume:
        return PaperSession.resume(adapter=build_adapter(_LONG_SPEC, tenant), **common)
    return PaperSession.create(template=_LONG_SPEC, initial_capital="100000", **common)


def _fills(store, run_id):
    events = EventLog(store, TenantContext.local(), run_id).read()
    return [e for e in events if e.kind == FILL]


# --- LiveFeed.replay_to ------------------------------------------------------


def test_replay_to_first_call_anchors_and_replays_nothing():
    feed = LiveFeed(MARKET, resolution_s=HOUR_S, gap_source=_lake(0, 9))
    assert feed.cursor is None

    # Mid-bar-3 "now": the session starts from now — no pre-session history.
    assert feed.replay_to(bar(3) + 120_000) == []
    assert feed.cursor == bar(2)  # anchored at the last bar closed before now
    assert feed.recoveries == []  # nothing was (or needed to be) recovered


def test_replay_to_emits_only_closed_bars_and_advances_the_cursor():
    feed = LiveFeed(MARKET, resolution_s=HOUR_S, gap_source=_lake(0, 9))
    feed.replay_to(bar(1))  # anchor: cursor = bar 0

    bars = feed.replay_to(bar(4) + 60_000)  # now is inside bar 4 (still open)
    assert [b.candle.ts for b in bars] == [bar(1), bar(2), bar(3)]
    assert all(b.origin == "gap-replay" for b in bars)
    assert feed.cursor == bar(3)
    # The lake candle timestamps drove the session clock (honest venue time).
    assert feed.clock.now == bar(3)


def test_replay_to_is_a_noop_until_a_new_bar_closes():
    feed = LiveFeed(MARKET, resolution_s=HOUR_S, gap_source=_lake(0, 9))
    feed.replay_to(bar(2))
    assert feed.replay_to(bar(2) + 60_000) == []  # bar 2 is still open
    assert feed.replay_to(bar(2) + HOUR_MS - 1) == []
    assert feed.cursor == bar(1)


def test_replay_to_lake_shortfall_is_degraded_and_retried_not_skipped():
    # The lake only holds bars 1 and 2; bars 3,4 have not landed yet.
    feed = LiveFeed(MARKET, resolution_s=HOUR_S, gap_source=_lake(1, 2))
    feed.replay_to(bar(1))  # anchor at bar 0

    bars = feed.replay_to(bar(5))  # bars 1..4 have closed
    assert [b.candle.ts for b in bars] == [bar(1), bar(2)]
    rec = feed.recoveries[-1]
    assert rec.bars_expected == 4 and rec.bars_recovered == 2
    assert rec.degraded_fidelity
    # The cursor stopped at the last recovered bar, so the missing bars are
    # retried — never skipped — once the lake catches up.
    assert feed.cursor == bar(2)
    late = _lake(1, 4)
    feed._gap_source = late
    assert [b.candle.ts for b in feed.replay_to(bar(5))] == [bar(3), bar(4)]


def test_replay_to_without_a_gap_source_is_an_error():
    feed = LiveFeed(MARKET, resolution_s=HOUR_S)
    feed.replay_to(bar(1))  # anchoring needs no lake
    with pytest.raises(ValueError, match="gap_source"):
        feed.replay_to(bar(3))


# --- PaperSession.catch_up ---------------------------------------------------


def test_catch_up_advances_the_session_and_fills_through_the_engine():
    store = InMemoryUserData()
    sess = _session(store, "head-1", gap_source=_lake(0, 9))

    first = sess.catch_up(bar(1) + 60_000)  # anchor tick: nothing processed
    assert first.processed == 0 and not _fills(store, "head-1")

    # Bars 1..4 close before the next poll; the long decided on bar 1 fills at
    # bar 2's open — T+1 inside one catch-up batch, same engine loop as live.
    result = sess.catch_up(bar(5) + 60_000)
    assert result.processed == 4
    assert result.final_state.position(VENUE, MARKET) is not None
    assert len(_fills(store, "head-1")) == 1

    # Later ticks re-fold the carried book: position-aware, no re-entry.
    again = sess.catch_up(bar(7) + 60_000)
    assert again.processed == 2
    assert len(_fills(store, "head-1")) == 1


def test_catch_up_persists_the_cursor_in_the_run_head():
    store = InMemoryUserData()
    sess = _session(store, "head-2", gap_source=_lake(0, 9))
    sess.catch_up(bar(3) + 60_000)  # anchor only — no bar processed

    rec = store.load_run(TenantContext.local(), "head-2")
    assert rec.summary["cursor_bar_start"] == bar(2)


def test_restart_resumes_from_the_headless_anchor_without_reprocessing():
    store = InMemoryUserData()
    s1 = _session(store, "head-3", gap_source=_lake(0, 9))
    s1.catch_up(bar(3) + 60_000)  # anchor at bar 2; kill before any bar lands

    # A restarted session must NOT replay pre-anchor history: only bars 3,4
    # (closed since the anchor) are processed after the restart.
    s2 = _session(store, "head-3", gap_source=_lake(0, 9), resume=True)
    result = s2.catch_up(bar(5) + 60_000)
    assert result.processed == 2
    assert [b.candle.ts for b in result.bars] == [bar(3), bar(4)]


def test_restart_mid_run_continues_without_double_fill():
    store = InMemoryUserData()
    s1 = _session(store, "head-4", gap_source=_lake(0, 9))
    s1.catch_up(bar(1))
    s1.catch_up(bar(4))  # bars 1..3: the long fills once at bar 2's open
    assert len(_fills(store, "head-4")) == 1

    s2 = _session(store, "head-4", gap_source=_lake(0, 9), resume=True)
    result = s2.catch_up(bar(8))  # bars 4..7 — position carried, no re-entry
    assert result.processed == 4
    assert len(_fills(store, "head-4")) == 1
    assert result.final_state.position(VENUE, MARKET) is not None


def test_restart_survives_the_process_via_the_durable_duckdb_store(tmp_path):
    """The embedder restart story: runs/events live in a file, not the process.

    ``flint.data.store.DuckDBUserData(path)`` is the durable ``UserDataPort`` —
    a fresh adapter instance over the same file resumes the session exactly
    where the killed process left it (cursor + folded book), no double-fill.
    """
    from flint.data.store import DuckDBUserData

    path = str(tmp_path / "user.duckdb")
    s1 = _session(DuckDBUserData(path), "dur-1", gap_source=_lake(0, 9))
    s1.catch_up(bar(1))
    s1.catch_up(bar(4))  # bars 1..3: one fill at bar 2's open
    del s1  # "process death" — nothing survives but the file

    store2 = DuckDBUserData(path)
    s2 = _session(store2, "dur-1", gap_source=_lake(0, 9), resume=True)
    result = s2.catch_up(bar(8))  # bars 4..7
    assert result.processed == 4
    assert len(_fills(store2, "dur-1")) == 1
    assert result.final_state.position(VENUE, MARKET) is not None


def test_catch_up_settles_funding_on_replayed_bars():
    key = (VENUE, MARKET)
    lake = _lake(0, 6)
    lake.funding[key] = [
        FundingRate(
            ts=bar(3) + 1_000,
            rate_hourly=0.0003,
            interval_s=HOUR_S,
            price_basis="oracle",
            rate_type="final",
            venue=VENUE,
            market=MARKET,
        )
    ]
    store = InMemoryUserData()
    sess = _session(store, "head-5", gap_source=lake)
    sess.catch_up(bar(1))

    result = sess.catch_up(bar(5))
    assert result.final_state.position(VENUE, MARKET) is not None
    assert result.final_state.account(VENUE).funding_paid != 0
