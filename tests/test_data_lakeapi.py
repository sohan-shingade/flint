"""Flint Data API service + local client (2.7, §9.0).

The service is driven by an in-memory ``LakeBackend`` (D26 — no live network, no
fabricated market data; fixtures are hand-authored unit inputs). The client is
exercised end-to-end against the app through a ``TestClient`` seam, then wired
into the real ``DataManager`` chain to prove write-through to the cache tier.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
from fastapi.testclient import TestClient

from flint.data import (
    DataManager,
    InMemoryCacheSource,
    Kind,
    LakeAPIClient,
    TimeRange,
)
from flint.data.lakeapi import (
    BLOCKED,
    CoverageRow,
    KeyQuota,
    TokenAuthority,
    create_lake_api,
)
from flint.data.wire import from_ipc

FUNDING = pa.schema(
    [("ts", pa.int64()), ("rate_hourly", pa.float64()), ("venue", pa.string())]
)


def _funding(ts_values: list[int]) -> pa.Table:
    return pa.table(
        {
            "ts": ts_values,
            "rate_hourly": [0.0001] * len(ts_values),
            "venue": ["hyperliquid"] * len(ts_values),
        },
        schema=FUNDING,
    )


class FakeBackend:
    """In-memory lake: (kind, venue, market) -> table, plus a coverage matrix."""

    def __init__(self, tables=None, coverage=None) -> None:
        self._tables = tables or {}
        self._coverage = coverage or []

    def read(self, kind: Kind, venue: str, market: str, span: TimeRange) -> pa.Table:
        table = self._tables.get((kind, venue, market))
        if table is None:
            return pa.table({})
        import pyarrow.compute as pc

        ts = table.column("ts")
        mask = pc.and_(pc.greater_equal(ts, span.start_ms), pc.less(ts, span.end_ms))
        return table.filter(mask)

    def coverage(self):
        return list(self._coverage)


class AppHttp:
    """A ``LakeHttp`` that routes the client's GETs into a starlette ``TestClient``."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def get_arrow(self, url, *, params, headers) -> bytes | None:
        resp = self._client.get(url, params=dict(params), headers=dict(headers))
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.content

    def get_json(self, url, *, params=None, headers=None) -> Any:
        resp = self._client.get(
            url, params=dict(params or {}), headers=dict(headers or {})
        )
        resp.raise_for_status()
        return resp.json()


def _auth(token="tok") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- service: auth + range endpoint -----------------------------------------


