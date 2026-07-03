"""Pure liquidation cascade — the orchestration N1 extracted from the loop (§6.5).

These exercise :func:`plan_liquidations` and :func:`adverse_mark` directly (§6.0,
D29): isolated close-whole, the cross-pool cascade with largest-loss-first ordering,
the solvent-fund backstop clamp, and the running-cash coupling where one forced
close drags the next under. Expected maintenance is taken from the independently
trusted ``check.py`` primitives; the cascade's own arithmetic (ordering, clamp,
running cash) is asserted against hand-computed values (D26).
"""

from __future__ import annotations

from flint.core.models import Candle, MarkSnapshot, Position, Side
from flint.engine.liquidation.cascade import adverse_mark, plan_liquidations
from flint.engine.money import money
from flint.venues import HYPERLIQUID

VENUE = "hyperliquid"
MARKET = "SOL-PERP"
HOUR_S = 3600
T0 = 1_700_000_000_000


def _candle(low=95.0, high=105.0) -> Candle:
    return Candle(
        ts=T0,
        open=100.0,
        high=high,
        low=low,
        close=100.0,
        volume=1000.0,
        market=MARKET,
        resolution_s=HOUR_S,
        venue=VENUE,
    )


def _pos(side=Side.LONG, size=10.0, entry=100.0, mode="cross", iso=0.0) -> Position:
    return Position(
        market=MARKET,
        venue=VENUE,
        side=side,
        size=size,
        entry_price=entry,
        margin_mode=mode,
        isolated_margin=iso,
    )


# --- adverse_mark: worst in-bar mark, or the candle extreme on Tier-C ------


def test_adverse_mark_uses_worst_recorded_mark_and_is_not_ambiguous():
    marks = [
        MarkSnapshot(market=MARKET, ts=T0 + 1, mark_price=99.0, index_price=99.0, venue=VENUE),
        MarkSnapshot(market=MARKET, ts=T0 + 2, mark_price=97.0, index_price=97.0, venue=VENUE),
    ]
    assert adverse_mark(Side.LONG, _candle(), marks) == (97.0, False)  # worst = min
    assert adverse_mark(Side.SHORT, _candle(), marks) == (99.0, False)  # worst = max


def test_adverse_mark_falls_back_to_candle_extreme_and_flags_ambiguous():
    # Tier-C (no recorded marks): long → candle low, short → candle high, path assumed.
    assert adverse_mark(Side.LONG, _candle(low=90.0), []) == (90.0, True)
    assert adverse_mark(Side.SHORT, _candle(high=110.0), []) == (110.0, True)


# --- no liquidation when the pool clears maintenance -----------------------


def test_solvent_position_yields_no_decisions():
    pos = _pos(size=1.0)  # notional 99, unrealized -1 against 1000 cash
    marks = {(VENUE, MARKET): (99.0, False)}
    decisions = plan_liquidations(
        {(VENUE, MARKET): pos}, money("1000"), marks, HYPERLIQUID, VENUE, T0
    )
    assert decisions == []


# --- isolated: closes whole against its own dedicated margin ---------------


def test_isolated_position_liquidates_against_its_own_margin():
    pos = _pos(mode="isolated", iso=20.0)  # long 10 @ 100, isolated margin 20
    marks = {(VENUE, MARKET): (80.0, True)}  # mark 80 → unrealized -200
    (decision,) = plan_liquidations(
        {(VENUE, MARKET): pos}, money("100000"), marks, HYPERLIQUID, VENUE, T0
    )
    # Backstop clamp: realizing the raw -200 would take the backing (20) negative,
    # so the solvent vault caps the loss at -backing and covers the 180 gap.
    assert decision.key == (VENUE, MARKET)
    assert decision.realized == -20.0
    assert decision.payload["realized_pnl"] == str(money(-20.0))
    assert decision.payload["insurance_shortfall"] == str(money(180.0))
    assert decision.payload["backstop"] is True
    assert decision.payload["margin_mode"] == "isolated"
    assert decision.payload["intrabar_ambiguous"] is True


# --- cross cascade: largest-loss first, running cash drags the next under --


def test_cross_cascade_orders_by_loss_and_couples_running_cash():
    a_key = (VENUE, MARKET)
    b_key = (VENUE, "ETH-PERP")
    a = _pos(size=10.0)  # mark 70 → unrealized -300 (the larger loss)
    b = Position(
        market="ETH-PERP", venue=VENUE, side=Side.LONG,
        size=5.0, entry_price=100.0, margin_mode="cross",
    )  # mark 90 → unrealized -50
    positions = {a_key: a, b_key: b}
    marks = {a_key: (70.0, False), b_key: (90.0, False)}

    decisions = plan_liquidations(positions, money("100"), marks, HYPERLIQUID, VENUE, T0)

    # A (loss -300) liquidates before B (loss -50): largest-loss-first.
    assert [d.key for d in decisions] == [a_key, b_key]
    # A: raw -300 clamps to -backing (-100), the vault covers 200; cash → 0.
    assert decisions[0].realized == -100.0
    assert decisions[0].payload["insurance_shortfall"] == str(money(200.0))
    # B is re-evaluated with the depleted pool: its equity is float(0) + (-50).
    assert decisions[1].payload["equity"] == -50.0
    # raw -50 clamps to -backing (now 0): the vault covers the full 50.
    assert decisions[1].realized == 0.0
    assert decisions[1].payload["insurance_shortfall"] == str(money(50.0))


def test_plan_liquidations_does_not_mutate_inputs():
    pos = _pos(size=10.0)
    positions = {(VENUE, MARKET): pos}
    marks = {(VENUE, MARKET): (70.0, False)}
    plan_liquidations(positions, money("100"), marks, HYPERLIQUID, VENUE, T0)
    assert positions == {(VENUE, MARKET): pos}  # pure: nothing deleted
