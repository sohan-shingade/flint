"""D-4.3-websocket slice 2b — LiveExecutionContext fill broadcast.

Pins the fill→broadcast path on a LiveExecutionContext subclass.
Uses a minimal stub subclass since LiveExecutionContext is abstract.
"""
from __future__ import annotations

import asyncio
import json

from flint.api.websocket import ConnectionManager
from flint.execution.live_base import LiveExecutionContext
from flint.models import Fill, Side


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def accept(self):
        pass

    async def send_text(self, msg):
        self.sent.append(msg)


class _StubLiveCtx(LiveExecutionContext):
    """Bare-minimum concrete subclass — only implements what the
    fill-broadcast test actually exercises."""

    async def _connect(self): pass
    async def _disconnect(self): pass
    async def _place_order(self, order): return ("o", None)
    async def _cancel_order(self, vid): return True
    async def _fetch_positions(self): return []
    async def _fetch_balance(self): return 0.0
    async def _poll_order_status(self, vid): pass

    @property
    def account(self):
        from flint.models import AccountState
        return AccountState(equity=0, cash=0, unrealized_pnl=0,
                            margin_used=0, free_margin=0, leverage=0)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestFillBroadcast:
    def test_default_no_ws_manager(self):
        ctx = _StubLiveCtx(venue="drift", session_id="s1")
        assert ctx.ws_manager is None

    def test_fill_broadcasts_when_manager_set(self):
        async def _scenario():
            mgr = ConnectionManager()
            ws = _FakeWS()
            await mgr.connect(ws, "live:s1")

            ctx = _StubLiveCtx(venue="drift", session_id="s1")
            ctx.ws_manager = mgr

            fill = Fill(
                market="SOL-PERP", side=Side.LONG, price=150.0,
                size=1.0, fee=0.05, ts=1700000000,
                order_id="o1", venue="drift",
            )
            # _handle_fill schedules an asyncio.ensure_future broadcast;
            # yield control so the broadcast lands before assertion.
            ctx._handle_fill("o1", fill)
            await asyncio.sleep(0)

            assert len(ws.sent) >= 1
            envelope = json.loads(ws.sent[0])
            assert envelope["channel"] == "live:s1"
            assert envelope["type"] == "fill"
            assert envelope["market"] == "SOL-PERP"
            assert envelope["side"] == "long"
            assert envelope["price"] == 150.0

        _run(_scenario())

    def test_fill_broadcast_swallows_exceptions(self):
        """Manager that fails to send must not propagate out of _handle_fill.
        We force this by giving the manager a broken broadcast."""
        async def _scenario():
            class _BrokenManager:
                async def broadcast(self, channel, data):
                    raise RuntimeError("simulated")

            ctx = _StubLiveCtx(venue="drift", session_id="s1")
            ctx.ws_manager = _BrokenManager()
            fill = Fill(
                market="SOL-PERP", side=Side.LONG, price=150.0,
                size=1.0, fee=0.05, ts=1700000000,
                order_id="o1", venue="drift",
            )
            # Should not raise.
            ctx._handle_fill("o1", fill)
            await asyncio.sleep(0)

        _run(_scenario())

    def test_no_session_id_no_broadcast(self):
        """Empty session_id means we have no channel to publish to —
        skip the broadcast even if a manager is set."""
        async def _scenario():
            mgr = ConnectionManager()
            ws = _FakeWS()
            # Subscribe to whatever channel an empty session_id would map to
            await mgr.connect(ws, "live:")

            ctx = _StubLiveCtx(venue="drift", session_id="")
            # Manually clear in case ctor populated a default
            ctx._session_id = ""
            ctx.ws_manager = mgr

            fill = Fill(
                market="SOL-PERP", side=Side.LONG, price=150.0,
                size=1.0, fee=0.05, ts=1700000000,
                order_id="o1", venue="drift",
            )
            ctx._handle_fill("o1", fill)
            await asyncio.sleep(0)
            assert ws.sent == []

        _run(_scenario())
