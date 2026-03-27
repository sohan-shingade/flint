"""PaperTradingEngine — runs a strategy against live data with simulated execution.

Uses the same Strategy code as BacktestEngine, but receives candles from
the live collector and executes orders through PaperBroker.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Dict, List, Optional

from ..backtest.engine import BacktestEngine
from ..execution.fee_models import FlatFeeModel
from ..execution.fill_models import ClosePriceFill
from ..execution.live_context import LiveContext
from ..execution.paper_broker import PaperBroker
from ..models import Candle, Signal, Side
from ..store import FlintStore
from ..strategy.base import Strategy
from .session_store import PaperSessionStore
from .risk_guard import RiskGuard, RiskConfig

logger = logging.getLogger("flint.paper")


class PaperSession:
    """Represents a single paper trading session."""

    def __init__(self, session_id: str, strategy: Strategy, market: str,
                 resolution_s: int, broker: PaperBroker, ctx: LiveContext):
        self.session_id = session_id
        self.strategy = strategy
        self.market = market
        self.resolution_s = resolution_s
        self.broker = broker
        self.ctx = ctx
        self.started_at = int(time.time())
        self.status = "running"
        self.equity_history: List[dict] = []
        self.last_candle_ts = 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "strategy": self.strategy.name,
            "market": self.market,
            "resolution_s": self.resolution_s,
            "status": self.status,
            "started_at": self.started_at,
            "equity": self.broker.equity,
            "cash": self.broker.cash,
            "positions": list(self.broker.positions.values()),
            "pending_orders": len(self.broker.pending_orders),
            "total_trades": len(self.broker.closed_trades),
            "total_fees": self.broker.total_fees,
            "pnl": self.broker.equity - self.broker.initial_capital,
        }


class PaperTradingEngine:
    """Manages paper trading sessions."""

    def __init__(self, store: FlintStore):
        self.store = store
        self.sessions: Dict[str, PaperSession] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    def start_session(
        self,
        strategy: Strategy,
        market: str = "SOL-PERP",
        resolution_s: int = 3600,
        initial_capital: float = 10_000.0,
    ) -> str:
        """Start a new paper trading session. Returns session_id."""
        session_id = uuid.uuid4().hex[:8]
        broker = PaperBroker(initial_capital=initial_capital)
        ctx = LiveContext(broker)

        session = PaperSession(
            session_id=session_id,
            strategy=strategy,
            market=market,
            resolution_s=resolution_s,
            broker=broker,
            ctx=ctx,
        )
        strategy.reset()
        self.sessions[session_id] = session

        # Launch the async loop if there's a running event loop
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._run_session(session))
            self._tasks[session_id] = task
        except RuntimeError:
            # No running event loop (e.g., in tests) — session created but not running
            pass
        logger.info("Paper session %s started: %s on %s", session_id, strategy.name, market)
        return session_id

    def stop_session(self, session_id: str) -> bool:
        """Stop a paper trading session."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        session.status = "stopped"
        task = self._tasks.get(session_id)
        if task:
            task.cancel()
        logger.info("Paper session %s stopped", session_id)
        return True

    def kill_session(self, session_id: str) -> bool:
        """Emergency stop — close all positions and stop."""
        session = self.sessions.get(session_id)
        if session is None:
            return False
        # Close all positions
        for market in list(session.broker.positions.keys()):
            pos = session.broker.positions[market]
            opposite = Side.SHORT if pos["side"] == "long" else Side.LONG
            session.ctx.market_order(market, opposite, pos["size"], reduce_only=True)
        session.broker.cancel_all()
        session.status = "killed"
        task = self._tasks.get(session_id)
        if task:
            task.cancel()
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
        return session.broker.closed_trades

    def list_sessions(self) -> List[dict]:
        return [s.to_dict() for s in self.sessions.values()]

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
    ) -> str:
        """Deploy a strategy with replay-forward execution."""
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
        strategy.reset()
        candles = self.store.query_candles(market, resolution_s, replay_start_ts, now)

        replay_equity: List[dict] = []
        final_cash = initial_capital

        if candles:
            engine = BacktestEngine(strategy, initial_capital, fee_rate=0.0005)
            result = engine.run(candles)

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
        broker = PaperBroker(initial_capital=final_cash)
        # RiskGuard.check() reads broker.equity_history for peak tracking
        broker.equity_history = [final_cash]
        ctx = LiveContext(broker)

        session = PaperSession(
            session_id=session_id, strategy=strategy, market=market,
            resolution_s=resolution_s, broker=broker, ctx=ctx,
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

        # Launch async loop
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._run_live_session(session))
            self._tasks[session_id] = task
        except RuntimeError:
            pass

        logger.info("Deployed session %s: %s on %s (replayed %d candles)",
                     session_id, strategy.name, market, len(candles))
        return session_id

    async def _run_session(self, session: PaperSession) -> None:
        """Main loop: poll store for new candles, run strategy."""
        history: List[Candle] = []

        # Load recent history for warm-up
        try:
            warm_up = self.store.query_candles(
                session.market, session.resolution_s
            )
            if warm_up:
                history = warm_up[-200:]  # last 200 candles for indicator warm-up
                session.last_candle_ts = history[-1].ts if history else 0
        except Exception as e:
            logger.warning("Failed to load warm-up candles: %s", e)

        while session.status == "running":
            try:
                # Poll for new candles
                candles = self.store.query_candles(
                    session.market,
                    session.resolution_s,
                    start_ts=session.last_candle_ts + 1,
                )

                for candle in candles:
                    history.append(candle)
                    session.last_candle_ts = candle.ts

                    # Set candle on context
                    session.ctx.set_candle(candle)

                    # Process pending orders
                    session.broker.process_candle(candle)

                    # Run strategy
                    signal = session.strategy.on_candle(candle, history, ctx=session.ctx)

                    # v1 signal adapter
                    if signal == Signal.BUY and not session.ctx.positions:
                        size = (session.ctx.account.cash * 0.95) / candle.close
                        if size > 0:
                            session.ctx.market_order(candle.market, Side.LONG, size)
                    elif signal == Signal.SELL and session.ctx.positions:
                        session.ctx.close_position(candle.market)

                    # Process any new market orders
                    session.broker.process_candle(candle)

                    # Record equity
                    session.equity_history.append({
                        "ts": candle.ts,
                        "equity": session.broker.equity,
                    })

                await asyncio.sleep(10)  # poll every 10s

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Paper session %s error: %s", session.session_id, e)
                await asyncio.sleep(30)

    async def _run_live_session(self, session: PaperSession) -> None:
        """Live loop with risk guard checks and persistence."""
        history: List[Candle] = []

        try:
            warm_up = self.store.query_candles(session.market, session.resolution_s)
            if warm_up:
                history = warm_up[-200:]
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
                    session.last_candle_ts = candle.ts
                    session.ctx.set_candle(candle)
                    session.broker.process_candle(candle)

                    signal = session.strategy.on_candle(candle, history, ctx=session.ctx)

                    if signal == Signal.BUY and not session.ctx.positions:
                        size = (session.ctx.account.cash * 0.95) / candle.close
                        if size > 0:
                            session.ctx.market_order(candle.market, Side.LONG, size)
                    elif signal == Signal.SELL and session.ctx.positions:
                        session.ctx.close_position(candle.market)

                    session.broker.process_candle(candle)

                    # Track equity for risk guard peak calculation
                    if hasattr(session.broker, "equity_history"):
                        session.broker.equity_history.append(session.broker.equity)

                    eq_snap = {
                        "ts": candle.ts, "equity": session.broker.equity,
                        "cash": session.broker.cash,
                        "unrealized_pnl": session.broker.equity - session.broker.cash,
                        "is_replay": False,
                    }
                    session.equity_history.append(eq_snap)
                    equity_buffer.append(eq_snap)

                    # Risk guard check
                    guard = getattr(session, "risk_guard", None)
                    if guard:
                        mark_prices = {session.market: candle.close}
                        breach = guard.check(session.broker, session.broker.initial_capital, mark_prices)
                        if breach:
                            session.status = "risk_stopped"
                            logger.warning("Session %s risk stop: %s", session.session_id, breach)
                            if ss:
                                ss.update_status(session.session_id, "risk_stopped",
                                                 stopped_at=int(time.time()), stop_reason=breach)
                            break

                if ss and len(equity_buffer) >= 10:
                    ss.save_equity_snapshots(session.session_id, equity_buffer)
                    equity_buffer.clear()

                await asyncio.sleep(10)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Live session %s error: %s", session.session_id, e)
                await asyncio.sleep(30)

        if ss and equity_buffer:
            ss.save_equity_snapshots(session.session_id, equity_buffer)
