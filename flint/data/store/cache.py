"""Content-addressed cache keys + freshness policy for the local cache (§9.0).

Cache keys are **content-addressed** — ``hash(source, range, schema_version,
lake_revision)`` — so a lake-side correction (exchanges *do* retroactively revise
funding) produces a *new* key instead of silently colliding with the stale copy.
A re-run after a correction therefore records a different ``lake_revision`` and
*says* the data changed, rather than quietly returning different numbers.

Freshness splits by kind: closed candles and recorded depth are immutable and
never expire; revisable kinds (funding, OI) carry a 24 h TTL so a stale local
copy is re-fetched after the venue may have revised it.
"""

from __future__ import annotations

import hashlib

# Kinds a venue may retroactively revise (§9.0) — these carry a freshness TTL.
# Everything else (closed candles, recorded depth) is immutable once written.
REVISABLE_KINDS = frozenset({"funding", "oi"})

# Default freshness window for revisable kinds: 24 hours in milliseconds.
FRESHNESS_TTL_MS = 24 * 60 * 60 * 1000


def cache_key(
    source: str,
    start_ts: int,
    end_ts: int,
    schema_version: int,
    lake_revision: str,
) -> str:
    """Return the content-addressed cache key for a fetched range.

    Any change to the source, the ``[start_ts, end_ts)`` range, the schema
    version, or the lake revision yields a different key — so a corrected lake
    revision never collides with the copy it supersedes.
    """
    digest = hashlib.sha256()
    for part in (source, start_ts, end_ts, schema_version, lake_revision):
        # NUL-delimit each field so ("ab", "c") and ("a", "bc") can't collide.
        digest.update(str(part).encode())
        digest.update(b"\x00")
    return digest.hexdigest()


def is_immutable(kind: str) -> bool:
    """True if ``kind`` never changes once written (closed candles, depth)."""
    return kind not in REVISABLE_KINDS


def is_fresh(
    kind: str,
    written_ts_ms: int,
    now_ts_ms: int,
    *,
    ttl_ms: int = FRESHNESS_TTL_MS,
) -> bool:
    """Whether a cached ``kind`` entry written at ``written_ts_ms`` is still fresh.

    Immutable kinds are always fresh; revisable kinds expire after ``ttl_ms``.
    """
    if is_immutable(kind):
        return True
    return (now_ts_ms - written_ts_ms) < ttl_ms
