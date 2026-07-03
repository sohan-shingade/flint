"""The Flint Data API — FastAPI serving contract for the shared lake (§9.0).

The lake is "the product's onboarding promise" (§9.0), so this is a real spec,
not a placeholder. Two endpoints:

* ``GET /v1/data/{kind}?venue&market&from&to`` → an **Arrow IPC stream** of the
  rows in the half-open ``[from, to)`` range. Bearer-token auth, metered against
  per-key quotas (default 10 GB/day egress, 4 concurrent range requests).
* ``GET /v1/data/coverage`` → **public** JSON coverage matrix: per venue × kind ×
  date-range, each row carrying its **redistribution status** (``serve-raw`` /
  ``serve-derived`` / ``blocked`` per the D23 ToS review) and **honest gaps**
  (Jupiter pre-recorder ≈ zero is *displayed*, not hidden — D26).

The lake bytes themselves live behind an injectable ``LakeBackend`` Protocol, so
this module never opens a file or a socket on its own: tests drive it with an
in-memory backend (D26 — no live network, no fabricated data). Production wires a
Parquet/DuckDB backend reading ``store.layout`` partitions. Auth exists from the
first deploy on purpose — retrofitting it onto an open endpoint is a migration
(§9.0); shipping it in the header keeps that honest.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import pyarrow as pa
from fastapi import FastAPI, HTTPException, Query, Request, Response

from .ranges import Kind, TimeRange
from .wire import ARROW_STREAM_MEDIA_TYPE, to_ipc

# Redistribution status per source, from the D23 per-source ToS review (§9.0).
SERVE_RAW = "serve-raw"
SERVE_DERIVED = "serve-derived"
BLOCKED = "blocked"


@dataclass(frozen=True)
class CoverageRow:
    """One cell of the public coverage matrix (§9.0).

    ``gaps`` are honest holes inside ``[start_ms, end_ms)`` — displayed, never
    hidden (D26). ``redistribution`` records what the lake may legally serve.
    """

    venue: str
    market: str
    kind: str
    start_ms: int
    end_ms: int
    redistribution: str = SERVE_RAW
    gaps: tuple[tuple[int, int], ...] = ()


class LakeBackend(Protocol):
    """The lake's read surface — injected so the service never touches storage.

    ``read`` returns the rows in ``span`` for one ``(kind, venue, market)`` as an
    Arrow table (empty table if none). ``coverage`` returns the public matrix.
    """

    def read(
        self, kind: Kind, venue: str, market: str, span: TimeRange
    ) -> pa.Table: ...

    def coverage(self) -> list[CoverageRow]: ...


# --- auth + quota metering --------------------------------------------------


@dataclass(frozen=True)
class KeyQuota:
    """Per-key limits (§9.0 defaults)."""

    daily_egress_bytes: int = 10 * 1024**3  # 10 GB/day
    max_concurrent: int = 4


class Unauthorized(Exception):
    """Missing or unknown bearer token — maps to HTTP 401."""


class QuotaExceeded(Exception):
    """Concurrency slot or daily egress budget exhausted — maps to HTTP 429."""


class TokenAuthority:
    """Validates bearer tokens and meters per-key quotas (§9.0).

    Tokens map to a ``KeyQuota``; metering is in-process and thread-safe (the
    daily egress counter resets on the UTC calendar day, read from an injectable
    clock so tests can advance time). This is the from-first-deploy auth header:
    range requests require a token, only the public endpoints do not.
    """

    def __init__(
        self,
        tokens: Mapping[str, KeyQuota] | Iterable[str],
        *,
        default_quota: KeyQuota = KeyQuota(),
        clock: Callable[[], float] = time.time,
    ) -> None:
        if isinstance(tokens, Mapping):
            self._quota: dict[str, KeyQuota] = dict(tokens)
        else:
            self._quota = {t: default_quota for t in tokens}
        self._clock = clock
        self._lock = threading.Lock()
        self._egress: dict[str, tuple[str, int]] = {}  # token -> (utc_day, bytes)
        self._concurrent: dict[str, int] = {}

    def authorize(self, auth_header: str | None) -> str:
        """Return the validated token from an ``Authorization: Bearer`` header."""
        if not auth_header or not auth_header.lower().startswith("bearer "):
            raise Unauthorized("missing bearer token")
        token = auth_header[7:].strip()
        if token not in self._quota:
            raise Unauthorized("unknown token")
        return token

    def _utc_day(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self._clock()))

    @contextmanager
    def slot(self, token: str):
        """Hold one concurrency slot for the request; raise if the key is at cap."""
        quota = self._quota[token]
        with self._lock:
            current = self._concurrent.get(token, 0)
            if current >= quota.max_concurrent:
                raise QuotaExceeded(
                    f"max {quota.max_concurrent} concurrent range requests"
                )
            self._concurrent[token] = current + 1
        try:
            yield
        finally:
            with self._lock:
                self._concurrent[token] = self._concurrent.get(token, 1) - 1

    def charge(self, token: str, nbytes: int) -> None:
        """Charge ``nbytes`` egress to ``token``; raise if it exceeds the daily budget."""
        quota = self._quota[token]
        day = self._utc_day()
        with self._lock:
            stored_day, used = self._egress.get(token, (day, 0))
            if stored_day != day:
                used = 0  # new UTC day resets the counter
            if used + nbytes > quota.daily_egress_bytes:
                raise QuotaExceeded(
                    f"daily egress budget of {quota.daily_egress_bytes} bytes exceeded"
                )
            self._egress[token] = (day, used + nbytes)


# --- app factory ------------------------------------------------------------


def create_lake_api(backend: LakeBackend, authority: TokenAuthority) -> FastAPI:
    """Build the Flint Data API app over ``backend`` with ``authority`` metering."""
    app = FastAPI(title="Flint Data API", version="1")

    @app.get("/v1/data/coverage")
    def coverage() -> list[dict[str, Any]]:
        # Public: no auth. The matrix is the honest onboarding promise (§9.0).
        return [asdict(row) for row in backend.coverage()]

    @app.get("/v1/data/{kind}")
    def range_query(
        kind: str,
        request: Request,
        venue: str = Query(...),
        market: str = Query(...),
        from_ms: int = Query(..., alias="from"),
        to_ms: int = Query(..., alias="to"),
    ) -> Response:
        try:
            token = authority.authorize(request.headers.get("authorization"))
        except Unauthorized as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        try:
            resolved = Kind(kind)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"unknown kind: {kind!r}") from exc
        if to_ms <= from_ms:
            raise HTTPException(status_code=400, detail="to must be greater than from")

        span = TimeRange(from_ms, to_ms)
        try:
            with authority.slot(token):
                table = backend.read(resolved, venue, market, span)
                body = to_ipc(table)
                authority.charge(token, len(body))
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return Response(content=body, media_type=ARROW_STREAM_MEDIA_TYPE)

    return app
