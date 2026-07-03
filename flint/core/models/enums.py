"""Trading enumerations (§5, §8.1).

String-valued (``enum.StrEnum``, Python 3.11+) so they serialize straight into
events and Parquet without a separate codec, and so equality with wire strings
("long", "market", "gtc") just works.
"""

from __future__ import annotations

from enum import StrEnum


class Side(StrEnum):
    """Direction of an order, fill, or position (one enum, shared — §5).

    ``LONG`` is the positive-exposure / buy direction, ``SHORT`` the negative /
    sell direction. ``sign`` gives the PnL and exposure multiplier; ``opposite``
    gives the reducing direction (what a close of this side trades).
    """

    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        """+1 for LONG, -1 for SHORT — the exposure/PnL multiplier."""
        return 1 if self is Side.LONG else -1

    @property
    def opposite(self) -> "Side":
        """The direction that reduces a position of this side."""
        return Side.SHORT if self is Side.LONG else Side.LONG


class OrderType(StrEnum):
    """Order kinds the engine understands (§5, §8.1)."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    TAKE_PROFIT = "take_profit"


class TimeInForce(StrEnum):
    """Order lifetime policy (§5, §8.1).

    Market signals default to ``IOC``; limit signals default to ``GTC``.
    """

    IOC = "ioc"  # immediate-or-cancel
    GTC = "gtc"  # good-till-cancelled
    FOK = "fok"  # fill-or-kill
