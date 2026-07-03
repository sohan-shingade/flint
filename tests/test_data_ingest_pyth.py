"""Pyth Hermes oracle poller — key-gated, decodes expo-scaled prices (2.5, §9.1).

The HttpTransport + SecretsPort are faked; no network. Fixtures match Hermes v2
``/v2/updates/price/latest`` response shape (D26).
"""

from __future__ import annotations

from typing import Any

from flint.data import Kind
from flint.data.ingest.recorders import PythOraclePoller
from flint.ports import TenantContext

FEEDS = {"SOL-PERP": "ef0d8b6f", "BTC-PERP": "e62df6c8"}


class FakeSecrets:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def get_secret(self, tenant: TenantContext, name: str) -> str | None:
        return self._value


class FakeTransport:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.calls: list[tuple[str, Any, Any]] = []

    def post_json(self, url: str, body: Any) -> Any:  # pragma: no cover - unused
        raise AssertionError("Pyth uses GET")

    def get_json(self, url, *, params=None, headers=None) -> Any:
        self.calls.append((url, params, headers))
        return self._payload


def _feed(fid: str, price: str, conf: str, expo: int, publish_time: int) -> dict:
    return {"id": fid, "price": {"price": price, "conf": conf, "expo": expo,
                                 "publish_time": publish_time}}


PAYLOAD = {"parsed": [_feed("ef0d8b6f", "10045000000", "5000000", -8, 1_735_689_600)]}


def _poller(secret: str | None, payload: Any = PAYLOAD, *, clock_ms: int = 0):
    return PythOraclePoller(
        FakeTransport(payload), FakeSecrets(secret), TenantContext.local(),
        feed_ids=FEEDS, clock=lambda: clock_ms,
    )


def test_poller_is_inert_without_a_key():
    poller = _poller(None)
    assert poller.enabled is False
    assert poller.poll() == []


def test_poller_enabled_with_key_and_sends_bearer_header():
    transport = FakeTransport(PAYLOAD)
    poller = PythOraclePoller(
        transport, FakeSecrets("k123"), TenantContext.local(),
        feed_ids=FEEDS, clock=lambda: 1_735_689_601_000,
    )
    assert poller.enabled is True
    poller.poll()
    assert transport.calls[0][2] == {"Authorization": "Bearer k123"}


def test_poller_decodes_expo_scaled_price():
    prices = _poller("k", clock_ms=1_735_689_601_000).poll()
    assert len(prices) == 1
    p = prices[0]
    assert p.market == "SOL-PERP"
    assert p.venue == "pyth"
    assert p.price == 100.45  # 10045000000 * 1e-8
    assert p.confidence == 0.05  # 5000000 * 1e-8
    assert p.ts == 1_735_689_600_000  # publish_time seconds -> ms


def test_poller_ignores_unmapped_feed_ids():
    payload = {"parsed": [_feed("deadbeef", "1", "1", -8, 1)]}
    assert _poller("k", payload).poll() == []


def test_poller_tracks_lag():
    poller = _poller("k", clock_ms=1_735_689_601_000)
    poller.poll()
    assert poller.lag.lag_ms("pyth", "SOL-PERP", Kind.OI) == 1000


def test_poll_to_arrow_roundtrip():
    prices = _poller("k", clock_ms=1_735_689_601_000).poll()
    table = PythOraclePoller.to_arrow(prices)
    assert table.column("price").to_pylist() == [100.45]
    assert table.column("feed_id").to_pylist() == ["ef0d8b6f"]
