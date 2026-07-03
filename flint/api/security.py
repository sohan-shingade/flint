"""Local API security — this server executes strategy code (§12).

Two guards, because a code-executing endpoint on ``localhost`` is a real target:

* **Per-session bearer token** — generated at ``flint serve`` start and required
  on every request (header ``Authorization: Bearer <token>`` or, for the WS which
  cannot set headers from a browser, a ``?token=`` query param). Compared with a
  constant-time check so a wrong token leaks no timing signal.
* **Origin check** on state-changing routes and the WS — a browser fetch/WS
  carries an ``Origin``; if it is not a localhost origin (or an explicitly
  allowed one) the request is refused. This blocks a DNS-rebinding page in the
  user's browser from reaching the server. A request with *no* ``Origin`` is not
  a browser fetch (curl, the SDK, an agent) and is allowed.

Binding beyond localhost is a deliberate, loud choice made at ``serve`` time (an
explicit ``--host`` + a printed warning); this module only enforces the token +
Origin contract regardless of bind address.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from starlette.requests import HTTPConnection

from flint.services.errors import AuthError, OriginError

_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


def check_token(conn: HTTPConnection, expected: str) -> None:
    """Raise :class:`AuthError` unless the connection carries the session token."""
    auth = conn.headers.get("authorization", "")
    presented = ""
    if auth.startswith("Bearer "):
        presented = auth[len("Bearer ") :]
    elif "token" in conn.query_params:
        presented = conn.query_params["token"]
    if not presented or not secrets.compare_digest(presented, expected):
        raise AuthError(
            "missing or invalid session token",
            detail="pass Authorization: Bearer <token> (or ?token= for the WS)",
            hint="the token is printed/injected at `flint serve` start",
        )


def check_origin(conn: HTTPConnection, allowed: frozenset[str]) -> None:
    """Raise :class:`OriginError` for a disallowed browser Origin (else allow).

    No ``Origin`` header → not a browser request → allowed. A localhost Origin is
    always allowed; any other Origin must be in ``allowed`` verbatim.
    """
    origin = conn.headers.get("origin")
    if origin is None:
        return
    if origin in allowed:
        return
    host = urlsplit(origin).hostname or ""
    if host in _LOCALHOST_HOSTS:
        return
    raise OriginError(
        f"origin {origin!r} is not allowed",
        detail="state-changing and WS requests must come from a localhost origin",
    )


def generate_token() -> str:
    """A fresh per-session bearer token (URL-safe, ~256 bits)."""
    return secrets.token_urlsafe(32)
