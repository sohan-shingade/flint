"""engine.context — the read-only value objects a strategy sees (§8.2, §8.3).

The ``EngineContext`` itself lives with the loop that builds it; this package
holds the inert value objects it hands out (``AccountView``, ``OpenInterestSnapshot``).
"""

from __future__ import annotations

from .view import AccountView, OpenInterestSnapshot

__all__ = ["AccountView", "OpenInterestSnapshot"]
