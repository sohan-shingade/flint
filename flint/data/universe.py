"""UniverseResolver — static lists + point-in-time dynamic membership (D27, §2.11).

A *universe* is the set of markets a backtest considers. It is either **static**
(``["SOL-PERP", "BTC-PERP"]``) or **dynamic** (a rule like ``top:20:volume``,
re-evaluated on a schedule). Dynamic universes obey the same no-look-ahead law as
prices: membership at re-eval point ``t`` is ranked using **only** ranking data
timestamped ``< t`` (the shared ``core.time.last_before`` primitive), so we never
select hindsight winners — survivorship bias by another name.

The five-point contract (§2.11):
1. rank ``t``'s membership using only data ``ts < t``;
2. write each membership snapshot to the event log (deterministic replay) — via
   the injected ``emit`` seam, so the engine wires it to its event log;
3. apply ``exit_behavior`` when a held market drops out — ``hold_existing``
   (default: keep the position, no new entries), ``force_close``, or ``warn``;
4. on a ranking-data gap at a re-eval point, **hold the previous membership** and
   flag it — never silently re-rank on partial data;
5. (look-ahead linter check lands with the trust tooling, Phase 6.)

No membership or ranking value is ever invented (D26): a re-eval point with no
ranking data holds the prior snapshot rather than fabricating a ranking.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from flint.core.time import last_before

EmitFn = Callable[[str, Mapping[str, object]], None]

#: Event kind for a membership snapshot written to the event log.
UNIVERSE_SNAPSHOT = "universe_snapshot"


class ExitBehavior(StrEnum):
    """What happens to a held market when it drops out of the ranking (§2.11)."""

    HOLD_EXISTING = "hold_existing"  # default: keep the position, accept no new entries
    FORCE_CLOSE = "force_close"  # close the position and remove the market
    WARN = "warn"  # keep the position (no new entries) but flag the drop-out


@dataclass(frozen=True, slots=True)
class StaticUniverse:
    """A fixed set of markets — membership never changes."""

    markets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DynamicUniverse:
    """A ``top:N:metric`` rule ranked point-in-time over a candidate pool."""

    metric: str  # "volume" | "oi"
    n: int
    candidates: tuple[str, ...]
    exit_behavior: ExitBehavior = ExitBehavior.HOLD_EXISTING

    @classmethod
    def parse(
        cls,
        rule: str,
        candidates: Sequence[str],
        *,
        exit_behavior: ExitBehavior = ExitBehavior.HOLD_EXISTING,
    ) -> "DynamicUniverse":
        """Parse ``top:N:metric`` (e.g. ``top:20:volume``) over a candidate pool."""
        parts = rule.split(":")
        if len(parts) != 3 or parts[0] != "top":
            raise ValueError(f"unrecognised universe rule {rule!r}; expected top:N:metric")
        try:
            n = int(parts[1])
        except ValueError as exc:
            raise ValueError(f"universe rule {rule!r}: N must be an integer") from exc
        if n <= 0:
            raise ValueError(f"universe rule {rule!r}: N must be positive")
        return cls(metric=parts[2], n=n, candidates=tuple(candidates), exit_behavior=exit_behavior)


UniverseSpec = StaticUniverse | DynamicUniverse


@dataclass(frozen=True)
class Membership:
    """One resolved membership snapshot at a re-eval point (§2.11)."""

    ts_ms: int
    active: tuple[str, ...] = ()  # open for new entries (fresh ranking, in rank order)
    retained: tuple[str, ...] = ()  # held for management only — no new entries
    closing: tuple[str, ...] = ()  # force-closed this snapshot, then removed
    warnings: tuple[str, ...] = ()  # human-readable drop-out / gap flags
    gap: bool = False  # held previous membership due to a ranking-data gap

    @property
    def members(self) -> tuple[str, ...]:
        """Markets the strategy sees this snapshot (tradeable + managed-only)."""
        return self.active + self.retained

    def to_payload(self) -> dict[str, object]:
        """Serialisable event-log row (deterministic replay, §2.10)."""
        return {
            "ts": self.ts_ms,
            "active": list(self.active),
            "retained": list(self.retained),
            "closing": list(self.closing),
            "warnings": list(self.warnings),
            "gap": self.gap,
        }


class RankingData(ABC):
    """Point-in-time ranking-metric source (volume/OI) for dynamic universes."""

    @abstractmethod
    def snapshot_as_of(
        self, candidates: Sequence[str], metric: str, t_ms: int
    ) -> dict[str, float | None]:
        """Each candidate's ``metric`` using ONLY data ``ts < t`` (None if absent)."""
        ...