def test_range_endpoint_streams_arrow_ipc_for_the_requested_span():
    backend = FakeBackend({(Kind.FUNDING, "hyperliquid", "SOL-PERP"): _funding([0, 10, 20])})
    app = create_lake_api(backend, TokenAuthority({"tok"}))
    client = TestClient(app)

    resp = client.get(
        "/v1/data/funding",
        params={"venue": "hyperliquid", "market": "SOL-PERP", "from": 0, "to": 20},
        headers=_auth(),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/vnd.apache.arrow.stream"
    table = from_ipc(resp.content)
    assert table.column("ts").to_pylist() == [0, 10]  # half-open [0, 20)


def test_range_endpoint_requires_a_bearer_token():
    app = create_lake_api(FakeBackend(), TokenAuthority({"tok"}))
    client = TestClient(app)
    resp = client.get(
        "/v1/data/funding",
        params={"venue": "hyperliquid", "market": "SOL-PERP", "from": 0, "to": 10},
    )
    assert resp.status_code == 401
    bad = client.get(
        "/v1/data/funding",
        params={"venue": "hyperliquid", "market": "SOL-PERP", "from": 0, "to": 10},
        headers=_auth("nope"),
    )
    assert bad.status_code == 401


def test_range_endpoint_rejects_unknown_kind_and_bad_span():
    app = create_lake_api(FakeBackend(), TokenAuthority({"tok"}))
    client = TestClient(app)
    assert (
        client.get(
            "/v1/data/bogus",
            params={"venue": "hl", "market": "SOL-PERP", "from": 0, "to": 10},
            headers=_auth(),
        ).status_code
        == 400
    )
    assert (
        client.get(
            "/v1/data/funding",
            params={"venue": "hl", "market": "SOL-PERP", "from": 10, "to": 10},
            headers=_auth(),
        ).status_code
        == 400
    )


# --- service: quotas --------------------------------------------------------


def test_concurrency_quota_returns_429_when_at_cap():
    authority = TokenAuthority({"tok": KeyQuota(max_concurrent=1)})
    app = create_lake_api(
        FakeBackend({(Kind.FUNDING, "hl", "SOL-PERP"): _funding([0])}), authority
    )
    client = TestClient(app)
    # Hold the one slot, then a request must be turned away.
    with authority.slot("tok"):
        resp = client.get(
            "/v1/data/funding",
            params={"venue": "hl", "market": "SOL-PERP", "from": 0, "to": 10},
            headers=_auth(),
        )
    assert resp.status_code == 429


def test_daily_egress_quota_returns_429_when_exceeded():
    authority = TokenAuthority({"tok": KeyQuota(daily_egress_bytes=10)})
    app = create_lake_api(
        FakeBackend({(Kind.FUNDING, "hl", "SOL-PERP"): _funding([0, 10, 20])}),
        authority,
    )
    client = TestClient(app)
    resp = client.get(
        "/v1/data/funding",
        params={"venue": "hl", "market": "SOL-PERP", "from": 0, "to": 30},
        headers=_auth(),
    )
    assert resp.status_code == 429


# --- service: public coverage matrix ----------------------------------------


def test_coverage_endpoint_is_public_and_honest():
    rows = [
        CoverageRow("hyperliquid", "SOL-PERP", "funding", 0, 100, gaps=((40, 50),)),
        CoverageRow("binance", "SOL-PERP", "funding", 0, 100, redistribution=BLOCKED),
    ]
    app = create_lake_api(FakeBackend(coverage=rows), TokenAuthority({"tok"}))
    client = TestClient(app)
    resp = client.get("/v1/data/coverage")  # no auth
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["gaps"] == [[40, 50]]
    assert body[1]["redistribution"] == BLOCKED


# --- client: available reads coverage; fetch decodes IPC --------------------


def _wired(tables=None, coverage=None, token="tok", quota=None):
    authority = TokenAuthority({token: quota} if quota else {token})
    app = create_lake_api(FakeBackend(tables, coverage), authority)
    http = AppHttp(TestClient(app))
    return LakeAPIClient(base_url="", token=token, http=http)


def test_client_available_reflects_coverage_minus_gaps():
    cov = [CoverageRow("hyperliquid", "SOL-PERP", "funding", 0, 100, gaps=((40, 50),))]
    client = _wired(coverage=cov)
    avail = client.available("hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(0, 100))
    assert avail.ranges == (TimeRange(0, 40), TimeRange(50, 100))
    # An uncovered market is honestly empty.
    assert client.available("hyperliquid", "BTC-PERP", Kind.FUNDING, TimeRange(0, 100)).is_empty


def test_client_available_serves_nothing_for_blocked_rows():
    cov = [CoverageRow("binance", "SOL-PERP", "funding", 0, 100, redistribution=BLOCKED)]
    client = _wired(coverage=cov)
    assert client.available("binance", "SOL-PERP", Kind.FUNDING, TimeRange(0, 100)).is_empty


def test_client_fetch_decodes_the_arrow_stream():
    tables = {(Kind.FUNDING, "hyperliquid", "SOL-PERP"): _funding([0, 10, 20])}
    client = _wired(tables=tables)
    table = client.fetch("hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(0, 20))
    assert table.column("ts").to_pylist() == [0, 10]


# --- integration: the client is a real chain tier, writes through to cache --


def test_client_in_the_chain_writes_through_to_the_cache():
    tables = {(Kind.FUNDING, "hyperliquid", "SOL-PERP"): _funding([0, 25, 50, 75])}
    cov = [CoverageRow("hyperliquid", "SOL-PERP", "funding", 0, 100)]
    lake = _wired(tables=tables, coverage=cov)
    cache = InMemoryCacheSource()
    dm = DataManager(sources=[cache, lake])

    prepared = dm.prepare(
        ["SOL-PERP"], ["hyperliquid"], [Kind.FUNDING], TimeRange(0, 100)
    )
    # Funding hard gate passes because the lake covers [0, 100).
    assert prepared.fidelity.all_full
    # The manager wrote the lake's rows through to the local cache tier.
    assert cache.available(
        "hyperliquid", "SOL-PERP", Kind.FUNDING, TimeRange(0, 100)
    ).ranges == (TimeRange(0, 76),)
