"""The HL live executor — caps, order state machine, reconciliation, kill switch (D20, §3.6).

Every venue call is mocked (:class:`FakeLiveClient`): the suite never places a real
order and never needs a real key (D26). Fixtures are hand-authored — tiny orders,
fixed prices, unit reports — no generated market-like data. Secrets are resolved
only through a fake :class:`SecretsPort`, and the executor's key never leaves the
client the factory builds.
"""

from __future__ import annotations


import pytest

from flint.adapters import InMemoryUserData
from flint.core.models import Order, OrderType, Side, TimeInForce
from flint.engine.portfolio import (
    FILL,
    ORDER_CANCELLED,
    ORDER_PLACED,
    ORDER_REJECTED,
    EventLog,
    fold,
)
from flint.live import (
    LiveCaps,
    LiveExecutor,
    LiveReject,
    LiveStartRefused,
    stop_all_live,
)
from flint.ports import SecretsPort, TenantContext
from flint.venues.hyperliquid import (
    ClearinghouseState,
    ExecReport,
    HyperliquidLiveClient,
    LiveVenueUnavailable,
    VenueOrder,
    VenuePosition,
)

ALICE = TenantContext(tenant_id="alice")
BOB = TenantContext(tenant_id="bob")
VENUE = "hyperliquid"
MARKET = "SOL-PERP"


class FakeSecrets(SecretsPort):
    """A SecretsPort that hands back one fixed key (or ``None`` for the missing case)."""

    def __init__(self, key: str | None = "0xTESTKEY") -> None:
        self.key = key

    def get_secret(self, tenant: TenantContext, name: str) -> str | None:
        return self.key


class FakeLiveClient:
    """A mocked venue: records what it was asked, returns unit ExecReports."""

    def __init__(self) -> None:
        self.placed: list[Order] = []
        self.cancelled: list[str | None] = []
        self.market_price = 100.0
        self.state = ClearinghouseState()
        # A test can override the reply for the next place with on_place(order).
        self.on_place = None

    def place_order(self, order: Order) -> ExecReport:
        self.placed.append(order)
        if self.on_place is not None:
            return self.on_place(order)
        price = order.price if order.price > 0 else self.market_price
        fee = abs(order.size) * price * 0.00045
        return ExecReport(
            client_order_id=order.client_order_id,
            market=order.market,
            side=order.side,
            status="filled",
            filled_size=order.size,
            avg_price=price,
            fee=fee,
            liquidity="taker",
            ts=1,
        )

    def cancel_all(self, market: str | None = None) -> list[str]:
        self.cancelled.append(market)
        return ["venue-oid-1", "venue-oid-2"]

    def clearinghouse_state(self) -> ClearinghouseState:
        return self.state


class CapturingFactory:
    """A client factory that records the key it was handed (to prove the seam)."""

    def __init__(self, client: FakeLiveClient) -> None:
        self.client = client
        self.key_seen: str | None = None

    def __call__(self, signing_key: str) -> FakeLiveClient:
        self.key_seen = signing_key
        return self.client


def _order(side: Side = Side.LONG, size: float = 1.0, **kw) -> Order:
    return Order(market=MARKET, venue=VENUE, side=side, type=OrderType.MARKET, size=size, **kw)


def _start(
    *,
    caps: LiveCaps,
    store: InMemoryUserData | None = None,
    client: FakeLiveClient | None = None,
    secrets: FakeSecrets | None = None,
    run_id: str = "live-1",
    initial_capital: str = "100000",
) -> tuple[LiveExecutor, FakeLiveClient, InMemoryUserData]:
    store = store or InMemoryUserData()
    client = client or FakeLiveClient()
    secrets = secrets or FakeSecrets()
    ex = LiveExecutor.start(
        tenant=ALICE,
        store=store,
        secrets=secrets,
        run_id=run_id,
        market=MARKET,
        caps=caps,
        client_factory=lambda key: client,
        initial_capital=initial_capital,
    )
    return ex, client, store


# -- pre-flight D20 refusals -------------------------------------------------


