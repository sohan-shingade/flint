"""``FlintFundingModule`` — final-only funding settlement on the Nautilus lane (§A4, §6.4).

Nautilus settles **no funding at all** (spike-verified, D29); the predicted/final
contract and the §6.4 settlement economics are Flint's. This module is the
Nautilus-lane equivalent of the legacy ``loop._settle_funding``: it wraps the three
pure primitives in :mod:`flint.engine.funding.settlement` **verbatim**
(``settlement_price_for`` → ``clamp_funding_rate`` → ``settlement_payment``), so
parity with the legacy engine reduces to "the same inputs reach the same pure math."

It is a :class:`SimulationModule` (the ``FXRolloverInterestModule`` precedent), **not**
an actor, for one reason the bar-lane still needs: only a ``SimulationModule`` holds
``self.exchange`` and can call :meth:`SimulatedExchange.adjust_account` to move the
Nautilus account when a settlement is not an order fill. Each settlement therefore
moves money on **both** books consistently — the Nautilus account (``adjust_account``)
and Flint's shadow book (via the recorder) — so the run-end shadow↔Nautilus
reconciliation (§19.4) stays exact.

**Who drives settlement, and why it differs by lane.** In the **bar lane** the
:class:`~flint.engine.nautilus.shim.BarLaneStrategy` calls :meth:`settle_bar`
inline at step (3) of its locked per-bar sequence — between the T+1 fills and the
resting orders, exactly where ``loop._settle_funding`` sits (§6.1). This is
**mandatory** for byte-exact parity: the shim emits the per-bar EQUITY snapshot at
the end of ``on_bar``, which Nautilus dispatches during ``data_engine.process`` —
*before* it runs ``module.process`` for that timestep. A settlement driven off
``process(ts_now)`` would land after the bar's own equity snapshot and lag the
``accrued_funding`` curve by one bar. So :meth:`process` is a **documented no-op in
the bar lane**; the tick lane (N8), which has no per-bar shim, will drive settlement
through ``process`` instead. Module *registration* order (funding before liquidation)
still governs the tick lane's within-timestep ordering — Nautilus iterates
``exchange.modules`` in registration order (verified against the pinned engine).

Only **final** rows ever settle here. Predicted rows are strategy data (they flow to
``ctx.funding_rate`` and, in the tick lane, to ``on_funding``); this module is
constructed with the final-rate series only and never sees the predicted stream.
"""

from __future__ import annotations

from flint.core.models import FundingRate, MarkSnapshot
from flint.engine.funding.settlement import (
    clamp_funding_rate,
    settlement_payment,
    settlement_price_for,
)
from flint.venues import VenueSpec

from . import timeconv
from ._compat import USDC, Money, SimulationModule, SimulationModuleConfig


