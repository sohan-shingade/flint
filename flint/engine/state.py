"""Engine state — the account and position book the loop mutates (§6.1, §6.6).

This is the in-memory portfolio the per-bar loop reads and writes. It is
deliberately thin in slice 3.1: one ``Account`` per venue holding ``Decimal``
cash plus running monetary counters, and a position book keyed by
``(venue, market)`` exactly as the two contexts key theirs. The per-venue
account *structure* (sizing helpers, cross/isolated margin split) is fleshed out
in slice 3.5/3.4; the shape is settled now so those slices add fields, not a
rewrite.

Cash is free collateral, not spent notional: opening a perp at the fair mark
reserves margin but does not move cash, so ``equity = cash + unrealized_pnl``.
The only things that move cash are realized PnL, fees, and funding — every one a
``Decimal`` accumulator (§5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flint.core.models import Position

from .money import ZERO, Money, money


@dataclass
class Account:
    """One venue's margin account: ``Decimal`` cash plus running counters.

    ``cash`` is free collateral. The counters are cumulative attribution totals
    the tearsheet reads (fees paid, funding paid/received, realized PnL) — all
    ``Decimal`` so they never float-drift over a long run.
    """

    venue: str
    cash: Money = ZERO
    fees_paid: Money = ZERO
    funding_paid: Money = ZERO
    realized_pnl: Money = ZERO

    def credit(self, amount: Money) -> None:
        """Add ``amount`` (signed) to free cash."""
        self.cash += amount


@dataclass
class PortfolioState:
    """The whole simulated wallet: per-venue accounts + a position book.

    Positions are keyed by ``(venue, market)`` — the same key both execution
    contexts use — so cross-venue books never collide. v1 instantiates a single
    Hyperliquid account; the dict shape makes a second account additive (§6.6).
    """

    accounts: dict[str, Account] = field(default_factory=dict)
    positions: dict[tuple[str, str], Position] = field(default_factory=dict)

    def account(self, venue: str) -> Account:
        """The account for ``venue``, created with zero cash on first touch."""
        acct = self.accounts.get(venue)
        if acct is None:
            acct = Account(venue=venue)
            self.accounts[venue] = acct
        return acct

    def fund(self, venue: str, cash: float | Money) -> Account:
        """Seed ``venue``'s account with starting ``cash`` (test/setup helper)."""
        acct = self.account(venue)
        acct.cash = money(cash)
        return acct

    def position(self, venue: str, market: str) -> Position | None:
        """The open position on ``(venue, market)``, or ``None``."""
        return self.positions.get((venue, market))

    def equity(self, venue: str, marks: dict[str, float]) -> float:
        """Venue equity = cash + unrealized PnL of that venue's positions.

        ``marks`` maps market -> current mark price. A position whose market has
        no mark contributes zero unrealized (it cannot be valued this bar).
        """
        total = float(self.account(venue).cash)
        for (pos_venue, market), pos in self.positions.items():
            if pos_venue != venue:
                continue
            mark = marks.get(market)
            if mark is not None:
                total += pos.unrealized_pnl(mark)
        return total

    def cross_margin_available(
        self, venue: str, marks: dict[str, float]
    ) -> float:
        """The shared cross pool on ``venue`` = equity − locked isolated margin (§6.5).

        Cross positions all draw on this one pool, so one breaching endangers the
        rest (the cascade in ``loop._resolve_cross_pool``). Isolated positions
        consume only their own dedicated ``isolated_margin`` and are excluded — a
        scalar ``margin_used`` would hide exactly that split, which is what gets
        leveraged traders killed. The sizing helpers (§8.1, slice 3.6) size off
        this and ``equity`` so positions compound with the account.
        """
        isolated = sum(
            pos.isolated_margin
            for (pos_venue, _market), pos in self.positions.items()
            if pos_venue == venue and pos.margin_mode == "isolated"
        )
        return self.equity(venue, marks) - isolated
