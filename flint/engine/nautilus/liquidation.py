"""``FlintLiquidationModule`` — §6.5 mark liquidation on the Nautilus lane (§A5).

Nautilus does no mark-based liquidation of its own (spike-verified, D29); the §6.5
economics — tiered maintenance, the intrabar adverse-extreme trigger, the cross-pool
cascade, isolated close-whole, and the 2/3-maintenance backstop with a solvent
insurance fund — are Flint's. This module is the Nautilus-lane equivalent of the
legacy ``loop._check_liquidations``: it builds the same ``key -> (mark, ambiguous)``
map and delegates to the pure :func:`~flint.engine.liquidation.cascade.plan_liquidations`
**verbatim** (the one §6.5 implementation both engines share), so parity with the
legacy engine reduces to "the same inputs reach the same pure planner."

It is a :class:`SimulationModule` (like :class:`~flint.engine.nautilus.funding.FlintFundingModule`),
**not** an actor, and is **registered after funding** so the tick lane (N8) settles
funding before checking liquidation in the same timestep (registration order is
processing order — proven by ``test_modules_process_in_registration_order``). This
preserves §6.1's funding-saves-position ordering: a funding credit at step (3) can
lift a position over maintenance so the step (4) check never liquidates it.

**Who drives the check, and the forced-close mechanic.** In the **bar lane** the
:class:`~flint.engine.nautilus.shim.BarLaneStrategy` calls :meth:`check_bar` inline at
step (4) of its locked per-bar sequence — immediately after funding, exactly where
``loop._check_liquidations`` sits. :meth:`process` is a **documented no-op in the bar
lane** (the tick lane in N8 will drive the same check through it). The
``SimulatedExchange`` has no force-close API (spike-verified), so a forced close is
applied in two halves that keep both books consistent (plan option (b), chosen over
the tagged reduce-only-at-mark option (a) because it is uniform for backstop and
non-backstop closes and never routes liquidation loss through Nautilus's own fill
PnL):

* **Money** — applied once, in this module. The recorder credits the shadow book and
  emits the LIQUIDATION event (``record_liquidation``, byte-identical to
  ``loop._apply_liquidation``); :meth:`SimulatedExchange.adjust_account` mirrors the
  same Decimal onto the Nautilus account (as funding does), so run-end reconciliation
  (§19.4) stays exact whether or not the backstop clamps the loss.
* **Position** — the Nautilus position is flattened by the shim submitting a
  reduce-only close **at the position's entry price** with zero fee (a
  :class:`~flint.engine.nautilus.execmodels.FlintPriceFillModel` fill pinned there):
  entry-priced ⇒ Nautilus realizes exactly zero on the fill, so ``adjust_account``
  above remains the sole money mover. The close carries a ``liquidation_close`` tag so
  the recorder ignores it (the shadow book was already updated here). :meth:`check_bar`
  returns these :class:`LiquidationClose` instructions; only the order submission —
  a Strategy capability — lives in the shim.

**Marks (the N3 double-write resolution).** The current market is valued at its
in-bar adverse extreme (:func:`adverse_mark`); every other position on the venue is
valued at its carried bar-close mark. Those carried marks are owned by the shim's
step-(8) ``recorder.set_mark`` (for equity) — this module *reads* them back from the
recorder rather than carrying its own, so there is a single writer of the carry and
no divergence. Legacy carries ``_last_mark[curkey]`` inside step (4); the Nautilus
lane carries it in step (8), but the value (``bar_marks[-1] or close``) and every
cross-market read of it are byte-identical because nothing mutates it in between.
"""

from __future__ import annotations

from dataclasses import dataclass

from flint.core.models import Side
from flint.engine.liquidation.cascade import adverse_mark, plan_liquidations
from flint.engine.money import money
from flint.engine.state import PortfolioState
from flint.venues import VenueSpec

from ._compat import USDC, Money, SimulationModule, SimulationModuleConfig


@dataclass(frozen=True)
class LiquidationClose:
    """A Nautilus-position flatten the shim submits for one applied liquidation.

    The shadow book and the LIQUIDATION event have already been written by
    :meth:`FlintLiquidationModule.check_bar`; this only tells the shim which Nautilus
    position to close and at what (entry) price so the close realizes zero on the
    Nautilus account and its position book agrees with the shadow at run end.
    """

    market: str
    venue: str
    side: Side
    size: float
    price: float


