"""``OraclePoolFillModel`` — the oracle-pool seam, stub until Jupiter (D28).

Oracle-pool venues (Jupiter Perps, GMX) fill at the spot oracle price valid at the
effective time **plus the venue's price-impact fee** (size vs pool depth), and
liquidate on a *smoothed* oracle (Jupiter's EMA), not spot (§6.3). None of that
can be modelled honestly without recorded on-chain data (Doves oracle + custody
accounts), which lands with the v1.x Jupiter phase — so there is **no synthetic
stand-in** (D26).

This class exists now only so the ``FillModel`` seam is proven against both market
structures (the CLOB model and this share one interface). Calling it raises,
loudly, rather than returning a fabricated fill.
"""

from __future__ import annotations

from flint.core.models import Order

from .base import FillContext, FillModel, FillResult


class OraclePoolFillModel(FillModel):
    """Interface stub for oracle-pool fills — implemented in v1.x with Jupiter."""

    def fill(self, order: Order, ctx: FillContext) -> FillResult | None:
        raise NotImplementedError(
            "oracle-pool fills (Jupiter/GMX) land in v1.x (D28); there is no "
            "synthetic stand-in without recorded on-chain oracle + custody data (D26)"
        )
