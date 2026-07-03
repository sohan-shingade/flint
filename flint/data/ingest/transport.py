"""Injectable network seams for the ingestion workers (§9.1).

Ingestion is the only part of the data layer that touches the outside world, so
the network is pushed behind two tiny interfaces the workers depend on instead of
calling ``httpx`` directly:

* ``HttpTransport`` — a JSON POST (Hyperliquid's ``/info`` endpoint speaks POST).
* ``ObjectStore`` — a keyed blob fetch (the HL S3 archive, served over public
  HTTPS — no credentials, no ``boto3``).

Tests inject fakes that return **recorded fixture fragments**, so no ingestion
test ever opens a socket (D26: fixtures are hand-authored unit inputs or real
recorded fragments, never generated market data). The real adapters here import
``httpx`` lazily, so importing this module — and running the mocked tests — never
requires the network stack to be reachable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any


class HttpTransport(ABC):
    """JSON over HTTP. POST (HL's info API) + GET (Pyth Hermes latest prices)."""

    @abstractmethod
    def post_json(self, url: str, body: Mapping[str, Any]) -> Any:
        """POST ``body`` as JSON to ``url``; return the decoded JSON response."""
        ...

    @abstractmethod
    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """GET ``url`` with query ``params`` and ``headers``; return decoded JSON."""
        ...


class ObjectStore(ABC):
    """A read-only keyed blob store — the HL S3 archive over public HTTPS."""

    @abstractmethod
    def get(self, key: str) -> bytes | None:
        """Return the raw bytes at ``key``, or None if the object is absent."""
        ...


class HttpxTransport(HttpTransport):
    """The production ``HttpTransport`` — a thin, retrying ``httpx`` POST.

    ``httpx`` is imported lazily on first use so the mocked test suite (which
    injects fakes) never needs it installed or the network reachable.
    """

    def __init__(self, *, timeout_s: float = 30.0) -> None:
        self._timeout_s = timeout_s

    def post_json(self, url: str, body: Mapping[str, Any]) -> Any:
        import httpx

        resp = httpx.post(url, json=dict(body), timeout=self._timeout_s)
        resp.raise_for_status()
        return resp.json()

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        import httpx

        resp = httpx.get(
            url,
            params=dict(params) if params else None,
            headers=dict(headers) if headers else None,
            timeout=self._timeout_s,
        )
        resp.raise_for_status()
        return resp.json()


class HttpsObjectStore(ObjectStore):
    """The production ``ObjectStore`` — GET ``{base_url}/{key}`` (public archive).

    The Hyperliquid archive is a public S3 bucket exposed over HTTPS, so a plain
    GET with no credentials is enough; a 404 becomes ``None`` (a missing hour is a
    gap the caller reconciles, not an error).
    """

    def __init__(self, base_url: str, *, timeout_s: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    def get(self, key: str) -> bytes | None:
        import httpx

        resp = httpx.get(f"{self._base_url}/{key.lstrip('/')}", timeout=self._timeout_s)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.content
