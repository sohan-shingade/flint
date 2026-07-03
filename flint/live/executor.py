"""The HL live executor — same order state machine as paper, real fills (D20, §3.6).

Live trading is *not* a second engine. It reuses the pieces paper trades on: the
persisted order state machine (:class:`~flint.engine.orders.OrderRecord`, §6.2),
the append-only event log (folded back for restart-safety), and the same
``apply_fill_delta`` position reducer the backtest uses — so a folded live book
is field-for-field the same shape as a folded backtest book. The one difference
is the *source of fills*: a backtest simulates them with a fill model, live gets
them from the exchange through the :class:`~flint.venues.hyperliquid.client.LiveVenueClient`
boundary. That boundary signs and submits with a wallet key resolved from the
``SecretsPort`` — the key lives only in the client, never in the browser or a log.

Three safety rails wrap every order (D20):

* **Position cap (required).** A live run refuses to start without a
  ``max_position_usd`` cap; any non-reduce-only order that would push the market's
  notional past it is rejected *before* it reaches the venue — structured data,
  never a placed order.
* **Daily-loss halt (optional).** When session loss crosses ``max_daily_loss_usd``
  the executor halts: it cancels resting orders, flattens open positions, and
  refuses new orders until restarted.
* **Kill switch.** :meth:`stop` (and the module-level :func:`stop_all_live` behind
  ``flint live --stop --all``) cancels all resting orders and optionally flattens —
  the operator's and the UI's single button to pull the plug.

On reconnect :meth:`reconcile` compares the local folded state against the venue's
clearinghouse and surfaces every mismatch as a structured :class:`DriftAlert`. It
never silently adopts either side — a divergence is a fact to show, not to paper
over.

All venue calls go through the injected client, so the whole executor is exercised
against a mocked venue in tests (D26): Flint never places a real order or needs a
real key to prove this code correct.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from flint.core.models import Order, OrderType, TimeInForce
from flint.engine.money import Money, money
from flint.engine.orders import OrderRecord, OrderStatus
from flint.engine.portfolio import (
    FILL,
    ORDER_CANCELLED,
    ORDER_PLACED,
    ORDER_REJECTED,
    EventLog,
    apply_fill_delta,
    fold,
)
from flint.engine.state import PortfolioState
from flint.ports import SecretsPort, TenantContext, UserDataPort
from flint.ports.records import RunRecord
from flint.venues import HYPERLIQUID, VenueSpec
from flint.venues.hyperliquid.client import (
    ClearinghouseState,
    ExecReport,
    LiveVenueClient,
    hyperliquid_client_factory,
)

# Positions closer than this many base units are treated as equal (float lot math).
_SIZE_EPS = 1e-9
# The secret name a live run resolves from the SecretsPort for HL signing.
DEFAULT_KEY_NAME = "hyperliquid"

ClientFactory = Callable[[str], LiveVenueClient]


class LiveStartRefused(Exception):
    """A D20 pre-flight refusal — caps missing or no venue key. The run never starts.

    Carries a structured ``code``/``message``/``hint`` so a surface (CLI exit code,
    API body) renders it as data, never a stack trace (§19.1).
    """

    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_payload(self) -> dict[str, object]:
        return {
            "refused": {"code": self.code, "message": self.message, "hint": self.hint}
        }


@dataclass(frozen=True)
class LiveCaps:
    """The D20 risk caps a live run is fenced by.

    ``max_position_usd`` is **required** (the run refuses without it);
    ``max_daily_loss_usd`` is an optional protective halt.
    """

    max_position_usd: float
    max_daily_loss_usd: float | None = None


@dataclass(frozen=True)
class LiveReject:
    """A pre-trade refusal returned to the caller — data, not an exception (§19.1).

    A capped or halted order is *not* an error: the executor declines it and hands
    back this structured payload. No order reaches the venue and nothing is
    written to the portfolio log (a refusal is control-plane, not order lifecycle).
    """

    code: str  # "position_cap" | "daily_loss_halt" | "halted" | "venue_rejected"
    message: str
    market: str = ""
    detail: str = ""
    hint: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "rejected": {
                "code": self.code,
                "message": self.message,
                "market": self.market,
                "detail": self.detail,
                "hint": self.hint,
            }
        }


@dataclass(frozen=True)
class DriftAlert:
    """One reconciliation mismatch between local and venue state (never adopted)."""

    kind: str  # "position_mismatch" | "order_mismatch"
    market: str
    local: str  # human summary of what the folded local log holds
    venue: str  # human summary of what the clearinghouse reports
    hint: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "market": self.market,
            "local": self.local,
            "venue": self.venue,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class StopReport:
    """The result of a kill-switch stop — what was cancelled and flattened."""

    run_id: str
    cancelled_orders: tuple[str, ...]
    flattened_markets: tuple[str, ...]
    reason: str

    def to_payload(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "cancelled_orders": list(self.cancelled_orders),
            "flattened_markets": list(self.flattened_markets),
            "reason": self.reason,
        }


class LiveExecutor:
    """Submits real orders under the D20 caps, on the paper/backtest order machine."""

    def __init__(
        self,
        *,
        tenant: TenantContext,
        store: UserDataPort,
        run_id: str,
        client: LiveVenueClient,
        caps: LiveCaps,
        market: str,
        venue: str = HYPERLIQUID.name,
        venue_spec: VenueSpec = HYPERLIQUID,
        initial_capital: Money | str | float = "100000",
    ) -> None:
        self._tenant = tenant
        self._store = store
        self._run_id = run_id
        self._client = client
        self._caps = caps
        self._market = market
        self._venue = venue
        self._spec = venue_spec
        self._initial = money(initial_capital)
        self._log = EventLog(store, tenant, run_id)
        self._coid = 0
        self._halted = False
        # Latest known mark per market — updated by submit() ref prices and
        # mark_to_market(); drives the daily-loss equity estimate.
        self._marks: dict[str, float] = {}
        # Restart-safety: fold whatever the log already holds into live state and
        # the order machine, so a reconnect never double-counts a persisted fill.
        self._orders: dict[str, OrderRecord] = {}
        self.state = self._reconstruct()

    # -- construction ---------------------------------------------------------

    @classmethod
    def start(
        cls,
        *,
        tenant: TenantContext,
        store: UserDataPort,
        secrets: SecretsPort,
        run_id: str,
        market: str,
        caps: LiveCaps,
        client_factory: ClientFactory = hyperliquid_client_factory,
        key_name: str = DEFAULT_KEY_NAME,
        venue: str = HYPERLIQUID.name,
        venue_spec: VenueSpec = HYPERLIQUID,
        initial_capital: Money | str | float = "100000",
    ) -> "LiveExecutor":
        """Pre-flight the D20 contract, resolve the key, persist the head, start.

        Refuses (``LiveStartRefused``, never a placed order) if the position cap is
        missing/non-positive or the tenant has no venue key. The key is read from
        the ``SecretsPort`` and handed to ``client_factory`` — it never returns to
        the caller and is never logged.
        """
        if caps.max_position_usd is None or caps.max_position_usd <= 0:
            raise LiveStartRefused(
                "missing_position_cap",
                "a live run requires a positive --max-position-usd (D20 safety cap)",
                hint="pass --max-position-usd to bound the largest position notional",
            )
        secret = secrets.get_secret(tenant, key_name)
        if not secret:
            raise LiveStartRefused(
                "missing_venue_key",
                f"no {venue} signing key is configured for this tenant",
                hint=f"set the {key_name} secret (server-side .env / vault) before going live",
            )
        client = client_factory(secret)
        cap = money(initial_capital)
        store.save_run(
            tenant,
            RunRecord(
                run_id=run_id,
                kind="live",
                status="running",
                summary={
                    "initial_capital": str(cap),
                    "market": market,
                    "venue": venue,
                    "max_position_usd": caps.max_position_usd,
                    "max_daily_loss_usd": caps.max_daily_loss_usd,
                },
            ),
        )
        return cls(
            tenant=tenant,
            store=store,
            run_id=run_id,
            client=client,
            caps=caps,
            market=market,
            venue=venue,
            venue_spec=venue_spec,
            initial_capital=cap,
        )

    @classmethod
    def resume(
        cls,
        *,
        tenant: TenantContext,
        store: UserDataPort,
        secrets: SecretsPort,
        run_id: str,
        client_factory: ClientFactory = hyperliquid_client_factory,
        key_name: str = DEFAULT_KEY_NAME,
        venue_spec: VenueSpec = HYPERLIQUID,
    ) -> "LiveExecutor":
        """Reattach to a persisted live run (kill switch / reconnect entry).

        Reads the caps, market, and initial capital back from the run head; the
        event log is folded on construction, so the reattached executor resumes on
        exactly the position and order state the last connection left (§6.7).
        """
        record = store.load_run(tenant, run_id)  # KeyError if absent / other tenant
        summary = dict(record.summary)
        secret = secrets.get_secret(tenant, key_name)
        if not secret:
            raise LiveStartRefused(
                "missing_venue_key",
                "no signing key is configured for this tenant",
                hint=f"set the {key_name} secret before reconnecting the live run",
            )
        caps = LiveCaps(
            max_position_usd=float(summary.get("max_position_usd") or 0.0),
            max_daily_loss_usd=(
                float(summary["max_daily_loss_usd"])
                if summary.get("max_daily_loss_usd") is not None
                else None
            ),
        )
        return cls(
            tenant=tenant,
            store=store,
            run_id=run_id,
            client=client_factory(secret),
            caps=caps,
            market=str(summary.get("market", "")),
            venue=str(summary.get("venue", HYPERLIQUID.name)),
            venue_spec=venue_spec,
            initial_capital=summary.get("initial_capital", "100000"),
        )

    # -- submitting orders ----------------------------------------------------

    def submit(self, order: Order, *, ref_price: float) -> ExecReport | LiveReject:
        """Gate ``order`` on the D20 caps, then sign+submit it and record the fill.

        ``ref_price`` is the caller's best current price estimate for the order's
        market (a limit order defaults to its own price) — it prices the position
        cap and the daily-loss equity check. A capped or halted order returns a
        :class:`LiveReject` and never reaches the venue; a venue rejection is
        likewise surfaced as data, not raised.
        """
        if not order.client_order_id:
            order.client_order_id = self._next_coid()
        self._marks[order.market] = ref_price

        if self._halted:
            return LiveReject(
                "halted",
                "the live executor is halted (daily-loss limit or kill switch)",
                market=order.market,
                hint="restart the live run to resume trading",
            )
        if self._caps.max_daily_loss_usd is not None and (
            self._session_loss() >= self._caps.max_daily_loss_usd
        ):
            self._trip_daily_loss_halt()
            return LiveReject(
                "daily_loss_halt",
                "session loss reached the daily-loss limit; new orders are blocked",
                market=order.market,
                detail=f"loss ${self._session_loss():.2f} >= "
                f"${self._caps.max_daily_loss_usd:.2f}",
                hint="positions were flattened and resting orders cancelled",
            )
        if not order.reduce_only:
            projected = self._projected_notional(order, ref_price)
            if projected > self._caps.max_position_usd:
                return LiveReject(
                    "position_cap",
                    "order would breach the max position notional",
                    market=order.market,
                    detail=f"projected ${projected:.2f} > cap "
                    f"${self._caps.max_position_usd:.2f}",
                    hint="reduce the order size or raise --max-position-usd",
                )

        report = self._client.place_order(order)
        self._record(order, report)
        return report

    def mark_to_market(self, marks: dict[str, float]) -> LiveReject | None:
        """Feed fresh marks; trip the daily-loss halt if session loss crosses the cap.

        The live loop calls this each bar with the latest marks so an *unrealized*
        drawdown (not just realized losses on fills) can trip the halt. Returns a
        structured :class:`LiveReject` when the halt fires, else ``None``.
        """
        self._marks.update(marks)
        if (
            not self._halted
            and self._caps.max_daily_loss_usd is not None
            and self._session_loss() >= self._caps.max_daily_loss_usd
        ):
            self._trip_daily_loss_halt()
            return LiveReject(
                "daily_loss_halt",
                "session loss reached the daily-loss limit; the run is halted",
                detail=f"loss ${self._session_loss():.2f} >= "
                f"${self._caps.max_daily_loss_usd:.2f}",
                hint="positions were flattened and resting orders cancelled",
            )
        return None

    # -- reconciliation -------------------------------------------------------

    def reconcile(self) -> list[DriftAlert]:
        """Compare the folded local state to the venue clearinghouse (§3.6).

        Reads positions + open orders from the venue and diffs them against the
        book folded from our own event log. Every mismatch becomes a
        :class:`DriftAlert`; neither side is silently adopted — a divergence after
        a dropped connection is surfaced for the operator to resolve.
        """
        venue_state: ClearinghouseState = self._client.clearinghouse_state()
        book = fold(self._log.read())
        alerts: list[DriftAlert] = []

        local_pos = {
            market: pos.signed_size
            for (v, market), pos in book.positions.items()
            if v == self._venue
        }
        venue_pos = {p.market: p.signed_size for p in venue_state.positions}
        for market in sorted(set(local_pos) | set(venue_pos)):
            lsz = local_pos.get(market, 0.0)
            vsz = venue_pos.get(market, 0.0)
            if abs(lsz - vsz) > _SIZE_EPS:
                alerts.append(
                    DriftAlert(
                        kind="position_mismatch",
                        market=market,
                        local=f"signed_size={lsz:g}",
                        venue=f"signed_size={vsz:g}",
                        hint="a fill may have been missed across a disconnect; "
                        "reconcile before resuming",
                    )
                )

        local_open = {
            coid: rec
            for coid, rec in book.orders.items()
            if not rec.is_terminal and rec.venue == self._venue
        }
        venue_open = {o.client_order_id: o for o in venue_state.open_orders}
        for coid in sorted(set(local_open) | set(venue_open)):
            if coid not in venue_open:
                rec = local_open[coid]
                alerts.append(
                    DriftAlert(
                        kind="order_mismatch",
                        market=rec.market,
                        local=f"{coid} working ({rec.status})",
                        venue="absent",
                        hint="local thinks this order rests but the venue does not",
                    )
                )
            elif coid not in local_open:
                o = venue_open[coid]
                alerts.append(
                    DriftAlert(
                        kind="order_mismatch",
                        market=o.market,
                        local="absent/terminal",
                        venue=f"{coid} resting size={o.size:g}",
                        hint="the venue rests an order the local log does not track",
                    )
                )
        return alerts

    # -- kill switch ----------------------------------------------------------

    def stop(self, *, flatten: bool = False, reason: str = "operator") -> StopReport:
        """Pull the plug: cancel resting orders, optionally flatten, halt the run.

        Halts first (no new order can slip through even if the venue cancel round
        trip fails), then cancels every resting order on the venue and reflects the
        cancellations in the local order machine. With ``flatten`` it closes each
        open position with a reduce-only market order. The run head is marked
        ``stopped``. This is the single mechanism behind the CLI ``--stop`` and the
        UI-callable kill switch.
        """
        self._halted = True
        cancelled = tuple(self._client.cancel_all(self._market))
        for rec in list(self._orders.values()):
            if not rec.is_terminal and rec.venue == self._venue:
                rec.transition(OrderStatus.CANCELLED, reason="kill_switch")
                self._log.emit(
                    ORDER_CANCELLED,
                    {
                        "client_order_id": rec.client_order_id,
                        "market": rec.market,
                        "venue": rec.venue,
                        "reason": "kill_switch",
                    },
                )
        flattened: list[str] = []
        if flatten:
            for (venue, market), pos in list(self.state.positions.items()):
                if venue != self._venue:
                    continue
                close = Order(
                    market=market,
                    venue=venue,
                    side=pos.side.opposite,
                    type=OrderType.MARKET,
                    size=pos.size,
                    tif=TimeInForce.IOC,
                    reduce_only=True,
                    margin_mode=pos.margin_mode,
                    client_order_id=self._next_coid(),
                )
                report = self._client.place_order(close)
                self._record(close, report)
                flattened.append(market)
        self._persist_status("stopped")
        return StopReport(
            run_id=self._run_id,
            cancelled_orders=cancelled,
            flattened_markets=tuple(flattened),
            reason=reason,
        )

    @property
    def halted(self) -> bool:
        return self._halted

    # -- internals ------------------------------------------------------------

    def _trip_daily_loss_halt(self) -> None:
        """Protective stop: flatten and cancel on a daily-loss breach (D20)."""
        if self._halted:
            return
        self.stop(flatten=True, reason="daily_loss_limit")

    def _record(self, order: Order, report: ExecReport) -> None:
        """Persist an order's venue outcome onto the shared event log (§6.2).

        Emits ORDER_PLACED (the venue accepted it), then a FILL for the executed
        portion via the *same* ``apply_fill_delta`` reducer the backtest uses — so
        a folded live book matches a folded backtest book. A venue rejection
        terminates the order; an IOC remainder after a partial is cancelled.
        """
        rec = OrderRecord(
            client_order_id=order.client_order_id,
            market=order.market,
            venue=order.venue or self._venue,
            side=order.side,
            type=order.type,
            size=order.size,
            price=order.price,
        )
        self._orders[order.client_order_id] = rec
        rec.transition(OrderStatus.PLACED)
        self._log.emit(
            ORDER_PLACED,
            {
                "client_order_id": order.client_order_id,
                "market": order.market,
                "venue": rec.venue,
                "side": order.side,
                "type": order.type,
                "size": order.size,
                "price": order.price,
                "tif": order.tif,
                "reduce_only": order.reduce_only,
            },
        )
        if report.status == "rejected":
            rec.transition(
                OrderStatus.REJECTED, reason=report.reason or "venue_rejected"
            )
            self._log.emit(
                ORDER_REJECTED,
                {
                    "client_order_id": order.client_order_id,
                    "market": order.market,
                    "venue": rec.venue,
                    "reason": report.reason or "venue_rejected",
                },
            )
            return
        if report.filled_size > _SIZE_EPS:
            self._apply_fill(order, report, rec)
        # A market/IOC order whose remainder did not fill is cancelled to terminal.
        if rec.status is OrderStatus.PARTIAL and order.tif is TimeInForce.IOC:
            rec.transition(OrderStatus.CANCELLED, reason="ioc_remainder")
            self._log.emit(
                ORDER_CANCELLED,
                {
                    "client_order_id": order.client_order_id,
                    "market": order.market,
                    "venue": rec.venue,
                    "reason": "ioc_remainder",
                },
            )

    def _apply_fill(self, order: Order, report: ExecReport, rec: OrderRecord) -> None:
        """Move cash + the position book on a venue fill and emit FILL (§6.1)."""
        venue = order.venue or self._venue
        acct = self.state.account(venue)
        acct.cash -= money(report.fee)
        acct.fees_paid += money(report.fee)
        realized = apply_fill_delta(
            self.state.positions,
            (venue, order.market),
            side=order.side,
            size=report.filled_size,
            price=report.avg_price,
            margin_mode=order.margin_mode,
        )
        if realized:
            acct.credit(money(realized))
            acct.realized_pnl += money(realized)
        rec.apply_fill(report.filled_size)
        self._log.emit(
            FILL,
            {
                "client_order_id": order.client_order_id,
                "market": order.market,
                "venue": venue,
                "side": order.side,
                "price": report.avg_price,
                "size": report.filled_size,
                "fee": report.fee,
                "liquidity": report.liquidity,
                "fidelity_tier": "live",
                "slippage_bps": 0.0,
                "is_partial": report.status == "partial",
                "margin_mode": order.margin_mode,
                "realized_pnl": str(money(realized)),
                "intrabar_ambiguous": False,
                "flags": ["live"],
            },
            ts=report.ts,
            event_version=2,
        )

    def _projected_notional(self, order: Order, ref_price: float) -> float:
        """The market's absolute notional if ``order`` fully filled (cap input)."""
        pos = self.state.position(order.venue or self._venue, order.market)
        current = pos.signed_size if pos is not None else 0.0
        projected = current + order.side.sign * order.size
        return abs(projected) * ref_price

    def _equity(self) -> float:
        """Session equity estimate = free cash + unrealized at last-known marks."""
        cash = sum((a.cash for a in self.state.accounts.values()), start=money(0))
        unrealized = 0.0
        for (_venue, market), pos in self.state.positions.items():
            mark = self._marks.get(market)
            if mark is not None:
                unrealized += pos.unrealized_pnl(mark)
        return float(cash) + unrealized

    def _session_loss(self) -> float:
        """Positive dollars lost since the run's initial capital (0 if in profit)."""
        return max(0.0, float(self._initial) - self._equity())

    def _persist_status(self, status: str) -> None:
        record = self._store.load_run(self._tenant, self._run_id)
        self._store.save_run(
            self._tenant,
            RunRecord(
                run_id=self._run_id,
                kind="live",
                status=status,
                created_ts=record.created_ts,
                summary=dict(record.summary),
            ),
        )

    def _reconstruct(self) -> PortfolioState:
        """Fold the persisted log into live state — restart-safe (§6.7)."""
        book = fold(self._log.read())
        state = PortfolioState()
        state.fund(self._venue, self._initial)
        for venue, a in book.accounts.items():
            target = state.account(venue)
            target.cash += a.cash
            target.fees_paid += a.fees_paid
            target.funding_paid += a.funding_paid
            target.realized_pnl += a.realized_pnl
        state.positions.update(book.positions)
        self._orders = dict(book.orders)
        return state

    def _next_coid(self) -> str:
        self._coid += 1
        return f"live-{self._run_id}-{self._coid}"


def stop_all_live(
    tenant: TenantContext,
    *,
    store: UserDataPort,
    secrets: SecretsPort,
    client_factory: ClientFactory = hyperliquid_client_factory,
    flatten: bool = False,
) -> list[StopReport]:
    """Kill every running live run for ``tenant`` — the ``flint live --stop --all`` path.

    Finds each ``kind="live"`` run still ``running`` in the tenant's store,
    reattaches an executor, and stops it (cancel + optional flatten). Runs that
    lack a venue key are skipped rather than crashing the sweep.
    """
    reports: list[StopReport] = []
    for record in store.list_runs(tenant):
        if record.kind != "live" or record.status != "running":
            continue
        try:
            executor = LiveExecutor.resume(
                tenant=tenant,
                store=store,
                secrets=secrets,
                run_id=record.run_id,
                client_factory=client_factory,
            )
        except LiveStartRefused:
            continue
        reports.append(executor.stop(flatten=flatten, reason="stop_all"))
    return reports
