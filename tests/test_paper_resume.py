"""Tests for paper trading session resumption."""
import os
import tempfile
import time
from unittest.mock import patch

import pytest

from flint.models import Candle
from flint.paper.engine import PaperTradingEngine
from flint.paper.session_store import PaperSessionStore
from flint.store import FlintStore
from flint.strategy.ma_crossover import MACrossoverStrategy


@pytest.fixture
def store_and_engine():
    db = os.path.join(tempfile.gettempdir(), "test_resume.duckdb")
    store = FlintStore(db)
    engine = PaperTradingEngine(store)
    yield store, engine
    store.close()
    if os.path.exists(db):
        os.unlink(db)


def _make_candles(n=50, start_ts=1700000000):
    candles = []
    price = 100.0
    for i in range(n):
        ts = start_ts + i * 3600
        price *= 1.01 if i % 2 == 0 else 0.99
        candles.append(Candle(
            market="SOL-PERP", resolution_s=3600, ts=ts,
            open=price, high=price * 1.005, low=price * 0.995,
            close=price, volume=1000,
        ))
    return candles


def test_resume_sessions_loads_from_db(store_and_engine):
    store, engine = store_and_engine
    candles = _make_candles(50)
    store.upsert_candles(candles)

    strategy = MACrossoverStrategy(fast_period=5, slow_period=10)
    strategy_code = '''
from flint.strategy import Strategy
from flint.models import Candle, Signal
class S(Strategy):
    @property
    def name(self): return "S"
    def reset(self): pass
    def on_candle(self, c, h, ctx=None): return Signal.HOLD
'''
    session_id = engine.deploy_session(
        strategy=strategy, strategy_code=strategy_code,
        strategy_params={},
        market="SOL-PERP", initial_capital=10000,
        replay_start_ts=1700000000, risk_config={},
    )

    # Simulate restart: fresh engine
    engine2 = PaperTradingEngine(store)
    assert len(engine2.sessions) == 0

    resumed = engine2.resume_sessions()
    assert resumed >= 1
    assert session_id in engine2.sessions


def test_positions_persisted_and_restored(store_and_engine):
    store, engine = store_and_engine

    # Manually save a session and positions to DB
    ss = PaperSessionStore(store)
    ss.save_session(
        session_id="resume-test", strategy_name="MA-Crossover(5/10)",
        strategy_code='''
from flint.strategy import Strategy
from flint.models import Candle, Signal
class S(Strategy):
    @property
    def name(self): return "S"
    def reset(self): pass
    def on_candle(self, c, h, ctx=None): return Signal.HOLD
''',
        strategy_params={}, market="SOL-PERP",
        initial_capital=10000, replay_start_ts=1700000000,
        started_at=1700000000, status="live", risk_config={},
    )
    ss.save_positions("resume-test", [
        {"market": "SOL-PERP", "side": "long", "size": 50.0,
         "entry_price": 100.0, "entry_ts": 1700000000, "unrealized_pnl": 500.0},
    ])
    ss.save_equity_snapshots("resume-test", [
        {"ts": 1700050000, "equity": 10500, "cash": 10000, "unrealized_pnl": 500, "is_replay": False},
    ])

    # Resume
    engine2 = PaperTradingEngine(store)
    resumed = engine2.resume_sessions()
    assert resumed == 1

    session = engine2.sessions["resume-test"]
    assert len(session.broker.positions) == 1
    assert session.broker.positions["SOL-PERP"]["size"] == 50.0
    assert session.last_candle_ts == 1700050000


