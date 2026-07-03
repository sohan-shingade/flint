"""The Signal — the strategy's declarative output (§8.1).

``Strategy.on_candle`` returns a ``list[Signal]`` (empty = do nothing). A Signal
is a *declared intent*; the engine converts it to an ``Order`` under the §8.1
rules (sizing at the execution bar's open, tick/lot rounding, reduce-only for
close). Frozen: a strategy cannot mutate a signal after emitting it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import TimeInForce

_ACTIONS = ("long", "short", "close")


@dataclass(frozen=True)
class Signal:
    """A declared trading intent (§8.1).

    Exactly one of ``size_usd`` or ``size`` carries the sizing for open intents
    (``long``/``short``); ``close`` needs neither (it reduce-only-closes the full
    current position). ``limit_price=0`` means a market order.
    """

    market: str
    venue: str
    action: str  # "long" | "short" | "close"
    size_usd: float = 0.0  # OR size (base units) -- exactly one, for open intents
    size: float = 0.0
    limit_price: float = 0.0  # 0 = market order
    stop_loss: float = 0.0  # optional protective stop (resting order)
    take_profit: float = 0.0
    margin_mode: str = "cross"
    tif: TimeInForce = TimeInForce.IOC

    def __post_init__(self) -> None:
        if self.action not in _ACTIONS:
            raise ValueError(
                f"Signal.action must be one of {_ACTIONS}, got {self.action!r}"
            )

    @property
    def is_close(self) -> bool:
        return self.action == "close"

    @classmethod
    def long(
        cls,
        market: str,
        venue: str,
        size_usd: float = 0.0,
        size: float = 0.0,
        **kwargs,
    ) -> "Signal":
        return cls(
            market=market,
            venue=venue,
            action="long",
            size_usd=size_usd,
            size=size,
            **kwargs,
        )

    @classmethod
    def short(
        cls,
        market: str,
        venue: str,
        size_usd: float = 0.0,
        size: float = 0.0,
        **kwargs,
    ) -> "Signal":
        return cls(
            market=market,
            venue=venue,
            action="short",
            size_usd=size_usd,
            size=size,
            **kwargs,
        )

    @classmethod
    def close(cls, market: str, venue: str, **kwargs) -> "Signal":
        """Reduce-only close of the full current position (§8.1 rule 3)."""
        return cls(market=market, venue=venue, action="close", **kwargs)