class FlintLiquidationModule(SimulationModule):
    """Check §6.5 liquidation on the Nautilus core, byte-exact with the legacy loop (§A5)."""

    def __init__(
        self,
        config: SimulationModuleConfig,
        *,
        recorder,
        shadow: PortfolioState,
        venue_spec: VenueSpec,
    ) -> None:
        super().__init__(config)
        # SimulationModule derives from the Cython ``Actor``/``Component`` base, which
        # reserves plain attribute names — every attribute is ``_flint_``-prefixed.
        self._flint_recorder = recorder
        self._flint_shadow = shadow
        self._flint_spec = venue_spec

    # -- bar-lane driver (shim-called, step (4) of the per-bar sequence) --------

    def check_bar(self, candle, bar_marks) -> list[LiquidationClose]:
        """Plan + apply this bar's liquidations for ``candle``'s venue (§6.5).

        Byte-identical to ``loop._check_liquidations`` + ``_apply_liquidation``: build
        the venue's ``key -> (mark, ambiguous)`` map (the current market at its in-bar
        adverse extreme, every other position at its carried close mark read back from
        the recorder), delegate to :func:`plan_liquidations`, then for each decision
        credit the shadow book + emit LIQUIDATION (via the recorder) and mirror the
        Decimal onto the Nautilus account (``adjust_account``). Returns the Nautilus
        flattens the shim submits — the shadow book is already settled here.
        """
        venue = candle.venue
        curkey = (venue, candle.market)
        positions = self._flint_shadow.positions
        # Best-available mark (+ path-ambiguity) per position on this venue.
        marks: dict[tuple[str, str], tuple[float, bool]] = {}
        curpos = positions.get(curkey)
        if curpos is not None:
            marks[curkey] = adverse_mark(curpos.side, candle, bar_marks)
        carried = self._flint_recorder.carried_marks()
        for key in positions:
            if key[0] == venue and key != curkey and key in carried:
                marks[key] = (carried[key], True)  # carried close mark → path assumed

        decisions = plan_liquidations(
            positions,
            self._flint_shadow.account(venue).cash,
            marks,
            self._flint_spec,
            venue,
            candle.ts,
        )
        closes: list[LiquidationClose] = []
        for decision in decisions:
            pos = positions[decision.key]  # capture the close before the recorder drops it
            closes.append(
                LiquidationClose(
                    market=pos.market,
                    venue=pos.venue,
                    side=pos.side,
                    size=pos.size,
                    price=pos.entry_price,
                )
            )
            # The recorder is the single shadow-book + EventLog writer: it credits the
            # realized loss, accrues realized_pnl, drops the position, and emits the
            # LIQUIDATION event with the legacy payload (§6.5).
            self._flint_recorder.record_liquidation(decision)
            # Mirror the same Decimal onto the Nautilus account so run-end
            # reconciliation (§19.4) balances shadow cash against the Nautilus balance;
            # the entry-priced flatten the shim submits then realizes exactly zero.
            self.exchange.adjust_account(Money(money(decision.realized), USDC))
        return closes

    # -- tick-lane driver (deferred to N8) -------------------------------------

    def process(self, ts_now: int) -> None:
        """No-op in the bar lane — the shim drives the check (see module docstring).

        The tick lane (N8) will drive the same §6.5 check here, in the exchange step,
        after the funding module's ``process`` in the same timestep (registration
        order: funding registered before liquidation). It will need its own Nautilus
        forced-close path, since the bar-lane flatten is submitted by the shim, which
        the tick lane does not have — the trap N8 must close.
        """

    def log_diagnostics(self, logger) -> None:  # pragma: no cover - diagnostics only
        """Abstract on ``SimulationModule``; Flint routes liquidations to the EventLog."""

    def reset(self) -> None:  # pragma: no cover - single-run backtests
        """Abstract on ``SimulationModule``; a Flint run is constructed per backtest."""


def build_liquidation_module(
    *, recorder, shadow: PortfolioState, venue_spec: VenueSpec
) -> FlintLiquidationModule:
    """Construct the liquidation module (always — any open position can liquidate).

    Unlike funding (which needs a final row to have anything to settle), liquidation
    is unconditional: a candle-only feed with an open position can still breach
    maintenance, so the module is always registered — after funding.
    """
    return FlintLiquidationModule(
        SimulationModuleConfig(),
        recorder=recorder,
        shadow=shadow,
        venue_spec=venue_spec,
    )


__all__ = ["FlintLiquidationModule", "LiquidationClose", "build_liquidation_module"]
