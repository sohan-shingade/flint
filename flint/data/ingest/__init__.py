"""data.ingest — the five lake-ingestion worker classes (§9.1).

Shared building blocks live here: the injectable network seams (``transport``)
and the pre-write quality bars (``quality``). Per-venue workers live in the
sub-packages (``backfillers`` for REST/CSV/S3, ``recorders`` for now-or-never WS,
``vendors`` for the BYO-license lane, ``onchain`` for Solana/EVM decoders).
"""

from .quality import (
    BackfillResult,
    QualityReport,
    check_prewrite,
    detect_candle_gaps,
    expected_bar_count,
)
from .transport import HttpTransport, HttpsObjectStore, HttpxTransport, ObjectStore

__all__ = [
    "BackfillResult",
    "QualityReport",
    "check_prewrite",
    "detect_candle_gaps",
    "expected_bar_count",
    "HttpTransport",
    "HttpsObjectStore",
    "HttpxTransport",
    "ObjectStore",
]