def test_start_refuses_without_position_cap():
    with pytest.raises(LiveStartRefused) as exc:
        LiveExecutor.start(
            tenant=ALICE,
            store=InMemoryUserData(),
            secrets=FakeSecrets(),
            run_id="x",
            market=MARKET,
            caps=LiveCaps(max_position_usd=0.0),
            client_factory=lambda key: FakeLiveClient(),
        )
    assert exc.value.code == "missing_position_cap"
    assert exc.value.to_payload()["refused"]["code"] == "missing_position_cap"


def test_start_refuses_without_venue_key():
    with pytest.raises(LiveStartRefused) as exc:
        LiveExecutor.start(
            tenant=ALICE,
            store=InMemoryUserData(),
            secrets=FakeSecrets(key=None),
            run_id="x",
            market=MARKET,
            caps=LiveCaps(max_position_usd=1000.0),
            client_factory=lambda key: FakeLiveClient(),
        )
    assert exc.value.code == "missing_venue_key"


def test_start_persists_a_running_live_head():
    _, _, store = _start(caps=LiveCaps(max_position_usd=5000.0, max_daily_loss_usd=200.0))
    rec = store.load_run(ALICE, "live-1")
    assert rec.kind == "live"
    assert rec.status == "running"
    assert rec.summary["market"] == MARKET
    assert rec.summary["max_position_usd"] == 5000.0
    assert rec.summary["max_daily_loss_usd"] == 200.0


def test_key_resolved_only_through_secrets_port_and_never_logged():
    client = FakeLiveClient()
    factory = CapturingFactory(client)
    store = InMemoryUserData()
    LiveExecutor.start(
        tenant=ALICE,
        store=store,
        secrets=FakeSecrets(key="0xSUPERSECRET"),
        run_id="live-1",
        market=MARKET,
        caps=LiveCaps(max_position_usd=5000.0),
        client_factory=factory,
    )
    # The factory got the key from the SecretsPort...
    assert factory.key_seen == "0xSUPERSECRET"
    # ...and the key appears nowhere in the persisted run head / event log.
    rec = store.load_run(ALICE, "live-1")
    assert "0xSUPERSECRET" not in str(rec.summary)
    assert all("0xSUPERSECRET" not in str(r) for r in store.load_events(ALICE, "live-1"))


# -- order path + event log --------------------------------------------------


def test_market_order_fills_and_records_placed_then_fill():
    ex, client, store = _start(caps=LiveCaps(max_position_usd=100000.0))
    client.market_price = 100.0
    report = ex.submit(_order(size=10.0), ref_price=100.0)
    assert isinstance(report, ExecReport) and report.status == "filled"
    kinds = [r["kind"] for r in store.load_events(ALICE, "live-1")]
    assert kinds == [ORDER_PLACED, FILL]
    pos = ex.state.position(VENUE, MARKET)
    assert pos is not None and pos.side is Side.LONG and pos.size == 10.0
    # A live FILL is tagged so the audit trail shows it was a real execution.
    fill = next(r for r in store.load_events(ALICE, "live-1") if r["kind"] == FILL)
    assert fill["payload"]["fidelity_tier"] == "live"


def test_folded_live_book_matches_executor_state():
    ex, _, store = _start(caps=LiveCaps(max_position_usd=100000.0))
    ex.submit(_order(size=10.0), ref_price=100.0)
    book = fold(EventLog(store, ALICE, "live-1").read())
    assert set(book.positions) == set(ex.state.positions)
    folded = book.positions[(VENUE, MARKET)]
    live = ex.state.positions[(VENUE, MARKET)]
    assert folded.size == live.size and folded.side == live.side


# -- caps --------------------------------------------------------------------


def test_position_cap_rejects_before_reaching_the_venue():
    ex, client, store = _start(caps=LiveCaps(max_position_usd=500.0))
    out = ex.submit(_order(size=10.0), ref_price=100.0)  # 10 * 100 = 1000 > 500
    assert isinstance(out, LiveReject) and out.code == "position_cap"
    assert client.placed == []  # never submitted
    assert store.load_events(ALICE, "live-1") == []  # no lifecycle written


