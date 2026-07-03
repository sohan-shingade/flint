"""``FlintRecorder`` — the single writer to the Flint EventLog (§A6).

Nautilus produces the run's *facts* (bars close, orders fill); Flint owns the
*accounting*. The recorder is a Nautilus ``Actor`` that sits inside the backtest,
maintains a **shadow book** (today's :class:`PortfolioState`), and is the sole
emitter into the injected :class:`EventLog`. Keeping one writer means the stored
event log and the shadow book cannot silently diverge from each other, and the
existing ``fold``/``build_tearsheet``/``check_invariants`` machinery folds a
Nautilus run exactly as it folds a legacy run — ``tearsheet.py`` and
``portfolio/`` stay untouched (§A6).

N2 scope (the skeleton):

* ``RUN_STARTED`` on actor start, ``RUN_FINISHED`` on actor stop — bracketing the
  run exactly where the legacy loop brackets it.
* One per-bar ``EQUITY`` snapshot on each bar close, with **today's exact payload
  shape** computed from the shadow book (byte-identical to ``loop._emit_equity``),
  its ``ts`` normalized back to the bar START.
* :meth:`reconcile` — the run-end shadow-vs-Nautilus invariant (§19.4).

FILL translation (N3) slots in here: an ``OrderFilled`` fact folds into the shadow
book through the *same* ``apply_fill_delta`` reducer the legacy engine uses and
emits a FILL event, so the shadow book stays the sole source of accounting.
"""

from __future__ import annotations

from decimal import Decimal

from flint.engine.money import ZERO, money
from flint.engine.portfolio.events import (
    EQUITY,
    RUN_FINISHED,
    RUN_STARTED,
)
from flint.engine.state import PortfolioState
from flint.engine.tearsheet import InvariantError

from . import timeconv
from ._compat import Actor, BarType

# §19.4 run-end reconciliation tolerance on the Decimal cash line. A Noop run has
# no float→Decimal conversions so it reconciles exactly; the tolerance covers the
# rounding that fill/funding math introduces once N3/N4 land.
CASH_TOLERANCE = Decimal("0.01")


class FlintRecorder(Actor):
    """Single EventLog writer + shadow book for one Nautilus backtest (§A6)."""

    def __init__(
        self,
        *,
        event_log,
        shadow: PortfolioState,
        bar_types: list[BarType],
        resolution_s: int,
        engine_name: str,
    ) -> None:
        super().__init__()
        # Attributes are ``_flint_``-prefixed: the Nautilus ``Component`` base
        # (Cython) reserves plain names like ``_log`` and forbids overwriting them.
        self._flint_log = event_log
        self._flint_shadow = shadow
        self._flint_bar_types = bar_types
        self._flint_resolution_s = resolution_s
        self._flint_engine_name = engine_name
        # Per-(venue, market) last mark for valuing open positions at a bar. Stays
        # empty in N2 (Noop holds no positions → unrealized is 0); N3 populates it.
        self._flint_last_mark: dict[tuple[str, str], float] = {}

    # -- run lifecycle ---------------------------------------------------------

    def on_start(self) -> None:
        self._flint_log.emit(RUN_STARTED, {"engine": self._flint_engine_name})
        for bar_type in self._flint_bar_types:
            self.subscribe_bars(bar_type)

    def on_stop(self) -> None:
        self._flint_log.emit(RUN_FINISHED, {"engine": self._flint_engine_name})

    # -- per-bar equity --------------------------------------------------------

    def on_bar(self, bar) -> None:
        """Emit one EQUITY snapshot per bar close, stamped at the bar START (§6.1).

        Nautilus stamps the bar at its close; the EQUITY event carries the bar
        START to match the legacy per-bar snapshot's ``ts`` exactly.
        """
        start_ms = timeconv.bar_close_ns_to_candle_start_ms(
            bar.ts_event, self._flint_resolution_s
        )
        self._emit_equity(start_ms)

    def _emit_equity(self, ts: int) -> None:
        """Portfolio-wide EQUITY from the shadow book — byte-identical to ``loop._emit_equity``."""
        cash = ZERO
        accrued_funding = ZERO
        for acct in self._flint_shadow.accounts.values():
            cash += acct.cash
            accrued_funding += acct.funding_paid
        unrealized = 0.0
        for key, pos in self._flint_shadow.positions.items():
            mark = self._flint_last_mark.get(key)
            if mark is not None:
                unrealized += pos.unrealized_pnl(mark)
        unrealized_money = money(unrealized)
        self._flint_log.emit(
            EQUITY,
            {
                "equity": str(cash + unrealized_money),
                "unrealized": str(unrealized_money),
                "accrued_funding": str(accrued_funding),
                "cash": str(cash),
            },
            ts=ts,
        )

    # -- run-end reconciliation (§19.4) ---------------------------------------

    def reconcile(self, *, portfolio, cache, venue, settlement_currency) -> None:
        """Assert the shadow book agrees with Nautilus's own account/positions (§19.4).

        Two event models describe one run; this closes the loop at run end. The
        shadow cash must equal the Nautilus account balance (within
        :data:`CASH_TOLERANCE`), and the set of open positions must match. A
        mismatch is an engine bug and raises :class:`InvariantError`.
        """
        shadow_cash = sum((a.cash for a in self._flint_shadow.accounts.values()), ZERO)
        account = portfolio.account(venue)
        nautilus_cash = account.balance_total(settlement_currency).as_decimal()
        if abs(shadow_cash - nautilus_cash) > CASH_TOLERANCE:
            raise InvariantError(
                f"shadow cash {shadow_cash} != Nautilus balance {nautilus_cash} "
                f"(tolerance {CASH_TOLERANCE}) at run end"
            )
        shadow_open = {
            market
            for (_, market), pos in self._flint_shadow.positions.items()
            if pos.size != 0
        }
        nautilus_open = {
            p.instrument_id.symbol.value for p in cache.positions_open()
        }
        if shadow_open != nautilus_open:
            raise InvariantError(
                f"shadow open positions {sorted(shadow_open)} != Nautilus open "
                f"positions {sorted(nautilus_open)} at run end"
            )


__all__ = ["FlintRecorder", "CASH_TOLERANCE"]
