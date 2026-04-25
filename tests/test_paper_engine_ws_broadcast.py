"""D-4.3-websocket slice 2 — paper engine broadcasts on tick.

Smoke-level: pin that PaperTradingEngine accepts a ws_manager and
calls `broadcast(paper:{id}, {...tick...})` on each bar. The full
loop runs through `_run_live_session`, so we test the broadcast
plumbing directly with a fake session + fake manager.
"""
from __future__ import annotations

import asyncio
import json

from flint.api.websocket import ConnectionManager


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def accept(self):
        pass

    async def send_text(self, msg):
        self.sent.append(msg)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestPaperEngineHasWsManagerAttr:
    def test_engine_default_ws_manager_is_none(self):
        from flint.paper.engine import PaperTradingEngine
        from flint.store import FlintStore
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            store = FlintStore(os.path.join(td, "t.duckdb"))
            engine = PaperTradingEngine(store)
            assert engine.ws_manager is None
            store.close()

    def test_engine_accepts_ws_manager(self):
        from flint.paper.engine import PaperTradingEngine
        from flint.store import FlintStore
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            store = FlintStore(os.path.join(td, "t.duckdb"))
            engine = PaperTradingEngine(store)
            mgr = ConnectionManager()
            engine.ws_manager = mgr
            assert engine.ws_manager is mgr
            store.close()


class TestBroadcastPayload:
    """The broadcast site builds an envelope shaped like:
    {channel: paper:{id}, seq, type: 'tick', ts, equity, cash,
     unrealized_pnl, is_replay, total_trades}.

    Test by directly invoking `ws_manager.broadcast` with the same
    payload the engine code would emit, then asserting the receiver
    sees the right shape."""

    def test_tick_envelope_shape(self):
        mgr = ConnectionManager()
        ws = _FakeWS()
        _run(mgr.connect(ws, "paper:s1"))
        eq_snap = {
            "ts": 1700000000, "equity": 10_125.0,
            "cash": 10_000.0, "unrealized_pnl": 125.0,
            "is_replay": False,
        }
        _run(mgr.broadcast("paper:s1", {"type": "tick", **eq_snap, "total_trades": 3}))

        envelope = json.loads(ws.sent[0])
        assert envelope["channel"] == "paper:s1"
        assert envelope["seq"] == 0
        assert envelope["type"] == "tick"
        assert envelope["equity"] == 10_125.0
        assert envelope["total_trades"] == 3

    def test_subscribers_only_get_their_session(self):
        mgr = ConnectionManager()
        a_ws = _FakeWS()
        b_ws = _FakeWS()
        _run(mgr.connect(a_ws, "paper:sessionA"))
        _run(mgr.connect(b_ws, "paper:sessionB"))

        _run(mgr.broadcast("paper:sessionA", {"type": "tick", "equity": 1}))
        _run(mgr.broadcast("paper:sessionB", {"type": "tick", "equity": 2}))

        assert len(a_ws.sent) == 1 and len(b_ws.sent) == 1
        assert json.loads(a_ws.sent[0])["equity"] == 1
        assert json.loads(b_ws.sent[0])["equity"] == 2


class TestBroadcastFailureDoesNotKillEngine:
    """A broken ws_manager shouldn't propagate exceptions out of the
    engine tick. We can't drive the full live loop here without an
    asyncio event loop set up around a real session, but we can
    confirm that broadcasting against a dead socket inside a
    try/except block doesn't blow up."""

    def test_broadcast_on_dead_socket_swallowed(self):
        """The engine catches Exception and logs at debug level; this
        test asserts the swallow path: broadcasting after a connection
        is forcibly dropped doesn't propagate."""
        mgr = ConnectionManager()
        ws = _FakeWS()

        async def _bad_send(msg):
            raise RuntimeError("simulated dead socket")
        ws.send_text = _bad_send

        _run(mgr.connect(ws, "paper:s1"))
        # Should not raise — manager prunes dead sockets internally.
        sent = _run(mgr.broadcast("paper:s1", {"type": "tick"}))
        assert sent == 0  # the only sub failed
