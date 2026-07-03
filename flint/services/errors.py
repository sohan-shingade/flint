"""The uniform error/outcome taxonomy shared by every surface (§19.1, §13.3).

One machine-readable shape everywhere: ``{"error": {code, message, detail, hint}}``.
The rule of §19.1 is that *only* genuine faults and bugs are errors — **expected
scarcity is data**. So a funding hard-gate rejection or a coverage clip is a
:class:`Rejection`/degrade *payload*, never a raised error and never a stack
trace; the surfaces render those as structured ``rejected``/``degraded`` bodies.

``ServiceError`` (and its subclasses) is the *fault/bug/user-error* half of the
taxonomy; the API layer maps each subclass to its HTTP status and emits its
:meth:`~ServiceError.to_payload` body verbatim.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class ServiceError(Exception):
    """A structured failure with the uniform error schema (§13.3).

    Subclasses fix the ``code`` (and the surface maps it to a status). The
    payload shape ``{"error": {code, message, detail, hint}}`` is identical for
    the REST API and the agent surface, so an agent never has to parse prose.
    """

    code = "internal"

    def __init__(self, message: str, *, detail: str = "", hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.hint = hint

    def to_payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "detail": self.detail,
                "hint": self.hint,
            }
        }


class ValidationError(ServiceError):
    """User error — bad param, unknown template, malformed request (§19.1 row 1)."""

    code = "validation"


class NotFoundError(ServiceError):
    """A run/resource the tenant cannot see (absent, or owned by another tenant)."""

    code = "not_found"


class AuthError(ServiceError):
    """Missing/invalid session bearer token (local API security, §12)."""

    code = "unauthorized"


class OriginError(ServiceError):
    """A state-changing/WS request from a disallowed browser Origin (§12).

    The guard against a DNS-rebinding page in the user's browser POSTing strategy
    code to this code-executing server.
    """

    code = "forbidden_origin"


class VenueUnavailableError(ServiceError):
    """Venue/network fault — WS drop, REST 5xx, rate limit (§19.1 row 3)."""

    code = "venue_unavailable"

    def __init__(
        self, message: str, *, detail: str = "", hint: str = "", retry_after: int | None = None
    ) -> None:
        super().__init__(message, detail=detail, hint=hint)
        self.retry_after = retry_after

    def to_payload(self) -> dict[str, Any]:
        payload = super().to_payload()
        if self.retry_after is not None:
            payload["error"]["retry_after"] = self.retry_after
        return payload


@dataclass(frozen=True)
class Rejection:
    """An *expected* data-scarcity outcome — data, not an error (§19.1 row 2).

    A funding hard-gate gap is the canonical case: the run is *rejected* with the
    ranges actually available and the fix, and this rides in the result body as a
    structured payload (never a 4xx/5xx). ``missing`` lists the ``(venue, market)``
    legs whose funding is missing; ``available`` gives each leg's covered bounds.

    ``extras`` carries any code-specific structured fields (merged flat into the
    ``rejected`` body): a ``granularity_unavailable`` rejection uses it for the
    per-leg per-kind ``coverage`` and the machine-readable ``options`` out (§B7),
    which the funding-gap case has no use for.
    """

    code: str  # e.g. "funding_gap"
    message: str
    missing: tuple[str, ...] = ()
    available: Mapping[str, Any] = field(default_factory=dict)
    hint: str = ""
    extras: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "rejected": {
                "code": self.code,
                "message": self.message,
                "missing": list(self.missing),
                "available": dict(self.available),
                "hint": self.hint,
                **dict(self.extras),
            }
        }