def test_closed_trades_and_totals_restored_after_resume(store_and_engine):
    store, engine = store_and_engine

    # Manually save a session with trades and funding payments to DB
    ss = PaperSessionStore(store)
    ss.save_session(
        session_id="trades-test", strategy_name="MA-Crossover(5/10)",
        strategy_code='''
from flint.strategy import Strategy
from flint.models import Candle, Signal
class S(Strategy):
    @property
    def name(self): return "S"
    def reset(self): pass
    def on_candle(self, c, h, ctx=None): return Signal.HOLD
''',
        strategy_params={}, market="SOL-PERP",
        initial_capital=10000, replay_start_ts=1700000000,
        started_at=1700000000, status="live", risk_config={},
    )
    ss.save_trades("trades-test", [
        {"trade_id": "t1", "market": "SOL-PERP", "side": "long", "size": 10.0,
         "entry_price": 100.0, "exit_price": 105.0, "entry_ts": 1700000000,
         "exit_ts": 1700010000, "pnl": 50.0, "fees": 0.5, "is_replay": False},
        {"trade_id": "t2", "market": "SOL-PERP", "side": "short", "size": 5.0,
         "entry_price": 110.0, "exit_price": 108.0, "entry_ts": 1700020000,
         "exit_ts": 1700030000, "pnl": 10.0, "fees": 1.0, "is_replay": False},
    ])
    ss.save_funding_payment("trades-test", ts=1700005000, market="SOL-PERP",
                            rate=0.001, payment=-2.5, position_size=10.0,
                            mark_price=100.0)
    ss.save_funding_payment("trades-test", ts=1700015000, market="SOL-PERP",
                            rate=0.0005, payment=1.0, position_size=5.0,
                            mark_price=108.0)
    ss.save_equity_snapshots("trades-test", [
        {"ts": 1700030000, "equity": 10060, "cash": 10060, "unrealized_pnl": 0, "is_replay": False},
    ])

    # Resume in a fresh engine
    engine2 = PaperTradingEngine(store)
    resumed = engine2.resume_sessions()
    assert resumed == 1

    session = engine2.sessions["trades-test"]
    assert len(session.broker.closed_trades) == 2
    assert session.broker.closed_trades[0]["pnl"] == 50.0
    assert session.broker.closed_trades[1]["pnl"] == 10.0
    assert session.broker.total_fees == 1.5
    assert session.broker.total_funding == pytest.approx(-1.5)


def test_resume_skips_stopped_sessions(store_and_engine):
    store, engine = store_and_engine
    ss = PaperSessionStore(store)
    ss.save_session(
        session_id="stopped-1", strategy_name="S",
        strategy_code="class S: pass",
        strategy_params={}, market="SOL-PERP",
        initial_capital=10000, replay_start_ts=0,
        started_at=1700000000, status="stopped", risk_config={},
    )

    engine2 = PaperTradingEngine(store)
    resumed = engine2.resume_sessions()
    assert resumed == 0


def test_resume_calls_backfill_candle_gap(store_and_engine):
    """When a session's last equity snapshot was >1 hour ago, resume should backfill candles."""
    store, engine = store_and_engine

    # Save a session whose last equity snapshot was 2 hours ago
    now = int(time.time())
    two_hours_ago = now - 7200
    ss = PaperSessionStore(store)
    ss.save_session(
        session_id="backfill-test", strategy_name="MA-Crossover(5/10)",
        strategy_code='''
from flint.strategy import Strategy
from flint.models import Candle, Signal
class S(Strategy):
    @property
    def name(self): return "S"
    def reset(self): pass
    def on_candle(self, c, h, ctx=None): return Signal.HOLD
''',
        strategy_params={}, market="SOL-PERP",
        initial_capital=10000, replay_start_ts=1700000000,
        started_at=1700000000, status="live", risk_config={},
    )
    ss.save_equity_snapshots("backfill-test", [
        {"ts": two_hours_ago, "equity": 10500, "cash": 10500,
         "unrealized_pnl": 0, "is_replay": False},
    ])

    engine2 = PaperTradingEngine(store)

    with patch("flint.paper.engine.backfill_candle_gap") as mock_backfill:
        mock_backfill.return_value = 2
        resumed = engine2.resume_sessions()

    assert resumed == 1
    mock_backfill.assert_called_once()
    call_args = mock_backfill.call_args
    assert call_args[0][0] is store          # store
    assert call_args[0][1] == "SOL-PERP"     # market
    assert call_args[0][2] == two_hours_ago  # from_ts
    # to_ts should be close to now (within a few seconds of test execution)
    assert abs(call_args[0][3] - now) < 5
