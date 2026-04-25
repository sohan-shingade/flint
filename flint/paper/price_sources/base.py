"""Price-source ABC + small dataclasses.

Each source implements one method: `fetch(markets)`. The ticker
walks an ordered list of sources per tick; the first one that
returns a quote for a given market wins. Sources track their own
health stats so the system endpoint can surface "DLOB down, fell
back to HL" in one glance.
"""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass
from typing import Dict, Iterable, Optional


@dataclass(frozen=True)
class PriceQuote:
    """One price observation. `ts` is the source-side time when
    available, otherwise the wall-clock at fetch."""
    market: str
    price: float
    ts: int
    source: str
    stale: bool = False  # True for cache-fallback or known-old data


@dataclass
class SourceHealth:
    """Per-source running stats. Surfaced via
    /api/v1/system/price-sources for ConnectionBanner-style UX."""
    name: str
    last_success_ts: Optional[float] = None
    last_error: Optional[str] = None
    success_count: int = 0
    error_count: int = 0

    def record_success(self) -> None:
        self.last_success_ts = time.time()
        self.last_error = None
        self.success_count += 1

    def record_error(self, err: str) -> None:
        self.last_error = err
        self.error_count += 1

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "last_success_ts": self.last_success_ts,
            "last_error": self.last_error,
            "success_count": self.success_count,
            "error_count": self.error_count,
        }


class PriceSource(abc.ABC):
    """One backing source of mid prices.

    `fetch(markets)` returns whatever subset of `markets` the source
    can serve. Quotes for unsupported markets simply omit. Sources
    are encouraged to batch — `HyperliquidInfoSource` covers every
    listed perp in one HTTP call.

    Failures don't raise; they record on `health` and return an empty
    dict (or a partial dict). The ticker treats "no quote" as
    "fall through to the next source".
    """

    name: str = "base"

    def __init__(self) -> None:
        self.health = SourceHealth(name=self.name)

    @abc.abstractmethod
    def fetch(self, markets: Iterable[str]) -> Dict[str, PriceQuote]: ...

    def supports(self, market: str) -> bool:
        """Optional pre-filter — sources can hint the chain about
        markets they can't serve. Default: assume yes; the source
        will simply omit unsupported markets at fetch time."""
        return True