def test_reduce_only_close_bypasses_the_position_cap():
    ex, client, _ = _start(caps=LiveCaps(max_position_usd=1500.0))
    ex.submit(_order(size=10.0), ref_price=100.0)  # long 10 @ 100 (1000 <= 1500)
    # A reduce-only close is allowed even though the cap is tight.
    out = ex.submit(
        _order(side=Side.SHORT, size=10.0, reduce_only=True), ref_price=100.0
    )
    assert isinstance(out, ExecReport)
    assert ex.state.position(VENUE, MARKET) is None  # flat


def test_partial_ioc_market_remainder_is_cancelled():
    ex, client, store = _start(caps=LiveCaps(max_position_usd=100000.0))
    client.on_place = lambda o: ExecReport(
        client_order_id=o.client_order_id,
        market=o.market,
        side=o.side,
        status="partial",
        filled_size=4.0,
        avg_price=100.0,
        fee=0.18,
        ts=1,
    )
    ex.submit(_order(size=10.0, tif=TimeInForce.IOC), ref_price=100.0)
    kinds = [r["kind"] for r in store.load_events(ALICE, "live-1")]
    assert kinds == [ORDER_PLACED, FILL, ORDER_CANCELLED]
    assert store.load_events(ALICE, "live-1")[-1]["payload"]["reason"] == "ioc_remainder"


def test_venue_rejection_is_structured_data_not_a_fill():
    ex, client, store = _start(caps=LiveCaps(max_position_usd=100000.0))
    client.on_place = lambda o: ExecReport(
        client_order_id=o.client_order_id,
        market=o.market,
        side=o.side,
        status="rejected",
        reason="post_only_would_cross",
    )
    ex.submit(_order(size=1.0), ref_price=100.0)
    kinds = [r["kind"] for r in store.load_events(ALICE, "live-1")]
    assert kinds == [ORDER_PLACED, ORDER_REJECTED]
    assert ex.state.position(VENUE, MARKET) is None


# -- daily-loss halt ---------------------------------------------------------


def test_daily_loss_halt_flattens_and_blocks_new_orders():
    ex, client, store = _start(
        caps=LiveCaps(max_position_usd=100000.0, max_daily_loss_usd=50.0)
    )
    ex.submit(_order(size=10.0), ref_price=100.0)  # long 10 @ 100
    # Mark drops: unrealized = (94 - 100) * 10 = -60, loss 60 >= 50 -> halt.
    reject = ex.mark_to_market({MARKET: 94.0})
    assert isinstance(reject, LiveReject) and reject.code == "daily_loss_halt"
    assert ex.halted is True
    # The halt flattened the position with a reduce-only close...
    assert ex.state.position(VENUE, MARKET) is None
    assert client.cancelled  # resting orders cancelled
    assert store.load_run(ALICE, "live-1").status == "stopped"
    # ...and further orders are refused.
    out = ex.submit(_order(size=1.0), ref_price=94.0)
    assert isinstance(out, LiveReject) and out.code == "halted"


# -- kill switch -------------------------------------------------------------


def test_kill_switch_cancels_and_optionally_flattens():
    ex, client, store = _start(caps=LiveCaps(max_position_usd=100000.0))
    ex.submit(_order(size=10.0), ref_price=100.0)
    report = ex.stop(flatten=True)
    assert client.cancelled == [MARKET]
    assert report.flattened_markets == (MARKET,)
    assert ex.state.position(VENUE, MARKET) is None
    assert store.load_run(ALICE, "live-1").status == "stopped"


def test_kill_switch_without_flatten_leaves_position_but_halts():
    ex, client, _ = _start(caps=LiveCaps(max_position_usd=100000.0))
    ex.submit(_order(size=10.0), ref_price=100.0)
    ex.stop(flatten=False)
    assert ex.state.position(VENUE, MARKET) is not None  # not flattened
    assert ex.halted is True
    out = ex.submit(_order(size=1.0), ref_price=100.0)
    assert isinstance(out, LiveReject) and out.code == "halted"


