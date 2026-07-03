"""CoverageLedger — ingester-asserted coverage per store directory (§9.0).

Row envelopes cannot distinguish a quiet market from a capture gap: an hour with
zero trades *is data* when the recorder was watching, and *is a hole* when it
was not. So coverage for tick-scale kinds is never inferred from rows — it is
**asserted** by whoever captured them (the Tardis backfiller marks whole vendor
days, the live recorder marks its gap-free capture spans, REST backfillers mark
fetched spans) and persisted here.

One ``_coverage.json`` lives in each ``(kind, venue, market)`` directory of the
Parquet lake layout. It holds a list of asserted ranges — the same half-open
``start_ms``/``end_ms`` integer idiom ``ranges.TimeRange`` uses — each carrying
its provenance (``source`` + ``written_ts``) and a ``variant`` key slot
(default ``""``; reserved for future candle-resolution variants — schema only,
no behavior today). ``covered()`` folds the entries into a normalised
:class:`~flint.data.ranges.RangeSet`.

Writes are atomic (temp file + ``os.replace`` in the same directory), so a
crash mid-write leaves the previous ledger intact — a ledger is either the old
truth or the new truth, never garbage. Eviction must call :meth:`remove` so a
pruned range stops being advertised — the ledger never lies (§B5/D5).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from ..ranges import RangeSet, TimeRange

LEDGER_FILENAME = "_coverage.json"
_LEDGER_SCHEMA_VERSION = 1

# Who may assert coverage. Anything else is a bug, not a new source: extending
# this set is a deliberate act with its own gap-semantics review (§B2).
KNOWN_SOURCES = frozenset({"tardis", "recorder", "hl_rest"})

# FUNDING coverage is asserted per rate_type series (§6.4): the default variant
# ``""`` is the **final/settled** series — the one that settles money and the
# only one the funding hard gate may accept — while predicted-rate captures
# (the live recorder's ctx fold, Tardis ``derivative_ticker``) assert under
# this variant so watching-the-predicted-rate never masquerades as settled
# history. Predicted coverage drives the fidelity flag, never the gate.
FUNDING_PREDICTED_VARIANT = "predicted"


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    """One asserted range with its provenance."""

    start_ms: int
    end_ms: int
    source: str
    written_ts: int  # unix-ms wall clock at assertion time (provenance, not market data)
    variant: str = ""

    @property
    def range(self) -> TimeRange:
        return TimeRange(self.start_ms, self.end_ms)


class CoverageLedger:
    """The asserted-coverage ledger of one ``(kind, venue, market)`` directory.

    Constructing one loads ``_coverage.json`` if it exists (``exists`` reports
    which case you got). Every mutation persists immediately and atomically.
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._path = self._dir / LEDGER_FILENAME
        self._entries: list[CoverageEntry] = []
        self._exists = self._path.exists()
        if self._exists:
            self._entries = self._parse(json.loads(self._path.read_text()))

    @classmethod
    def load(cls, directory: str | Path) -> CoverageLedger | None:
        """The ledger persisted in ``directory``, or None if none exists yet."""
        ledger = cls(directory)
        return ledger if ledger.exists else None

    # --- read surface -------------------------------------------------------

    @property
    def exists(self) -> bool:
        """True if a ledger file is on disk (asserted coverage is in force)."""
        return self._exists

    @property
    def path(self) -> Path:
        return self._path

    @property
    def entries(self) -> tuple[CoverageEntry, ...]:
        return tuple(self._entries)

    def covered(self, variant: str = "") -> RangeSet:
        """The normalised union of asserted ranges for ``variant``."""
        return RangeSet(
            e.range for e in self._entries if e.variant == variant
        )

    def covered_any(self) -> RangeSet:
        """The union across *all* variants — "are there rows worth serving?".

        The serve path (``available``/``fetch``) wants every asserted range
        regardless of variant (predicted funding rows are real, servable rows);
        only the funding hard gate must discriminate, via ``covered("")`` vs
        ``covered(FUNDING_PREDICTED_VARIANT)``.
        """
        return RangeSet(e.range for e in self._entries)

    # --- write surface ------------------------------------------------------

    def assert_covered(
        self,
        span: TimeRange,
        source: str,
        *,
        variant: str = "",
        written_ts: int | None = None,
    ) -> None:
        """Assert that ``span`` is fully captured, attributed to ``source``.

        A zero-width span asserts nothing and is ignored. (A covered range whose
        rows are empty is *valid* — a quiet market is data, not a gap.)
        """
        if source not in KNOWN_SOURCES:
            raise ValueError(
                f"unknown coverage source {source!r}; expected one of "
                f"{sorted(KNOWN_SOURCES)}"
            )
        if span.is_empty:
            return
        self._entries.append(
            CoverageEntry(
                start_ms=span.start_ms,
                end_ms=span.end_ms,
                source=source,
                written_ts=(
                    written_ts if written_ts is not None else int(time.time() * 1000)
                ),
                variant=variant,
            )
        )
        self._write()

    def remove(self, span: TimeRange, *, variant: str = "") -> None:
        """Retract coverage of ``span`` — pruned data stops being advertised.

        Entries overlapping ``span`` are trimmed (split if ``span`` punches a
        hole through the middle); each surviving piece keeps its provenance.
        """
        if span.is_empty:
            return
        hole = RangeSet((span,))
        kept: list[CoverageEntry] = []
        for e in self._entries:
            if e.variant != variant:
                kept.append(e)
                continue
            for piece in RangeSet((e.range,)).subtract(hole).ranges:
                kept.append(
                    CoverageEntry(
                        start_ms=piece.start_ms,
                        end_ms=piece.end_ms,
                        source=e.source,
                        written_ts=e.written_ts,
                        variant=e.variant,
                    )
                )
        self._entries = kept
        self._write()

    # --- persistence --------------------------------------------------------

    def _write(self) -> None:
        payload = {
            "schema_version": _LEDGER_SCHEMA_VERSION,
            "ranges": [
                {
                    "start_ms": e.start_ms,
                    "end_ms": e.end_ms,
                    "source": e.source,
                    "written_ts": e.written_ts,
                    "variant": e.variant,
                }
                for e in self._entries
            ],
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{LEDGER_FILENAME}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload, indent=1))
        os.replace(tmp, self._path)
        self._exists = True

    @staticmethod
    def _parse(payload: object) -> list[CoverageEntry]:
        if not isinstance(payload, dict):
            raise ValueError("coverage ledger must be a JSON object")
        entries: list[CoverageEntry] = []
        for row in payload.get("ranges", ()):
            entries.append(
                CoverageEntry(
                    start_ms=int(row["start_ms"]),
                    end_ms=int(row["end_ms"]),
                    source=str(row["source"]),
                    written_ts=int(row["written_ts"]),
                    variant=str(row.get("variant", "")),
                )
            )
        return entries
