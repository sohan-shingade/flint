"""venues.hyperliquid.client — the live execution boundary (D20, §3.6).

The live executor reaches the exchange **only** through this client: it is the
single place a signed order is submitted, resting orders are cancelled, and the
clearinghouse (positions + open orders) is read back for reconciliation. The
signing wallet key is resolved by the executor from the ``SecretsPort`` and
handed to the client's factory — it lives in this trusted server-side object,
never in the browser and never in a log line (the client's ``repr`` deliberately
hides it).

In tests every method is mocked (a fake client implementing :class:`LiveVenueClient`)
— Flint never places a real order in its suite and never requires a real key
(D26). The production HTTP/signing transport is intentionally a thin, lazily
constructed shim: v1 ships the *executor logic* — caps, the persisted order state
machine, reconciliation, the kill switch — proven against a fake, and wiring the
real Hyperliquid SDK transport is a v1.x deferral (see docs/redesign/STATUS.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from flint.core.models import Order, Side


@dataclass(frozen=True)
class ExecReport:
    """The venue's response to a submitted order (§6.2 fill semantics).

    ``status`` is one of ``filled`` / ``partial`` / ``resting`` / ``rejected``.
    ``filled_size`` and ``avg_price`` describe what actually executed (0 for a
    pure ``resting`` maker or a ``rejected`` order); ``fee`` is the taker/maker
    fee the venue charged on the executed portion.
    """

    client_order_id: str
    market: str
    side: Side
    status: str
    filled_size: float = 0.0
    avg_price: float = 0.0
    fee: float = 0.0
    liquidity: str = "taker"  # "maker" | "taker"
    ts: int = 0
    reason: str = ""  # why it was rejected (attribution)


@dataclass(frozen=True)
class VenueOrder:
    """One resting order as the venue reports it (reconciliation input)."""

    client_order_id: str
    market: str
    side: Side
    size: float
    price: float


@dataclass(frozen=True)
class VenuePosition:
    """One open position as the venue reports it (reconciliation input)."""

    market: str
    side: Side
    size: float
    entry_price: float

    @property
    def signed_size(self) -> float:
        return self.side.sign * self.size


@dataclass(frozen=True)
class ClearinghouseState:
    """A snapshot of the account on the venue — the truth to reconcile against."""

    positions: tuple[VenuePosition, ...] = ()
    open_orders: tuple[VenueOrder, ...] = ()


@runtime_checkable
class LiveVenueClient(Protocol):
    """The narrow seam the live executor uses to reach the exchange (§3.6).

    Every method is a single venue round-trip. Implementations own the signing
    key and the transport; the executor never sees either. A network/venue fault
    is raised as :class:`LiveVenueUnavailable` so the executor can surface it as a
    structured ``venue_unavailable`` rather than a stack trace (§19.1).
    """

    def place_order(self, order: Order) -> ExecReport:
        """Sign and submit ``order``; return what the venue did with it."""
        ...

    def cancel_all(self, market: str | None = None) -> list[str]:
        """Cancel all resting orders (optionally one market); return their ids."""
        ...

    def clearinghouse_state(self) -> ClearinghouseState:
        """Read back the account's positions + open orders for reconciliation."""
        ...


class LiveVenueUnavailable(Exception):
    """A venue/network fault (WS drop, REST 5xx, rate limit) — retryable (§19.1)."""


@dataclass
class HyperliquidLiveClient:
    """The production HL client — holds the signing key, owns the transport.

    The key is stored under a private, ``repr``-hidden field so it never leaks
    into a log line or a traceback. The actual signing + HTTP round-trips are the
    v1.x deferral: this shim is structurally complete (the executor is written and
    tested entirely against the :class:`LiveVenueClient` seam it satisfies), but
    each call raises :class:`LiveVenueUnavailable` with an actionable message until
    the real Hyperliquid transport is wired (docs/redesign/STATUS.md). This keeps
    v1 honest — Flint never *pretends* to have placed an order it did not place.
    """

    base_url: str = "https://api.hyperliquid.xyz"
    _signing_key: str = field(default="", repr=False)

    _UNWIRED = (
        "the real Hyperliquid signing/transport is not wired in this build "
        "(v1.x deferral); the live executor logic is complete and tested against "
        "a mocked venue client. See docs/redesign/STATUS.md."
    )

    def place_order(self, order: Order) -> ExecReport:
        raise LiveVenueUnavailable(self._UNWIRED)

    def cancel_all(self, market: str | None = None) -> list[str]:
        raise LiveVenueUnavailable(self._UNWIRED)

    def clearinghouse_state(self) -> ClearinghouseState:
        raise LiveVenueUnavailable(self._UNWIRED)


def hyperliquid_client_factory(signing_key: str) -> HyperliquidLiveClient:
    """Build a key-bound production client — the executor's default factory.

    The executor resolves the key from the ``SecretsPort`` and calls this; tests
    inject a fake factory returning a mocked :class:`LiveVenueClient` instead.
    """
    return HyperliquidLiveClient(_signing_key=signing_key)