class FlintFundingModule(SimulationModule):
    """Settle §6.4 funding on the Nautilus core, byte-exact with the legacy loop (§A4)."""

    def __init__(
        self,
        config: SimulationModuleConfig,
        *,
        recorder,
        shadow,
        feed,
        venue_spec: VenueSpec,
        lane: str = "bar",
    ) -> None:
        super().__init__(config)
        # SimulationModule derives from the Cython ``Actor``/``Component`` base, which
        # reserves plain attribute names — every attribute is ``_flint_``-prefixed.
        self._flint_recorder = recorder
        self._flint_shadow = shadow
        self._flint_spec = venue_spec
        # The lane decides who drives settlement: ``"bar"`` = shim-driven (settle_bar,
        # a per-bar no-op process); ``"tick"`` = process-driven (settle_bar unused).
        self._flint_lane = lane
        # Final-rate series per market (predicted rows are strategy data, never
        # settled here — §6.4). Sorted by ts so a wide bar settles in ts order.
        self._flint_final: dict[str, list[FundingRate]] = {
            market: sorted(
                (r for r in rows if r.rate_type == "final"), key=lambda r: r.ts
            )
            for market, rows in feed.funding.items()
        }
        # Mark/oracle series per market — the settlement price basis (§6.4). Read
        # only; the shim owns the bar-close mark carried to the recorder for equity.
        self._flint_marks: dict[str, list[MarkSnapshot]] = feed.marks
        # Tick-lane settlement cursor: how many of each market's ts-sorted final rows
        # ``process`` has already settled, so a later ``process(ts_now)`` settles only
        # the newly-due rows exactly once, in ts order (bar lane leaves this unused).
        self._flint_cursor: dict[str, int] = {m: 0 for m in self._flint_final}

    # -- bar-lane driver (shim-called, step (3) of the per-bar sequence) --------

    def settle_bar(self, candle, end: int) -> None:
        """Settle every final row whose ts falls in ``[candle.ts, end)``, in ts order.

        Byte-identical to ``loop._settle_funding``: each settlement applies
        individually at its own rate (a wide bar over hourly funding is never one
        lumped payment), priced on the ``price_basis`` price at the settlement second
        (hard-fail on a missing mark — never the bar close, §6.4), clamped to the
        venue cap, scaled by the settlement's own ``interval_s``. Only a position open
        at the settlement ts is charged.
        """
        venue = candle.venue
        market = candle.market
        marks = self._flint_marks.get(market, [])
        settlements = [
            r for r in self._flint_final.get(market, []) if candle.ts <= r.ts < end
        ]
        for rate in settlements:
            self._settle_one(rate, venue, market, marks)

    # -- tick-lane driver (deferred to N8) -------------------------------------

    def process(self, ts_now: int) -> None:
        """Tick-lane driver — settle every final row now due (``ts <= ts_now``), in ts order.

        A **no-op in the bar lane** (the shim drives settlement via :meth:`settle_bar`
        so it lands before the bar's EQUITY snapshot; running here would settle *after*
        that snapshot and lag ``accrued_funding`` by a bar). In the **tick lane** this
        runs in the exchange step, **before** the liquidation module's ``process`` in
        the same timestep (registration order: funding registered first), preserving
        §6.1's funding-saves-position ordering. ``ts_now`` is Nautilus's exchange clock
        in **nanoseconds**; it is converted to unix-ms to compare against the rows'
        ms timestamps. Nautilus calls ``process`` several times per step, so the
        per-market cursor is what makes each settlement land exactly once, at its own
        rate/price/interval (never one lumped payment, §6.4).
        """
        if self._flint_lane != "tick":
            return
        ts_now_ms = timeconv.ns_to_ms(ts_now)
        for market, rows in self._flint_final.items():
            marks = self._flint_marks.get(market, [])
            cursor = self._flint_cursor[market]
            while cursor < len(rows) and rows[cursor].ts <= ts_now_ms:
                rate = rows[cursor]
                self._settle_one(rate, rate.venue, market, marks)
                cursor += 1
            self._flint_cursor[market] = cursor

    def log_diagnostics(self, logger) -> None:  # pragma: no cover - diagnostics only
        """Abstract on ``SimulationModule``; Flint routes settlement to the EventLog."""

    def reset(self) -> None:  # pragma: no cover - single-run backtests
        """Abstract on ``SimulationModule``; a Flint run is constructed per backtest."""

    # -- one settlement (the three pure functions, verbatim) -------------------

    def _settle_one(
        self, rate: FundingRate, venue: str, market: str, marks: list[MarkSnapshot]
    ) -> None:
        pos = self._flint_shadow.positions.get((venue, market))
        if pos is None:
            return  # only positions open at the settlement ts are charged (§6.4)
        price = settlement_price_for(rate, marks)  # hard-gate: no silent bar-close
        capped_rate, was_capped = clamp_funding_rate(
            rate.rate_hourly, self._flint_spec.rate_cap_hourly
        )
        amount = settlement_payment(pos, capped_rate, price, rate.interval_s)
        # The recorder is the single shadow-book + EventLog writer: it credits cash,
        # accrues funding_paid, and emits the FUNDING event with the legacy payload.
        self._flint_recorder.record_funding(
            venue=venue,
            market=market,
            rate_hourly=rate.rate_hourly,
            settled_rate_hourly=capped_rate,
            rate_capped=was_capped,
            interval_s=rate.interval_s,
            price_basis=rate.price_basis,
            rate_type=rate.rate_type,
            oracle_price=price,
            size=pos.size,
            amount=amount,
            ts=rate.ts,
        )
        # Mirror the same Decimal move onto the Nautilus account so the run-end
        # reconciliation (§19.4) balances shadow cash against the Nautilus balance.
        self.exchange.adjust_account(Money(amount, USDC))


def build_funding_module(
    *, recorder, shadow, feed, venue_spec: VenueSpec, lane: str = "bar"
) -> FlintFundingModule | None:
    """Construct the module iff the feed carries at least one final funding row.

    A candle-only feed with no final rows needs no settlement module — returning
    ``None`` keeps the module list empty and the assembly unchanged for that case.
    ``lane`` decides the driver: ``"bar"`` (shim-driven) or ``"tick"``
    (``process``-driven, §6.4).
    """
    has_final = any(
        r.rate_type == "final" for rows in feed.funding.values() for r in rows
    )
    if not has_final:
        return None
    return FlintFundingModule(
        SimulationModuleConfig(),
        recorder=recorder,
        shadow=shadow,
        feed=feed,
        venue_spec=venue_spec,
        lane=lane,
    )


__all__ = ["FlintFundingModule", "build_funding_module"]
