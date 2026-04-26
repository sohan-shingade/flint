"""Tests for paper trading risk guard."""
from unittest.mock import MagicMock
from flint.execution._position import _Position
from flint.models import Side
from flint.paper.risk_guard import RiskGuard, RiskConfig


def _make_broker(equity=10000, cash=5000, positions=None, equity_history=None):
    """Build a mock-broker with the post-D-2.1.d shape: positions live
    on `_pm.values()` as `_Position` objects, not on `broker.positions`
    as a dict-of-dict. The legacy dict spec the tests pass us
    (`{"SOL-PERP": {"size": 100, "side": "long", ...}}`) is converted
    to `_Position` instances and exposed via a real PositionManager-like
    object so RiskGuard's `for pos in broker._pm.values():` iteration
    works."""
    b = MagicMock()
    b.equity = equity
    b.cash = cash
    pm = MagicMock()
    pos_objs = []
    for market, p in (positions or {}).items():
        side = Side.LONG if p.get("side", "long") == "long" else Side.SHORT
        po = _Position(
            market=market, side=side, size=p["size"],
            entry_price=p["entry_price"],
            entry_ts=p.get("entry_ts", 0),
            venue=p.get("venue", "drift"),
        )
        po.unrealized_pnl = p.get("unrealized_pnl", 0)
        po.mark_price = p.get("mark_price", p["entry_price"])
        pos_objs.append(po)
    pm.values = MagicMock(return_value=pos_objs)
    pm.__bool__ = lambda self_: bool(pos_objs)
    pm.__len__ = lambda self_: len(pos_objs)
    b._pm = pm
    b.equity_history = equity_history or [10000]
    b.initial_capital = 10000
    return b


def test_no_breach():
    guard = RiskGuard(RiskConfig(max_drawdown_pct=0.15, daily_loss_limit=500, max_position_pct=0.95))
    broker = _make_broker(equity=9500, equity_history=[10000, 9800, 9500])
    assert guard.check(broker, 10000, {}) is None


def test_max_drawdown_breach():
    guard = RiskGuard(RiskConfig(max_drawdown_pct=0.10))
    broker = _make_broker(equity=8900, equity_history=[10000, 9500, 8900])
    result = guard.check(broker, 10000, {})
    assert result is not None and "max_drawdown" in result


def test_daily_loss_breach():
    import time
    guard = RiskGuard(RiskConfig(daily_loss_limit=200, max_drawdown_pct=0.99))
    broker = _make_broker(equity=9700, equity_history=[10000])
    guard._day_start_equity = 10000
    guard._current_day = int(time.time()) // 86400  # prevent reset on check()
    result = guard.check(broker, 10000, {})
    assert result is not None and "daily_loss" in result


def test_position_size_breach():
    guard = RiskGuard(RiskConfig(max_position_pct=0.50))
    positions = {"SOL-PERP": {"size": 100, "entry_price": 100, "side": "long"}}
    broker = _make_broker(equity=10000, positions=positions)
    result = guard.check(broker, 10000, {"SOL-PERP": 150})
    assert result is not None and "max_position" in result


def test_liquidation_triggered():
    # Disable drawdown and position limits so liquidation check is reached
    guard = RiskGuard(RiskConfig(liquidation_enabled=True, max_drawdown_pct=0.99, max_position_pct=99.0))
    positions = {"SOL-PERP": {"size": 100, "entry_price": 110, "side": "long"}}
    # equity=400, margin_required = 100*100*0.05 = 500, so equity < margin_required
    broker = _make_broker(equity=400, cash=400, positions=positions)
    broker.close_all_positions = MagicMock()
    result = guard.check(broker, 10000, {"SOL-PERP": 100})
    assert result is not None and "liquidation" in result


def test_liquidation_not_triggered_when_healthy():
    # Use initial_capital matching equity_history peak to avoid false drawdown breach
    guard = RiskGuard(RiskConfig(liquidation_enabled=True, max_drawdown_pct=0.99))
    positions = {"SOL-PERP": {"size": 10, "entry_price": 100, "side": "long"}}
    # equity=5000, margin_required = 10*100*0.05 = 50, so equity >> margin_required
    broker = _make_broker(equity=5000, equity_history=[5000], positions=positions)
    assert guard.check(broker, 5000, {"SOL-PERP": 100}) is None


def test_margin_ratio():
    guard = RiskGuard(RiskConfig())
    positions = {"SOL-PERP": {"size": 100, "entry_price": 100, "side": "long"}}
    broker = _make_broker(equity=1000, positions=positions)
    assert abs(guard.margin_ratio(broker, {"SOL-PERP": 100}) - 2.0) < 0.01


def test_liquidation_distance():
    guard = RiskGuard(RiskConfig())
    positions = {"SOL-PERP": {"size": 100, "entry_price": 100, "side": "long"}}
    broker = _make_broker(equity=1000, positions=positions)
    assert abs(guard.liquidation_distance_pct(broker, {"SOL-PERP": 100}) - 0.5) < 0.01


def test_no_positions_infinite_distance():
    guard = RiskGuard(RiskConfig())
    broker = _make_broker(equity=10000, positions={})
    assert guard.liquidation_distance_pct(broker, {}) == 1.0


def test_risk_status_dict():
    guard = RiskGuard(RiskConfig())
    broker = _make_broker(equity=9500, equity_history=[10000, 9500])
    status = guard.risk_status(broker, 10000, {})
    assert "current_drawdown" in status
    assert "margin_ratio" in status
    assert "liquidation_distance_pct" in status
    assert "any_breached" in status
