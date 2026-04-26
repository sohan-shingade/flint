"""PaperTradingEngine — runs a strategy against live data with simulated execution.

Uses the same Strategy code as BacktestEngine, but receives candles from
the live collector and executes orders through `PaperContext` (post-D-2.1.d
the unified state owner + strategy-facing context, replacing the old
`PaperBroker` + `LiveContext` split).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Dict, List, Optional

from ..backtest.engine import BacktestEngine
from ..execution._position import _Position
from ..models import Candle, Signal, Side
from ..store import FlintStore
from ..strategy.base import Strategy
from .context import PaperContext
from .session_store import PaperSessionStore
from .risk_guard import RiskGuard, RiskConfig

logger = logging.getLogger("flint.paper")


def backfill_candle_gap(store: FlintStore, market: str, from_ts: int, to_ts: int) -> int:
    """Download candles for a gap period so resumed sessions can catch up.

    Walks a fallback chain so a single venue outage doesn't strand
    paper sessions on resume. Order:

    1. **Hyperliquid** — primary live source (Drift offline post-hack;
       see CLAUDE.md). Covers every market in `_FLINT_TO_HL`.
    2. **Drift Data API** — kept registered so resume auto-recovers
       when Drift returns; fails fast in the meantime.
    3. **Drift S3** — archival fallback for older windows; Drift's
       S3 path is also offline today, but the chain stays in place.

    Returns count of candles stored. Each source is attempted
    independently; failures are logged at debug and the chain falls
    through. The first source that returns rows wins.
    """
    if to_ts - from_ts < 3600:
        return 0

    # 1. Hyperliquid first — primary live source.
    try:
        from ..providers.hyperliquid_candles import (
            HyperliquidCandleProvider, _FLINT_TO_HL,
        )
        if market in _FLINT_TO_HL:
            provider = HyperliquidCandleProvider()
            try:
                candles = provider.fetch_candles(
                    market, from_ts, to_ts, resolution="1h",
                )
            finally:
                provider.close()
            if candles:
                stored = store.upsert_candles(candles)
                logger.info(
                    "Backfilled %d candles for %s via Hyperliquid (%d->%d)",
                    stored, market, from_ts, to_ts,
                )
                return stored
    except Exception as e:
        logger.debug("Hyperliquid backfill failed for %s: %s", market, e)

    # 2. Drift Data API — offline post-hack, kept for auto-recovery.
    try:
        from ..providers.drift_candles import DriftCandleProvider
        provider = DriftCandleProvider()
        try:
            candles = provider.fetch_candles(market, 3600, from_ts, to_ts)
        finally:
            provider.close()
        if candles:
            stored = store.upsert_candles(candles)
            logger.info(
                "Backfilled %d candles for %s via Drift API (%d->%d)",
                stored, market, from_ts, to_ts,
            )
            return stored
    except Exception as e:
        logger.debug("Drift API backfill failed for %s: %s", market, e)

    # 3. Drift S3 archival — also offline today.
    try:
        from ..providers.drift_s3 import DriftS3Provider
        provider = DriftS3Provider()
        try:
            candles = provider.fetch_candles(market, 3600, from_ts, to_ts)
        finally:
            provider.close()
        if candles:
            stored = store.upsert_candles(candles)
            logger.info(
                "Backfilled %d candles for %s via Drift S3 (%d->%d)",
                stored, market, from_ts, to_ts,
            )
            return stored
    except Exception as e:
        logger.warning(
            "All backfill sources failed for %s: %s", market, e,
        )

    return 0


class PaperSession:
    """Represents a single paper trading session.

    Post-D-2.1.d: `ctx` is a `PaperContext` (unified state owner +
    strategy-facing context). The legacy `broker` attribute aliases
    the same instance so existing callers that read
    `session.broker.<x>` keep working — `PaperContext` exposes every
    `PaperBroker` property + method.
    """

    def __init__(self, session_id: str, strategy: Strategy, market: str,
                 resolution_s: int, ctx: PaperContext,
                 venue: Optional[str] = None):
        self.session_id = session_id
        self.strategy = strategy
        self.market = market
        self.resolution_s = resolution_s
        self.ctx = ctx
        # Back-compat alias: `session.broker` is the same PaperContext
        # instance. Code that reads `session.broker.cash`, `.positions`,
        # `.equity`, etc. resolves onto PaperContext properties.
        self.broker = ctx
        if venue is None:
            from ..config import default_venue
            venue = default_venue()
        self.venue = venue
        self.started_at = int(time.time())
        self.status = "running"
        self.equity_history: List[dict] = []
        self.last_candle_ts = 0

    def to_dict(self) -> dict:
        unrealized_pnl = self.ctx.unrealized_pnl_total
        # Include DB trades (replay + live) when session_store is available
        ss = getattr(self, "session_store", None)
        if ss:
            all_trades = ss.get_trades(self.session_id)
        else:
            all_trades = self.ctx.closed_trades
        realized_pnl = sum(t.get("pnl", 0) for t in all_trades)
        # Public position list — flat dicts, one per (venue, market) leg.
        positions = [p.to_dict() for p in self.ctx._pm.values()]
        return {
            "session_id": self.session_id,
            "strategy": self.strategy.name,
            "market": self.market,
            "venue": self.venue,
            "resolution_s": self.resolution_s,
            "status": self.status,
            "started_at": self.started_at,
            "equity": self.ctx.equity,
            "cash": self.ctx.cash,
            "initial_capital": self.ctx.initial_capital,
            "positions": positions,
            "pending_orders": len(self.ctx.pending_orders),
            "total_trades": len(all_trades),
            "total_fees": self.ctx.total_fees,
            "total_funding": self.ctx.total_funding,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "pnl": realized_pnl + unrealized_pnl,
        }


class PaperTradingEngine:
    """Manages paper trading sessions."""

    def __init__(self, store: FlintStore):
        self.store = store
        self.sessions: Dict[str, PaperSession] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None  # set by lifespan
        # D-4.3-websocket slice 2: optional ConnectionManager — when
        # set, per-bar equity ticks broadcast to `paper:{session_id}`.
        # None when the API isn't running (e.g. unit tests).
        self.ws_manager = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store reference to the main event loop for scheduling tasks from sync threads."""
        self._loop = loop

    def _schedule_async_task(self, coro) -> Optional[asyncio.Task]:
        """Schedule an async task on the event loop, works from both sync and async contexts."""
        # Try 1: We're in the event loop thread
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(coro)
            logger.info("Scheduled async task via create_task (in event loop thread)")
            return task
        except RuntimeError:
            pass
        # Try 2: We're in a threadpool thread, use the stored loop reference
        if self._loop is not None and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            logger.info("Scheduled async task via run_coroutine_threadsafe (from threadpool)")
            return future  # type: ignore
        logger.warning("No event loop available (self._loop=%s) — async task not scheduled", self._loop)
        return None

    def start_session(
        self,
        strategy: Strategy,
        market: str = "SOL-PERP",
        resolution_s: int = 3600,
        initial_capital: float = 10_000.0,
        venue: Optional[str] = None,
        strategy_code: str = "",
        strategy_params: dict = None,
        risk_config: dict = None,
    ) -> str:
        """Start a new paper trading session with replay. Returns session_id.

        Delegates to deploy_session() with a 7-day default replay so all
        sessions get persistence, equity history, and transition to 'live' status.
        """
        replay_start = int(time.time()) - 7 * 86400
        return self.deploy_session(
            strategy=strategy,
            strategy_code=strategy_code or "",
            strategy_params=strategy_params or {},
            risk_config=risk_config,
            market=market,
            resolution_s=resolution_s,
            initial_capital=initial_capital,
            replay_start_ts=replay_start,
            venue=venue,
        )

    def stop_session(self, session_id: str) -> bool:
        """Stop a paper trading session."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.status = "stopped"
        task = self._tasks.get(session_id)
        if task:
            task.cancel()
        # Persist status to DB so it doesn't resume on restart
        ss = getattr(session, "session_store", None)
        if ss:
            ss.update_status(session_id, "stopped", stopped_at=int(time.time()))
        logger.info("Paper session %s stopped", session_id)
        return True

    def kill_session(self, session_id: str) -> bool:
        """Emergency stop — close all positions and stop."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        # Close every leg across all (venue, market) pairings.
        for (venue, market), pos in list(session.ctx._pm.items()):
            opposite = Side.SHORT if pos.side == Side.LONG else Side.LONG
            session.ctx.market_order(
                market, opposite, pos.size, reduce_only=True, venue=venue,
            )
        session.ctx.cancel_all()
        session.status = "killed"
        task = self._tasks.get(session_id)
        if task:
            task.cancel()
        # Persist status to DB so it doesn't resume on restart
        ss = getattr(session, "session_store", None)
        if ss:
            ss.update_status(session_id, "killed", stopped_at=int(time.time()))
        logger.info("Paper session %s killed", session_id)
        return True

    def get_status(self, session_id: str) -> Optional[dict]:
        session = self.sessions.get(session_id)
        if session is None:
            return None
        return session.to_dict()

    def get_trades(self, session_id: str) -> List[dict]:
        session = self.sessions.get(session_id)
        if session is None:
            return []
        # Use DB trades (includes replay + live) when session_store is available
        ss = getattr(session, "session_store", None)
        if ss:
            return ss.get_trades(session_id)
        return session.ctx.closed_trades

    def list_sessions(self) -> List[dict]:
        return [s.to_dict() for s in self.sessions.values()]

    def resume_sessions(self) -> int:
        """Resume all active sessions from DuckDB after server restart.

        Returns count of successfully resumed sessions.
        """
        ss = PaperSessionStore(self.store)
        active = ss.list_active_sessions()
        resumed = 0

        for session_data in active:
            sid = session_data["session_id"]
            try:
                full = ss.load_session(sid)
                if not full:
                    continue

                # Reconstruct strategy
                from ..strategy.loader import load_user_strategy
                strategy = load_user_strategy(
                    full["strategy_code"],
                    full["strategy_params"] or None,
                )

                # Get last equity snapshot for cash recovery
                equity_history = ss.get_equity_history(sid)
                last_eq = equity_history[-1] if equity_history else None
                cash = last_eq["equity"] if last_eq else full["initial_capital"]
                last_ts = last_eq["ts"] if last_eq else 0

                # Reconstruct context with recovered cash. If the
                # persisted session pre-dates the venue column, fall
                # back to the configured default (Hyperliquid post-
                # v1.5.4) — never silently force "drift" since that
                # surface is offline post-hack.
                from ..config import default_venue
                resumed_venue = full.get("venue") or default_venue()
                ctx = PaperContext(
                    initial_capital=cash, venue=resumed_venue,
                    store=self.store, resolution_s=3600, session_id=sid,
                )
                # Restore full equity history for accurate peak/drawdown tracking
                ctx.equity_history = (
                    [eq["equity"] for eq in equity_history]
                    if equity_history else [cash]
                )

                # Restore positions from DB into the PositionManager,
                # keyed by (venue, market) so multi-venue legs survive.
                positions = ss.load_positions(sid)
                for p in positions:
                    venue = p.get("venue", resumed_venue)
                    side = Side.LONG if p["side"] == "long" else Side.SHORT
                    pos = _Position(
                        market=p["market"], side=side, size=p["size"],
                        entry_price=p["entry_price"],
                        entry_ts=p.get("entry_ts", 0), venue=venue,
                    )
                    pos.unrealized_pnl = p.get("unrealized_pnl", 0)
                    pos.mark_price = p.get("entry_price", 0)
                    ctx._pm.set((venue, p["market"]), pos)

                # Restore closed trades + counters via the managers.
                trades = ss.get_trades(sid)
                # Keep the closed-trade ledger in sync for tearsheet
                # attribution (PaperContext exposes it via property).
                ctx._pm._closed.extend(trades)
                ctx._cm.total_fees = sum(t.get("fees", 0) for t in trades)

                # Restore funding total from the persisted ledger.
                funding_payments = ss.get_funding_payments(sid)
                ctx._cm.total_funding = sum(
                    fp.get("payment", 0) for fp in funding_payments
                )

                session = PaperSession(
                    session_id=sid, strategy=strategy,
                    market=full["market"], resolution_s=3600,
                    ctx=ctx, venue=resumed_venue,
                )
                session.last_candle_ts = last_ts
                session.status = "live"
                session.session_store = ss
                session._persisted_trade_count = len(trades)

                # Attach risk guard
                risk_cfg = full.get("risk_config", {})
                if isinstance(risk_cfg, str):
                    import json
                    risk_cfg = json.loads(risk_cfg)
                rc = RiskConfig(
                    max_drawdown_pct=risk_cfg.get("max_drawdown_pct", 0.15),
                    daily_loss_limit=risk_cfg.get("daily_loss_limit", 500),
                    max_position_pct=risk_cfg.get("max_position_pct", 0.95),
                    liquidation_enabled=risk_cfg.get("liquidation_enabled", True),
                )
                session.risk_guard = RiskGuard(rc)
                session.risk_config = risk_cfg

                self.sessions[sid] = session

                # Cancel stale pending orders (they're from before restart)
                ctx._oq.cancel_all()

                # Backfill any candle data missing during server downtime
                now_ts = int(time.time())
                if last_ts > 0 and now_ts - last_ts > 3600:
                    backfill_candle_gap(self.store, full["market"], last_ts, now_ts)

                # Launch live loop
                task = self._schedule_async_task(self._run_live_session(session))
                if task is not None:
                    self._tasks[sid] = task

                resumed += 1
                logger.info("Resumed session %s: %s on %s (last_ts=%d, cash=%.2f, positions=%d)",
                            sid, full["strategy_name"], full["market"], last_ts, cash, len(positions))
            except Exception as e:
                logger.error("Failed to resume session %s: %s", sid, e)

        logger.info("Resumed %d/%d active sessions", resumed, len(active))
        return resumed

    def deploy_session(
        self,
        strategy: Strategy,
        strategy_code: str,
        strategy_params: dict,
        market: str = "SOL-PERP",
        resolution_s: int = 3600,
        initial_capital: float = 10_000.0,
        replay_start_ts: int = 0,
        risk_config: Optional[dict] = None,
        capital_allocation: Optional[dict] = None,
        venue: Optional[str] = None,
    ) -> str:
        """Deploy a strategy with replay-forward execution."""
        if venue is None:
            from ..config import default_venue
            venue = default_venue()
        session_id = uuid.uuid4().hex[:8]
        risk_cfg = risk_config or {}
        now = int(time.time())
        ss = PaperSessionStore(self.store)

        # Persist session metadata
        ss.save_session(
            session_id=session_id,
            strategy_name=strategy.name,
            strategy_code=strategy_code,
            strategy_params=strategy_params,
            market=market,
            initial_capital=initial_capital,
            replay_start_ts=replay_start_ts,
            started_at=now,
            status="replaying",
            risk_config=risk_cfg,
        )

        # Phase 1: Replay
        try:
            strategy.reset()
            candles = self.store.query_candles(market, resolution_s, replay_start_ts, now)
        except Exception as e:
            # Clean up orphaned session record on failure
            ss.update_status(session_id, "failed", stop_reason=f"Replay init failed: {e}")
            raise

        replay_equity: List[dict] = []
        final_cash = initial_capital

        if candles:
            try:
                engine = BacktestEngine(strategy, initial_capital, fee_rate=0.0005)
                result = engine.run(candles)
            except Exception as e:
                ss.update_status(session_id, "failed", stop_reason=f"Replay failed: {e}")
                raise

            # Record replay equity
            for i, eq in enumerate(result.equity_curve):
                if i < len(candles):
                    replay_equity.append({
                        "ts": candles[i].ts, "equity": eq, "cash": eq,
                        "unrealized_pnl": 0, "is_replay": True,
                    })

            # Record replay trades
            replay_trades: List[dict] = []
            for j, pos in enumerate(result.positions):
                replay_trades.append({
                    "trade_id": f"replay-{j}",
                    "market": market,
                    "side": pos.side.value if isinstance(pos.side, Side) else str(pos.side),
                    "size": abs(pos.size),
                    "entry_price": pos.entry_price,
                    "exit_price": pos.exit_price,
                    "entry_ts": pos.entry_ts,
                    "exit_ts": pos.exit_ts,
                    "pnl": pos.pnl,
                    "fees": 0, "is_replay": True,
                })

            final_cash = result.equity_curve[-1] if result.equity_curve else initial_capital

            ss.save_equity_snapshots(session_id, replay_equity)
            if replay_trades:
                ss.save_trades(session_id, replay_trades)

        # Set up live session
        ctx = PaperContext(
            initial_capital=final_cash,
            capital_allocation=capital_allocation,
            venue=venue,
            store=self.store,
            resolution_s=resolution_s,
            session_id=session_id,
        )
        # RiskGuard.check() reads ctx.equity_history for peak tracking
        ctx.equity_history = [final_cash]

        session = PaperSession(
            session_id=session_id, strategy=strategy, market=market,
            resolution_s=resolution_s, ctx=ctx, venue=venue,
        )
        session.last_candle_ts = candles[-1].ts if candles else 0
        session.status = "live"

        # Attach risk guard and persistence
        rc = RiskConfig(
            max_drawdown_pct=risk_cfg.get("max_drawdown_pct", 0.15),
            daily_loss_limit=risk_cfg.get("daily_loss_limit", 500),
            max_position_pct=risk_cfg.get("max_position_pct", 0.95),
            liquidation_enabled=risk_cfg.get("liquidation_enabled", True),
        )
        session.risk_guard = RiskGuard(rc)
        session.risk_config = risk_cfg
        session.session_store = ss

        self.sessions[session_id] = session
        ss.update_status(session_id, "live", live_start_ts=int(time.time()))

        # Launch async live loop
        task = self._schedule_async_task(self._run_live_session(session))
        if task is not None:
            self._tasks[session_id] = task

        logger.info("Deployed session %s: %s on %s (replayed %d candles)",
                     session_id, strategy.name, market, len(candles))
        return session_id

    def redeploy_session(self, session_id: str, replay_start_ts: int) -> Optional[str]:
        """Kill an existing session and redeploy it from a new start date.
        Returns new session_id on success, None on failure.
        """
        ss = PaperSessionStore(self.store)
        old = ss.load_session(session_id)
        if not old:
            return None

        # Stop the old session
        self.kill_session(session_id)
        if session_id in self.sessions:
            del self.sessions[session_id]

        # Clear old data
        ss.clear_session_data(session_id)

        # Backfill candle data for the new range
        now_ts = int(time.time())
        backfill_candle_gap(self.store, old["market"], replay_start_ts, now_ts)

        # Rebuild strategy
        from ..strategy.loader import load_user_strategy
        strategy = load_user_strategy(old["strategy_code"], old["strategy_params"] or None)

        # Re-deploy
        new_id = self.deploy_session(
            strategy=strategy,
            strategy_code=old["strategy_code"],
            strategy_params=old["strategy_params"],
            market=old["market"],
            initial_capital=old["initial_capital"],
            replay_start_ts=replay_start_ts,
            risk_config=old["risk_config"],
        )

        # Mark old session as replaced
        ss.update_status(session_id, "replaced", stopped_at=now_ts, stop_reason=f"redeployed as {new_id}")

        return new_id

    def _apply_session_funding(
        self,
        session: PaperSession,
        ss,
        fallback_mark_price: float,
    ) -> None:
        """Multi-venue funding application for one tick.

        Iterates the distinct venues appearing in the session's open
        positions, queries `venue_funding_rates` per venue, applies
        with `venue=` so an HL rate doesn't book against a Drift leg.
        Persists payments with the originating venue tagged.

        Extracted from `_run_live_session` for testability — async
        loops are awkward to mock; this is sync and pure.
        """
        last_funding_ts = getattr(session, '_last_funding_ts', 0)
        now_ts = int(time.time())
        # Distinct venues across the session's open legs. Multi-venue
        # paper sessions (cross-venue funding-arb, basis trades) hold
        # legs on multiple venues simultaneously now that positions
        # are keyed by (venue, market) post-D-2.1.d.
        venues_in_book = {
            pos.venue for pos in session.ctx._pm.values()
            if pos.market == session.market
        }
        latest_ts = last_funding_ts
        for v in venues_in_book:
            try:
                funding = self.store.query_venue_funding(
                    v, session.market,
                    last_funding_ts + 1, now_ts,
                )
            except Exception as e:
                logger.debug(
                    "Funding query error %s/%s: %s",
                    session.session_id, v, e,
                )
                continue
            for fr in funding:
                mp = fr["mark_price"] if fr.get("mark_price") else fallback_mark_price
                try:
                    payment = session.ctx.apply_funding(
                        session.market, fr["rate_hourly"], mp, venue=v,
                    )
                except Exception as e:
                    logger.debug(
                        "Funding apply error %s/%s: %s",
                        session.session_id, v, e,
                    )
                    continue
                if payment != 0 and ss:
                    pos = session.ctx.position_at(v, session.market)
                    ss.save_funding_payment(
                        session.session_id, fr["ts"], session.market,
                        fr["rate_hourly"], payment,
                        pos.size if pos else 0, mp,
                        venue=v,
                    )
                if fr["ts"] > latest_ts:
                    latest_ts = fr["ts"]
        session._last_funding_ts = latest_ts

    async def _run_live_session(self, session: PaperSession) -> None:
        """Live loop with risk guard checks and persistence."""
        logger.info("Live loop STARTED for session %s (%s), last_candle_ts=%d",
                     session.session_id, session.market, session.last_candle_ts)
        history: List[Candle] = []

        try:
            warm_up = self.store.query_candles(session.market, session.resolution_s)
            logger.info("Warm-up loaded %d candles for %s", len(warm_up) if warm_up else 0, session.market)
            if warm_up:
                history = warm_up[-500:]
        except Exception as e:
            logger.warning("Failed to load warm-up candles: %s", e)

        equity_buffer: List[dict] = []
        ss = getattr(session, "session_store", None)

        while session.status in ("running", "live"):
            try:
                candles = self.store.query_candles(
                    session.market, session.resolution_s,
                    start_ts=session.last_candle_ts + 1,
                )

                for candle in candles:
                    history.append(candle)
                    if len(history) > 500:
                        history = history[-500:]
                    session.last_candle_ts = candle.ts
                    session.ctx.set_candle(candle)
                    session.ctx.process_candle(candle)

                    signal = session.strategy.on_candle(candle, history, ctx=session.ctx)

                    if signal == Signal.BUY and not session.ctx.positions:
                        size = (session.ctx.account.cash * 0.95) / candle.close
                        if size > 0:
                            session.ctx.market_order(candle.market, Side.LONG, size)
                    elif signal == Signal.SELL and session.ctx.positions:
                        session.ctx.close_position(candle.market)

                    session.ctx.process_candle(candle)

                    # Persist new closed trades
                    if ss:
                        trade_count = getattr(session, '_persisted_trade_count', 0)
                        new_trades = session.ctx.closed_trades[trade_count:]
                        if new_trades:
                            for idx, t in enumerate(new_trades):
                                t.setdefault("trade_id", f"live-{session.session_id}-{trade_count + idx}")
                                t.setdefault("is_replay", False)
                            ss.save_trades(session.session_id, new_trades)
                            session._persisted_trade_count = len(session.ctx.closed_trades)
                            # D-4.3-websocket slice 2b: broadcast each
                            # newly-closed trade to ws subscribers.
                            if self.ws_manager is not None:
                                for trade in new_trades:
                                    try:
                                        await self.ws_manager.broadcast(
                                            f"paper:{session.session_id}",
                                            {"type": "trade", **trade},
                                        )
                                    except Exception as e:
                                        logger.debug("WS trade broadcast skipped: %s", e)

                    # Track equity for risk guard peak calculation
                    session.ctx.equity_history.append(session.ctx.equity)

                    eq_snap = {
                        "ts": candle.ts, "equity": session.ctx.equity,
                        "cash": session.ctx.cash,
                        "unrealized_pnl": session.ctx.unrealized_pnl_total,
                        "is_replay": False,
                    }
                    session.equity_history.append(eq_snap)
                    equity_buffer.append(eq_snap)

                    # D-4.3-websocket slice 2: broadcast tick to
                    # `paper:{session_id}` subscribers. The manager
                    # noop's when no clients are connected, so this is
                    # cheap; failures are swallowed (log spam from a
                    # dead socket should never tank the engine).
                    if self.ws_manager is not None:
                        try:
                            await self.ws_manager.broadcast(
                                f"paper:{session.session_id}",
                                {"type": "tick", **eq_snap,
                                 "total_trades": len(session.ctx.closed_trades)},
                            )
                        except Exception as e:
                            logger.debug("WS broadcast skipped: %s", e)

                    # Persist positions for crash recovery — one row
                    # per (venue, market) leg.
                    if ss:
                        pos_list = [p.to_dict() for p in session.ctx._pm.values()]
                        ss.save_positions(session.session_id, pos_list)

                    # Risk guard check
                    guard = getattr(session, "risk_guard", None)
                    if guard:
                        mark_prices = {session.market: candle.close}
                        breach = guard.check(
                            session.ctx, session.ctx.initial_capital, mark_prices,
                        )
                        if breach:
                            session.status = "risk_stopped"
                            logger.warning("Session %s risk stop: %s", session.session_id, breach)
                            if ss:
                                ss.update_status(session.session_id, "risk_stopped",
                                                 stopped_at=int(time.time()), stop_reason=breach)
                            break

                # Apply funding rates from store. Multi-venue paper
                # sessions hold legs on >1 venue — query each venue's
                # funding stream independently and apply per-leg so a
                # Drift rate doesn't book against an HL leg.
                if session.ctx._pm:
                    fallback_mp = candle.close if candles else 0.0
                    self._apply_session_funding(session, ss, fallback_mp)

                if ss and equity_buffer:
                    ss.save_equity_snapshots(session.session_id, equity_buffer)
                    equity_buffer.clear()

                # Process pending venue transfers
                if session.ctx._cm.allocator is not None:
                    import time as _time
                    session.ctx._cm.allocator.process_arrivals(int(_time.time()))
                    session.ctx.cash = session.ctx._cm.allocator.total_cash

                # Update mark prices from ticker (between candles).
                # Multi-venue legs on the same market all repaint to
                # the latest tick — venue-specific marks would need
                # per-venue ticker channels.
                ticker = getattr(self, 'price_ticker', None)
                if ticker and session.ctx._pm:
                    mark = ticker.get_price(session.market)
                    if mark is not None:
                        for (_v, market), pos in session.ctx._pm.items():
                            if market == session.market:
                                pos.update_pnl(mark)

                await asyncio.sleep(10)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Live session %s error: %s", session.session_id, e)
                await asyncio.sleep(30)

        if ss and equity_buffer:
            ss.save_equity_snapshots(session.session_id, equity_buffer)