class InMemoryRankingData(RankingData):
    """Reference ranking source — per ``(market, metric)`` (ts, value) history.

    The durable source is the lake's point-in-time volume/OI (via the
    DataManager); this in-memory one backs the resolver's tests with
    hand-authored histories and enforces ``ts < t`` through ``last_before``.
    """

    def __init__(self) -> None:
        self._series: dict[tuple[str, str], list[tuple[int, float]]] = {}

    def add(self, market: str, metric: str, points: Sequence[tuple[int, float]]) -> None:
        series = self._series.setdefault((market, metric), [])
        series.extend(points)
        series.sort(key=lambda p: p[0])

    def snapshot_as_of(
        self, candidates: Sequence[str], metric: str, t_ms: int
    ) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for market in candidates:
            series = self._series.get((market, metric), [])
            point = last_before(series, t_ms, key=lambda p: p[0])
            out[market] = point[1] if point is not None else None
        return out


class UniverseResolver:
    """Resolves a universe spec into a sequence of membership snapshots (§2.11)."""

    def __init__(
        self,
        spec: UniverseSpec,
        ranking: RankingData | None = None,
        *,
        emit: EmitFn | None = None,
    ) -> None:
        if isinstance(spec, DynamicUniverse) and ranking is None:
            raise ValueError("a dynamic universe needs a RankingData source")
        self._spec = spec
        self._ranking = ranking
        self._emit = emit

    def resolve(self, reeval_points: Sequence[int]) -> list[Membership]:
        """Resolve membership at each re-eval point, emitting every snapshot."""
        snapshots: list[Membership] = []
        previous: Membership | None = None
        for t in reeval_points:
            snap = self._resolve_at(t, previous)
            if self._emit is not None:
                self._emit(UNIVERSE_SNAPSHOT, snap.to_payload())
            snapshots.append(snap)
            previous = snap
        return snapshots

    def all_members(self, snapshots: Sequence[Membership]) -> frozenset[str]:
        """Every market that is a member in any snapshot — the set to funding-gate.

        A member that fails the funding hard gate anywhere in the range must
        reject the whole run up front, never be dropped mid-run (§2.11/§6.3).
        """
        out: set[str] = set()
        for s in snapshots:
            out.update(s.members)
            out.update(s.closing)
        return frozenset(out)

    # --- internals ---------------------------------------------------------

    def _resolve_at(self, t: int, previous: Membership | None) -> Membership:
        if isinstance(self._spec, StaticUniverse):
            return Membership(ts_ms=t, active=self._spec.markets)
        return self._resolve_dynamic(self._spec, t, previous)

    def _resolve_dynamic(
        self, spec: DynamicUniverse, t: int, previous: Membership | None
    ) -> Membership:
        assert self._ranking is not None  # guarded in __init__
        snapshot = self._ranking.snapshot_as_of(spec.candidates, spec.metric, t)
        available = {m: v for m, v in snapshot.items() if v is not None}

        need = min(spec.n, len(spec.candidates))
        if len(available) < need:
            # Ranking-data gap — hold the previous membership, never re-rank on
            # partial data (§2.11 point 4).
            flag = f"ranking-data gap at ts={t}: held previous membership ({spec.metric})"
            if previous is None:
                return Membership(ts_ms=t, warnings=(flag,), gap=True)
            return Membership(
                ts_ms=t,
                active=previous.active,
                retained=previous.retained,
                warnings=previous.warnings + (flag,),
                gap=True,
            )

        # Rank available candidates by metric desc, tie-break by name for a
        # deterministic, replayable ordering.
        ranked = sorted(available.items(), key=lambda kv: (-kv[1], kv[0]))
        new_active = tuple(m for m, _ in ranked[: spec.n])

        held_prev = (
            set(previous.active) | set(previous.retained) if previous else set()
        )
        dropped = held_prev - set(new_active)

        retained: list[str] = []
        closing: list[str] = []
        warnings: list[str] = []
        for market in sorted(dropped):
            if spec.exit_behavior is ExitBehavior.FORCE_CLOSE:
                closing.append(market)
            else:
                retained.append(market)
                if spec.exit_behavior is ExitBehavior.WARN:
                    warnings.append(
                        f"{market} dropped out of top-{spec.n} at ts={t}; held (warn)"
                    )

        return Membership(
            ts_ms=t,
            active=new_active,
            retained=tuple(retained),
            closing=tuple(closing),
            warnings=tuple(warnings),
        )