def test_stop_all_live_stops_every_running_live_run():
    store = InMemoryUserData()
    secrets = FakeSecrets()
    c1, c2 = FakeLiveClient(), FakeLiveClient()
    clients = iter([c1, c2])
    factory = lambda key: next(clients)  # noqa: E731
    for rid in ("live-a", "live-b"):
        LiveExecutor.start(
            tenant=ALICE,
            store=store,
            secrets=secrets,
            run_id=rid,
            market=MARKET,
            caps=LiveCaps(max_position_usd=1000.0),
            client_factory=factory,
        )
    reports = stop_all_live(
        ALICE, store=store, secrets=secrets, client_factory=lambda key: FakeLiveClient()
    )
    assert len(reports) == 2
    assert store.load_run(ALICE, "live-a").status == "stopped"
    assert store.load_run(ALICE, "live-b").status == "stopped"


# -- reconciliation ----------------------------------------------------------


def test_reconcile_surfaces_position_mismatch_without_adopting():
    ex, client, _ = _start(caps=LiveCaps(max_position_usd=100000.0))
    ex.submit(_order(size=10.0), ref_price=100.0)  # local: long 10
    client.state = ClearinghouseState(
        positions=(VenuePosition(market=MARKET, side=Side.LONG, size=7.0, entry_price=100.0),)
    )
    alerts = ex.reconcile()
    assert len(alerts) == 1
    a = alerts[0]
    assert a.kind == "position_mismatch" and a.market == MARKET
    assert "10" in a.local and "7" in a.venue
    # Never silently adopted: local state is untouched.
    assert ex.state.position(VENUE, MARKET).size == 10.0


def test_reconcile_surfaces_order_mismatch_for_venue_only_order():
    ex, client, _ = _start(caps=LiveCaps(max_position_usd=100000.0))
    client.state = ClearinghouseState(
        open_orders=(
            VenueOrder(client_order_id="ghost", market=MARKET, side=Side.LONG, size=3.0, price=90.0),
        )
    )
    alerts = ex.reconcile()
    assert [a.kind for a in alerts] == ["order_mismatch"]
    assert alerts[0].venue.startswith("ghost")


def test_reconcile_clean_when_state_agrees():
    ex, client, _ = _start(caps=LiveCaps(max_position_usd=100000.0))
    ex.submit(_order(size=10.0), ref_price=100.0)
    client.state = ClearinghouseState(
        positions=(VenuePosition(market=MARKET, side=Side.LONG, size=10.0, entry_price=100.0),)
    )
    assert ex.reconcile() == []


# -- restart safety ----------------------------------------------------------


def test_resume_folds_persisted_state_without_double_counting():
    store = InMemoryUserData()
    secrets = FakeSecrets()
    client = FakeLiveClient()
    ex, _, _ = _start(caps=LiveCaps(max_position_usd=100000.0), store=store, client=client, secrets=secrets)
    ex.submit(_order(size=10.0), ref_price=100.0)
    cash_before = ex.state.account(VENUE).cash
    # A fresh executor reattaches and folds the same log — same position, same cash.
    ex2 = LiveExecutor.resume(
        tenant=ALICE, store=store, secrets=secrets, run_id="live-1",
        client_factory=lambda key: client,
    )
    assert ex2.state.position(VENUE, MARKET).size == 10.0
    assert ex2.state.account(VENUE).cash == cash_before


def test_resume_reads_caps_back_from_the_run_head():
    store = InMemoryUserData()
    secrets = FakeSecrets()
    _start(
        caps=LiveCaps(max_position_usd=750.0, max_daily_loss_usd=None),
        store=store, secrets=secrets,
    )
    ex2 = LiveExecutor.resume(
        tenant=ALICE, store=store, secrets=secrets, run_id="live-1",
        client_factory=lambda key: FakeLiveClient(),
    )
    # The reattached executor enforces the same position cap it started under.
    out = ex2.submit(_order(size=100.0), ref_price=100.0)  # 10000 > 750
    assert isinstance(out, LiveReject) and out.code == "position_cap"


# -- the production client is honest about being unwired ---------------------


def test_production_client_raises_until_transport_is_wired():
    client = HyperliquidLiveClient(_signing_key="0xKEY")
    with pytest.raises(LiveVenueUnavailable):
        client.place_order(_order())
    with pytest.raises(LiveVenueUnavailable):
        client.cancel_all()
    with pytest.raises(LiveVenueUnavailable):
        client.clearinghouse_state()
    assert "0xKEY" not in repr(client)  # the key never surfaces in a repr
