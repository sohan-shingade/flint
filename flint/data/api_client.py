"""The local client for the hosted Flint Data API (§9.0, D16).

``LakeAPIClient`` is the real second tier of the source chain — it replaces the
2.2 ``FlintDataAPIClient`` stub. It speaks the v1 serving contract:

* ``available`` reads the **public coverage matrix** (``GET /v1/data/coverage``),
  memoized with a short TTL, and answers honestly — the lake's covered range for
  a ``(venue, market, kind)`` minus its published gaps, intersected with what the
  caller wants. A ``blocked`` (D23) row serves nothing.
* ``fetch`` GETs the **Arrow IPC** range endpoint with the bearer token and
  decodes the stream into a Table. The ``DataManager`` writes the result through
  to the cache tier, so the next run for that range never leaves the machine.

The network lives behind the injectable ``LakeHttp`` seam (bytes + JSON), lazily
importing ``httpx`` only in the production adapter — so importing this module and
running the mocked suite never needs a socket (D26). Because the client is just
another ``DataSource`` behind the same chain, the engine cannot tell the hosted
lake from the local cache (§17) — "your compute stays local; only the shared
market-data firehose is hosted."
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import pyarrow as pa

from .ranges import Kind, RangeSet, TimeRange
from .sources import DataSource
from .wire import from_ipc

# Rows the lake is not allowed to redistribute serve nothing to the client (D23).
_BLOCKED = "blocked"

# How long a fetched coverage matrix is trusted before re-fetching (5 min).
_COVERAGE_TTL_MS = 5 * 60 * 1000


class LakeHttp(Protocol):
    """The client's network seam: an Arrow-bytes GET and a JSON GET."""

    def get_arrow(
        self,
        url: str,
        *,
        params: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> bytes | None: ...

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any: ...


class HttpxLakeHttp:
    """Production ``LakeHttp`` — thin ``httpx`` GETs, imported lazily."""

    def __init__(self, *, timeout_s: float = 60.0) -> None:
        self._timeout_s = timeout_s

    def get_arrow(self, url, *, params, headers) -> bytes | None:
        import httpx

        resp = httpx.get(
            url, params=dict(params), headers=dict(headers), timeout=self._timeout_s
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.content

    def get_json(self, url, *, params=None, headers=None) -> Any:
        import httpx

        resp = httpx.get(
            url,
            params=dict(params) if params else None,
            headers=dict(headers) if headers else None,
            timeout=self._timeout_s,
        )
        resp.raise_for_status()
        return resp.json()


def _coverage_ranges(row: Mapping[str, Any]) -> RangeSet:
    """Turn one coverage-matrix row into a servable ``RangeSet`` (span minus gaps)."""
    if row.get("redistribution") == _BLOCKED:
        return RangeSet()
    span = RangeSet((TimeRange(int(row["start_ms"]), int(row["end_ms"])),))
    gaps = RangeSet(
        TimeRange(int(g[0]), int(g[1])) for g in row.get("gaps", ())
    )
    return span.subtract(gaps)


class LakeAPIClient(DataSource):
    """Hosted-lake tier of the source chain — the real replacement for the stub."""

    name = "flint_data_api"

    def __init__(
        self,
        base_url: str,
        token: str,
        http: LakeHttp,
        *,
        coverage_ttl_ms: int = _COVERAGE_TTL_MS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._http = http
        self._ttl_ms = coverage_ttl_ms
        self._clock = clock
        self._coverage: dict[tuple[str, str, Kind], RangeSet] | None = None
        self._coverage_at_ms = 0

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _coverage_map(self) -> dict[tuple[str, str, Kind], RangeSet]:
        """Fetch + memoize the public coverage matrix (re-fetched past the TTL)."""
        fresh = (
            self._coverage is not None
            and self._now_ms() - self._coverage_at_ms < self._ttl_ms
        )
        if fresh:
            return self._coverage  # type: ignore[return-value]
        rows = self._http.get_json(f"{self._base}/v1/data/coverage") or []
        built: dict[tuple[str, str, Kind], RangeSet] = {}
        for row in rows:
            try:
                kind = Kind(row["kind"])
            except (KeyError, ValueError):
                continue
            key = (row["venue"], row["market"], kind)
            built[key] = built.get(key, RangeSet()).union(_coverage_ranges(row))
        self._coverage = built
        self._coverage_at_ms = self._now_ms()
        return built

    def available(
        self, venue: str, market: str, kind: Kind, want: TimeRange
    ) -> RangeSet:
        covered = self._coverage_map().get((venue, market, kind))
        if covered is None:
            return RangeSet()
        return covered.intersect(RangeSet((want,)))

    def fetch(
        self, venue: str, market: str, kind: Kind, span: TimeRange
    ) -> pa.Table:
        if span.is_empty:
            return pa.table({})
        raw = self._http.get_arrow(
            f"{self._base}/v1/data/{kind.value}",
            params={
                "venue": venue,
                "market": market,
                "from": span.start_ms,
                "to": span.end_ms,
            },
            headers=self._auth(),
        )
        if not raw:
            return pa.table({})
        return from_ipc(raw)
