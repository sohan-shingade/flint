"""The liquidation cascade — pure orchestration over the mark-based check (§6.5).

Extracted from the legacy loop in N1 (§6.0, D29) as the **one** implementation of
the cross-pool cascade + isolated close-whole logic; since N10 the Nautilus
liquidation module is its only caller (the legacy loop was deleted). Pure:
positions / cash / marks / spec in, an ordered list of :class:`LiquidationDecision`
out — no mutation, no I/O. The caller applies each decision in order (credit the
realized amount to the venue account, delete the position, emit the LIQUIDATION
event); the arithmetic — including the running-cash coupling between successive
liquidations in the cascade — is reproduced here so the caller stays a thin applier.

``check.py`` holds the pure per-position primitives (maintenance, liquidation /
bankruptcy price); this module composes them into the §6.5 economics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flint.core.models import Candle, MarkSnapshot, Position, Side
from flint.venues import VenueSpec

from ..money import Money, money
from .check import bankruptcy_price, liquidation_price, tiered_maintenance


@dataclass(frozen=True)
class LiquidationDecision:
    """One forced close: the realized amount to credit + the LIQUIDATION payload.

    ``realized`` is the post-backstop cash delta (already reflecting any insurance
    coverage) — the caller credits ``money(realized)`` to the venue account and adds
    it to ``realized_pnl``, matching the legacy loop. ``payload`` is the exact
    LIQUIDATION event body (``realized_pnl``/``insurance_shortfall`` pre-stringified
    as ``Decimal``), emitted at ``ts``.
    """

    key: tuple[str, str]
    realized: float
    payload: dict[str, Any]
    ts: int


def adverse_mark(
    side: Side, candle: Candle, bar_marks: list[MarkSnapshot]
) -> tuple[float, bool]:
    """The bar's adverse mark for ``side`` + whether the path was assumed.

    With recorded mark snapshots (Tier A/B) the adverse extreme is the worst mark in
    the bar and the timing is known-enough. On OHLCV-only segments (Tier C) it falls
    back to the candle's adverse extreme — low for a long, high for a short — and the
    caller flags the event ``intrabar_ambiguous`` (§6.1 D11 pessimistic policy).
    """
    if bar_marks:
        prices = [m.mark_price for m in bar_marks]
        return (min(prices) if side is Side.LONG else max(prices)), False
    return (candle.low if side is Side.LONG else candle.high), True


def _decide(
    key: tuple[str, str],
    pos: Position,
    mark: float,
    equity: float,
    maintenance: float,
    *,
    backing: float,
    ambiguous: bool,
    ts: int,
    spec: VenueSpec,
) -> LiquidationDecision:
    """Compute one position's realized loss + LIQUIDATION payload (§6.5).

    HL charges no clearance fee, so the position realizes at the mark; the extra loss
    comes from the *backstop*: below ``backstop_maint_frac`` × maintenance the HLP
    vault takes over and the user forfeits remaining margin. Under the v1
    solvent-fund assumption the vault absorbs any sub-bankruptcy gap, so the backing
    cannot go negative — the covered ``insurance_shortfall`` is recorded so the
    tearsheet can surface it.
    """
    venue, market = key
    liq = spec.liquidation
    m_frac = liq.maint_frac(pos.notional(mark))
    realized = (mark - pos.entry_price) * pos.side.sign * pos.size
    backstop = equity < liq.backstop_maint_frac * maintenance
    shortfall = 0.0
    if backstop and liq.insurance_fund_solvent:
        max_loss = -backing  # realizing this leaves the backing at exactly zero
        if realized < max_loss:
            shortfall = max_loss - realized  # the vault covers this gap
            realized = max_loss
    payload = {
        "market": market,
        "venue": venue,
        "side": pos.side,
        "size": pos.size,
        "margin_mode": pos.margin_mode,
        "mark_price": mark,
        "equity": equity,
        "maintenance": maintenance,
        "liq_price": liquidation_price(pos, backing, m_frac),
        "bankruptcy_price": bankruptcy_price(pos, backing),
        "realized_pnl": str(money(realized)),
        "backstop": backstop,
        "insurance_fund_solvent": liq.insurance_fund_solvent,
        "insurance_shortfall": str(money(shortfall)),
        "adl_rank": liq.adl_rank,
        "intrabar_ambiguous": ambiguous,
    }
    return LiquidationDecision(key=key, realized=realized, payload=payload, ts=ts)


def plan_liquidations(
    positions: dict[tuple[str, str], Position],
    cash: Money,
    marks: dict[tuple[str, str], tuple[float, bool]],
    spec: VenueSpec,
    venue: str,
    ts: int,
) -> list[LiquidationDecision]:
    """Plan every liquidation this bar for ``venue``, in application order (§6.5).

    ``marks`` is the caller-built ``key -> (mark, ambiguous)`` map for the positions
    this venue can value this bar (the current market at its adverse in-bar extreme,
    others at their carried close mark). Isolated positions each close whole against
    their own ``isolated_margin``; cross positions share the venue pool, so one
    breaching position endangers all of them — the pool is re-evaluated after each
    forced close and the largest-loss position goes first, until the pool clears
    maintenance. Isolated liquidations settle first and their realized cash feeds the
    cross pool (the running-cash coupling the caller no longer has to model). Pure:
    ``positions`` and ``cash`` are read, never mutated.
    """
    decisions: list[LiquidationDecision] = []
    liq = spec.liquidation
    running_cash = cash

    # Isolated: each closes whole against its own dedicated margin.
    isolated = [
        k
        for k, p in positions.items()
        if k in marks and p.margin_mode == "isolated"
    ]
    for key in isolated:
        pos = positions[key]
        mark, ambiguous = marks[key]
        maintenance = tiered_maintenance(pos, mark, liq)
        equity = pos.isolated_margin + pos.unrealized_pnl(mark)
        if equity < maintenance:
            decision = _decide(
                key, pos, mark, equity, maintenance,
                backing=pos.isolated_margin, ambiguous=ambiguous, ts=ts, spec=spec,
            )
            decisions.append(decision)
            running_cash = running_cash + money(decision.realized)

    # Cross: shared-pool cascade over positions not yet liquidated.
    liquidated: set[tuple[str, str]] = {d.key for d in decisions}
    while True:
        items = [
            (key, positions[key], marks[key][0], marks[key][1])
            for key in list(positions)
            if key[0] == venue
            and key in marks
            and key not in liquidated
            and positions[key].margin_mode == "cross"
        ]
        if not items:
            return decisions
        pool_equity = float(running_cash) + sum(
            p.unrealized_pnl(m) for _, p, m, _ in items
        )
        pool_maint = sum(
            tiered_maintenance(p, m, liq) for _, p, m, _ in items
        )
        if pool_equity >= pool_maint:
            return decisions  # pool clears maintenance
        key, pos, mark, ambiguous = min(
            items, key=lambda it: it[1].unrealized_pnl(it[2])
        )
        decision = _decide(
            key, pos, mark, pool_equity, pool_maint,
            backing=float(running_cash), ambiguous=ambiguous, ts=ts, spec=spec,
        )
        decisions.append(decision)
        running_cash = running_cash + money(decision.realized)
        liquidated.add(key)
