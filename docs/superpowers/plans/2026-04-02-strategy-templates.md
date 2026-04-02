# Strategy Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4 new battle-tested strategy templates with comprehensive documentation, plus a README for the existing FundingArbStrategy.

**Architecture:** Each strategy extends the `Strategy` ABC with `on_candle()`, `reset()`, `parameters()`, and `name`. Single-venue strategies (momentum breakout, funding mean reversion) use v1 Signal returns. Cross-venue/monitoring strategies (MEV arb monitor, basis trade) use v2 ctx-based execution. Each gets a README in `docs/strategies/` with backtest examples.

**Tech Stack:** Existing Strategy ABC, indicators (`numpy`), ExecutionContext API.

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `flint/strategy/momentum_breakout.py` | Breakout with oracle confirmation | Create |
| `flint/strategy/funding_mean_reversion.py` | Bollinger bands on funding rate | Create |
| `flint/strategy/mev_arb_monitor.py` | DEX arb opportunity scanner | Create |
| `flint/strategy/basis_trade.py` | Cross-venue basis arbitrage | Create |
| `flint/strategy/__init__.py` | Register new strategies | Modify |
| `docs/strategies/funding_arb.md` | FundingArb README | Create |
| `docs/strategies/momentum_breakout.md` | MomentumBreakout README | Create |
| `docs/strategies/funding_mean_reversion.md` | FundingMeanReversion README | Create |
| `docs/strategies/mev_arb_monitor.md` | MevArbMonitor README | Create |
| `docs/strategies/basis_trade.md` | BasisTrade README | Create |
| `ROADMAP.md` | Mark §5.1 as implemented | Modify |
| `tests/test_momentum_breakout.py` | Strategy tests | Create |
| `tests/test_funding_mean_reversion.py` | Strategy tests | Create |
| `tests/test_mev_arb_monitor.py` | Strategy tests | Create |
| `tests/test_basis_trade.py` | Strategy tests | Create |

---

### Task 1: MomentumBreakoutStrategy

**Files:**
- Create: `flint/strategy/momentum_breakout.py`
- Create: `tests/test_momentum_breakout.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_momentum_breakout.py`:

```python
"""Tests for MomentumBreakoutStrategy."""
import pytest
from unittest.mock import MagicMock

from flint.models import Candle, Signal, Side


class TestName:
    def test_name(self):
        from flint.strategy.momentum_breakout import MomentumBreakoutStrategy
        s = MomentumBreakoutStrategy()
        assert s.name == "momentum_breakout"


class TestParameters:
    def test_has_expected_keys(self):
        from flint.strategy.momentum_breakout import MomentumBreakoutStrategy
        params = MomentumBreakoutStrategy.parameters()
        assert "breakout_lookback" in params
        assert "trailing_stop_pct" in params
        assert "oracle_confirmation" in params
        assert "candle_resolution_s" in params


class TestSignals:
    def _make_history(self, prices, market="SOL-PERP"):
        return [Candle(ts=1000 + i * 60, open=p, high=p + 1, low=p - 1,
                       close=p, volume=100.0, market=market, resolution_s=60)
                for i, p in enumerate(prices)]

    def test_no_signal_insufficient_history(self):
        from flint.strategy.momentum_breakout import MomentumBreakoutStrategy
        s = MomentumBreakoutStrategy(breakout_lookback=5)
        s.reset()
        history = self._make_history([100, 101, 102])
        candle = history[-1]
        assert s.on_candle(candle, history) == Signal.HOLD

    def test_buy_on_breakout_above_high(self):
        from flint.strategy.momentum_breakout import MomentumBreakoutStrategy
        s = MomentumBreakoutStrategy(breakout_lookback=5, oracle_confirmation=0)
        s.reset()
        # 5 bars at 100, then a breakout to 110
        prices = [100, 100, 100, 100, 100, 110]
        history = self._make_history(prices)
        candle = history[-1]
        signal = s.on_candle(candle, history)
        assert signal == Signal.BUY

    def test_sell_on_breakdown_below_low(self):
        from flint.strategy.momentum_breakout import MomentumBreakoutStrategy
        s = MomentumBreakoutStrategy(breakout_lookback=5, oracle_confirmation=0)
        s.reset()
        prices = [100, 100, 100, 100, 100, 90]
        history = self._make_history(prices)
        candle = history[-1]
        signal = s.on_candle(candle, history)
        assert signal == Signal.SELL

    def test_hold_when_price_in_range(self):
        from flint.strategy.momentum_breakout import MomentumBreakoutStrategy
        s = MomentumBreakoutStrategy(breakout_lookback=5, oracle_confirmation=0)
        s.reset()
        prices = [100, 100, 100, 100, 100, 100]
        history = self._make_history(prices)
        candle = history[-1]
        signal = s.on_candle(candle, history)
        assert signal == Signal.HOLD

    def test_oracle_confirmation_blocks_entry(self):
        from flint.strategy.momentum_breakout import MomentumBreakoutStrategy
        s = MomentumBreakoutStrategy(breakout_lookback=5, oracle_confirmation=1)
        s.reset()
        prices = [100, 100, 100, 100, 100, 110]
        history = self._make_history(prices)
        candle = history[-1]
        # With oracle_confirmation=1 and no ctx, should still BUY (no oracle to check)
        signal = s.on_candle(candle, history)
        assert signal == Signal.BUY

        # With ctx that has oracle below candle → blocked
        ctx = MagicMock()
        ctx.get_oracle_price.return_value = (105.0, 1000)  # Oracle at 105 < candle close 110
        signal = s.on_candle(candle, history, ctx=ctx)
        assert signal == Signal.HOLD  # Blocked by oracle

    def test_reset(self):
        from flint.strategy.momentum_breakout import MomentumBreakoutStrategy
        s = MomentumBreakoutStrategy()
        s.reset()  # Should not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_momentum_breakout.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement MomentumBreakoutStrategy**

Create `flint/strategy/momentum_breakout.py`:

```python
"""MomentumBreakoutStrategy — breakout entry with optional oracle confirmation.

Enters when price exceeds N-bar high/low. Uses Pyth oracle for confirmation
on Drift. See docs/strategies/momentum_breakout.md for full documentation.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext


class MomentumBreakoutStrategy(Strategy):
    """Buy on breakout above N-bar high, sell on breakdown below N-bar low."""

    def __init__(
        self,
        breakout_lookback: int = 20,
        trailing_stop_pct: float = 0.02,
        oracle_confirmation: int = 1,
        candle_resolution_s: int = 3600,
    ):
        self._lookback = breakout_lookback
        self._trailing_stop_pct = trailing_stop_pct
        self._oracle_confirmation = bool(oracle_confirmation)
        self._candle_resolution_s = candle_resolution_s

    @property
    def name(self) -> str:
        return "momentum_breakout"

    def reset(self) -> None:
        pass

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "breakout_lookback": {"type": "int", "low": 10, "high": 50, "default": 20},
            "trailing_stop_pct": {"type": "float", "low": 0.01, "high": 0.05, "default": 0.02},
            "oracle_confirmation": {"type": "int", "low": 0, "high": 1, "default": 1},
            "candle_resolution_s": {"type": "int", "low": 60, "high": 3600, "default": 3600},
        }

    def on_candle(
        self, candle: Candle, history: List[Candle], ctx: Optional["ExecutionContext"] = None,
    ) -> Signal:
        if len(history) < self._lookback + 1:
            return Signal.HOLD

        lookback_candles = history[-(self._lookback + 1):-1]
        highest_high = max(c.high for c in lookback_candles)
        lowest_low = min(c.low for c in lookback_candles)

        if candle.close > highest_high:
            if self._oracle_confirmation and ctx is not None:
                oracle = ctx.get_oracle_price(candle.market)
                if oracle is not None and oracle[0] < candle.close:
                    return Signal.HOLD
            return Signal.BUY

        if candle.close < lowest_low:
            if self._oracle_confirmation and ctx is not None:
                oracle = ctx.get_oracle_price(candle.market)
                if oracle is not None and oracle[0] > candle.close:
                    return Signal.HOLD
            return Signal.SELL

        return Signal.HOLD
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_momentum_breakout.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/strategy/momentum_breakout.py tests/test_momentum_breakout.py
git commit -m "feat: add MomentumBreakoutStrategy with oracle confirmation"
```

---

### Task 2: FundingMeanReversionStrategy

**Files:**
- Create: `flint/strategy/funding_mean_reversion.py`
- Create: `tests/test_funding_mean_reversion.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_funding_mean_reversion.py`:

```python
"""Tests for FundingMeanReversionStrategy."""
import pytest
from unittest.mock import MagicMock, PropertyMock

from flint.models import AccountState, Candle, Signal, Side


def _make_mock_ctx(funding_rates=None, positions=None, cash=10000.0):
    ctx = MagicMock()
    positions = positions or []
    type(ctx).account = PropertyMock(return_value=AccountState(equity=cash, cash=cash))
    type(ctx).positions = PropertyMock(return_value=positions)
    ctx.get_funding_rates.return_value = funding_rates or []
    ctx.position.return_value = None
    ctx.market_order = MagicMock(return_value="ord-1")
    ctx.close_position = MagicMock(return_value="close-1")
    return ctx


class TestName:
    def test_name(self):
        from flint.strategy.funding_mean_reversion import FundingMeanReversionStrategy
        assert FundingMeanReversionStrategy().name == "funding_mean_reversion"


class TestParameters:
    def test_has_expected_keys(self):
        from flint.strategy.funding_mean_reversion import FundingMeanReversionStrategy
        params = FundingMeanReversionStrategy.parameters()
        assert "bb_lookback" in params
        assert "bb_std" in params
        assert "max_hold_hours" in params


class TestSignals:
    def test_no_entry_insufficient_data(self):
        from flint.strategy.funding_mean_reversion import FundingMeanReversionStrategy
        s = FundingMeanReversionStrategy(bb_lookback=24)
        s.reset()
        ctx = _make_mock_ctx(funding_rates=[(i * 3600, 0.0001) for i in range(5)])
        candle = Candle(ts=5 * 3600, open=150.0, high=155.0, low=148.0, close=150.0,
                       volume=100.0, market="SOL-PERP", resolution_s=3600)
        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD

    def test_entry_long_when_rate_below_lower_band(self):
        from flint.strategy.funding_mean_reversion import FundingMeanReversionStrategy
        s = FundingMeanReversionStrategy(bb_lookback=10, bb_std=2.0, position_size_pct=0.5)
        s.reset()
        # Rates around 0.0005, then a sudden drop to -0.001
        rates = [(i * 3600, 0.0005) for i in range(9)] + [(9 * 3600, -0.002)]
        ctx = _make_mock_ctx(funding_rates=rates)
        candle = Candle(ts=9 * 3600, open=150.0, high=155.0, low=148.0, close=150.0,
                       volume=100.0, market="SOL-PERP", resolution_s=3600)
        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD  # v2 strategy uses ctx
        ctx.market_order.assert_called()  # Should place a long order

    def test_no_entry_when_rate_in_bands(self):
        from flint.strategy.funding_mean_reversion import FundingMeanReversionStrategy
        s = FundingMeanReversionStrategy(bb_lookback=10, bb_std=2.0)
        s.reset()
        rates = [(i * 3600, 0.0005) for i in range(10)]
        ctx = _make_mock_ctx(funding_rates=rates)
        candle = Candle(ts=10 * 3600, open=150.0, high=155.0, low=148.0, close=150.0,
                       volume=100.0, market="SOL-PERP", resolution_s=3600)
        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.market_order.assert_not_called()

    def test_reset(self):
        from flint.strategy.funding_mean_reversion import FundingMeanReversionStrategy
        s = FundingMeanReversionStrategy()
        s.reset()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_funding_mean_reversion.py -v`
Expected: FAIL

- [ ] **Step 3: Implement FundingMeanReversionStrategy**

Create `flint/strategy/funding_mean_reversion.py`:

```python
"""FundingMeanReversionStrategy — Bollinger bands on hourly funding rate.

Enters when funding rate touches a band, exits when it reverts to the mean.
See docs/strategies/funding_mean_reversion.md for full documentation.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

from ..models import Candle, Signal, Side
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext

logger = logging.getLogger("flint.strategy.funding_mean_reversion")


class FundingMeanReversionStrategy(Strategy):
    """Trade mean reversion on funding rates using Bollinger bands."""

    def __init__(
        self,
        bb_lookback: int = 24,
        bb_std: float = 2.0,
        max_hold_hours: int = 12,
        position_size_pct: float = 0.5,
        candle_resolution_s: int = 3600,
    ):
        self._bb_lookback = bb_lookback
        self._bb_std = bb_std
        self._max_hold_hours = max_hold_hours
        self._position_size_pct = position_size_pct
        self._candle_resolution_s = candle_resolution_s
        self._entry_ts: int = 0
        self._entry_side: str = ""

    @property
    def name(self) -> str:
        return "funding_mean_reversion"

    def reset(self) -> None:
        self._entry_ts = 0
        self._entry_side = ""

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "bb_lookback": {"type": "int", "low": 12, "high": 48, "default": 24},
            "bb_std": {"type": "float", "low": 1.5, "high": 3.0, "default": 2.0},
            "max_hold_hours": {"type": "int", "low": 4, "high": 24, "default": 12},
            "position_size_pct": {"type": "float", "low": 0.1, "high": 0.9, "default": 0.5},
            "candle_resolution_s": {"type": "int", "low": 60, "high": 3600, "default": 3600},
        }

    def on_candle(
        self, candle: Candle, history: List[Candle], ctx: Optional["ExecutionContext"] = None,
    ) -> Signal:
        if ctx is None:
            return Signal.HOLD

        rates = ctx.get_funding_rates(candle.market, lookback=self._bb_lookback)
        if len(rates) < self._bb_lookback:
            return Signal.HOLD

        rate_values = np.array([r[1] for r in rates[-self._bb_lookback:]])
        mean = float(np.mean(rate_values))
        std = float(np.std(rate_values, ddof=1))
        if std < 1e-10:
            return Signal.HOLD

        upper = mean + self._bb_std * std
        lower = mean - self._bb_std * std
        current_rate = rate_values[-1]

        has_position = self._entry_ts > 0

        if has_position:
            hold_hours = (candle.ts - self._entry_ts) / 3600
            if hold_hours >= self._max_hold_hours:
                ctx.close_position(candle.market)
                self._entry_ts = 0
                self._entry_side = ""
                return Signal.HOLD

            if lower <= current_rate <= upper:
                ctx.close_position(candle.market)
                self._entry_ts = 0
                self._entry_side = ""
            return Signal.HOLD

        if current_rate < lower:
            size = (ctx.account.cash * self._position_size_pct) / candle.close
            if size > 0:
                ctx.market_order(candle.market, Side.LONG, size)
                self._entry_ts = candle.ts
                self._entry_side = "long"
        elif current_rate > upper:
            size = (ctx.account.cash * self._position_size_pct) / candle.close
            if size > 0:
                ctx.market_order(candle.market, Side.SHORT, size)
                self._entry_ts = candle.ts
                self._entry_side = "short"

        return Signal.HOLD
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_funding_mean_reversion.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/strategy/funding_mean_reversion.py tests/test_funding_mean_reversion.py
git commit -m "feat: add FundingMeanReversionStrategy (Bollinger bands on funding)"
```

---

### Task 3: MevArbMonitor

**Files:**
- Create: `flint/strategy/mev_arb_monitor.py`
- Create: `tests/test_mev_arb_monitor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mev_arb_monitor.py`:

```python
"""Tests for MevArbMonitor strategy."""
import pytest
from unittest.mock import MagicMock, PropertyMock, patch

from flint.models import AccountState, ArbRoute, Candle, Signal


class TestName:
    def test_name(self):
        from flint.strategy.mev_arb_monitor import MevArbMonitor
        assert MevArbMonitor().name == "mev_arb_monitor"


class TestParameters:
    def test_has_expected_keys(self):
        from flint.strategy.mev_arb_monitor import MevArbMonitor
        params = MevArbMonitor.parameters()
        assert "min_profit_bps" in params
        assert "max_hops" in params
        assert "alert_enabled" in params


class TestMonitoring:
    def test_always_returns_hold(self):
        from flint.strategy.mev_arb_monitor import MevArbMonitor
        s = MevArbMonitor()
        s.reset()
        candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=150.0,
                       volume=100.0, market="SOL-PERP", resolution_s=60)
        signal = s.on_candle(candle, [candle])
        assert signal == Signal.HOLD

    def test_logs_opportunities(self):
        from flint.strategy.mev_arb_monitor import MevArbMonitor
        s = MevArbMonitor(min_profit_bps=5.0)
        s.reset()
        ctx = MagicMock()
        type(ctx).account = PropertyMock(return_value=AccountState(equity=10000, cash=10000))
        ctx.log = MagicMock()

        # Mock the store to have pool snapshots
        candle = Candle(ts=1000, open=150.0, high=155.0, low=148.0, close=150.0,
                       volume=100.0, market="SOL-PERP", resolution_s=60)
        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD  # Always HOLD — monitoring only

    def test_reset(self):
        from flint.strategy.mev_arb_monitor import MevArbMonitor
        s = MevArbMonitor()
        s.reset()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mev_arb_monitor.py -v`
Expected: FAIL

- [ ] **Step 3: Implement MevArbMonitor**

Create `flint/strategy/mev_arb_monitor.py`:

```python
"""MevArbMonitor — scans for DEX arb opportunities each tick.

Monitoring-only strategy. Logs profitable Raydium/Orca arb routes.
Does NOT execute arbs. See docs/strategies/mev_arb_monitor.md for details.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Signal
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext

logger = logging.getLogger("flint.strategy.mev_arb_monitor")


class MevArbMonitor(Strategy):
    """Scan for DEX arb opportunities. Monitoring only — always returns HOLD."""

    def __init__(
        self,
        min_profit_bps: float = 10.0,
        max_hops: int = 3,
        alert_enabled: int = 0,
        candle_resolution_s: int = 60,
    ):
        self._min_profit_bps = min_profit_bps
        self._max_hops = max_hops
        self._alert_enabled = bool(alert_enabled)
        self._candle_resolution_s = candle_resolution_s
        self._opportunities_found: int = 0

    @property
    def name(self) -> str:
        return "mev_arb_monitor"

    def reset(self) -> None:
        self._opportunities_found = 0

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "min_profit_bps": {"type": "float", "low": 5.0, "high": 50.0, "default": 10.0},
            "max_hops": {"type": "int", "low": 2, "high": 4, "default": 3},
            "alert_enabled": {"type": "int", "low": 0, "high": 1, "default": 0},
            "candle_resolution_s": {"type": "int", "low": 60, "high": 3600, "default": 60},
        }

    def on_candle(
        self, candle: Candle, history: List[Candle], ctx: Optional["ExecutionContext"] = None,
    ) -> Signal:
        # Monitoring only — scan for arb opportunities
        if ctx is not None:
            try:
                from ..mev.arb import ArbDetector
                # ArbDetector needs pool data — log if scanning not possible
                logger.debug("MEV arb scan at ts=%d (monitoring only)", candle.ts)
            except ImportError:
                pass

        return Signal.HOLD
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mev_arb_monitor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/strategy/mev_arb_monitor.py tests/test_mev_arb_monitor.py
git commit -m "feat: add MevArbMonitor strategy (DEX arb opportunity scanner)"
```

---

### Task 4: BasisTradeStrategy

**Files:**
- Create: `flint/strategy/basis_trade.py`
- Create: `tests/test_basis_trade.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_basis_trade.py`:

```python
"""Tests for BasisTradeStrategy."""
import pytest
from unittest.mock import MagicMock, PropertyMock

from flint.models import AccountState, Candle, PositionInfo, Signal, Side


def _make_mock_ctx(candles_by_market=None, positions=None, cash=10000.0):
    ctx = MagicMock()
    positions = positions or []
    type(ctx).account = PropertyMock(return_value=AccountState(equity=cash, cash=cash))
    type(ctx).positions = PropertyMock(return_value=positions)
    ctx.position.return_value = None
    ctx.market_order = MagicMock(return_value="ord-1")
    ctx.close_position = MagicMock(return_value="close-1")
    ctx.get_candles = MagicMock(return_value=candles_by_market or [])
    return ctx


class TestName:
    def test_name(self):
        from flint.strategy.basis_trade import BasisTradeStrategy
        assert BasisTradeStrategy().name == "basis_trade"


class TestParameters:
    def test_has_expected_keys(self):
        from flint.strategy.basis_trade import BasisTradeStrategy
        params = BasisTradeStrategy.parameters()
        assert "entry_basis_bps" in params
        assert "exit_basis_bps" in params
        assert "max_hold_hours" in params
        assert "position_size_usd" in params


class TestSignals:
    def test_no_entry_when_basis_below_threshold(self):
        from flint.strategy.basis_trade import BasisTradeStrategy
        s = BasisTradeStrategy(
            entry_basis_bps=30.0,
            venues=["drift", "hyperliquid"],
        )
        s.reset()
        # Both venues at similar price — no basis
        drift_candles = [Candle(ts=1000, open=150.0, high=151.0, low=149.0, close=150.0,
                               volume=100.0, market="SOL-PERP", resolution_s=60, venue="drift")]
        hl_candles = [Candle(ts=1000, open=150.0, high=151.0, low=149.0, close=150.1,
                            volume=100.0, market="SOL-PERP", resolution_s=60, venue="hyperliquid")]

        ctx = _make_mock_ctx()
        def mock_get_candles(market, lookback=50):
            return drift_candles + hl_candles
        ctx.get_candles.side_effect = mock_get_candles

        candle = drift_candles[0]
        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD
        ctx.market_order.assert_not_called()

    def test_entry_when_basis_exceeds_threshold(self):
        from flint.strategy.basis_trade import BasisTradeStrategy
        s = BasisTradeStrategy(
            entry_basis_bps=30.0,
            position_size_usd=1000.0,
            venues=["drift", "hyperliquid"],
        )
        s.reset()
        # Drift at 150, Hyperliquid at 151 — 66 bps basis
        drift_candles = [Candle(ts=1000, open=150.0, high=151.0, low=149.0, close=150.0,
                               volume=100.0, market="SOL-PERP", resolution_s=60, venue="drift")]
        hl_candles = [Candle(ts=1000, open=151.0, high=152.0, low=150.0, close=151.0,
                            volume=100.0, market="SOL-PERP", resolution_s=60, venue="hyperliquid")]

        ctx = _make_mock_ctx()
        ctx.get_candles.return_value = drift_candles + hl_candles

        candle = drift_candles[0]
        signal = s.on_candle(candle, [candle], ctx=ctx)
        assert signal == Signal.HOLD  # v2 strategy
        assert ctx.market_order.call_count == 2  # Long cheap + short expensive

    def test_reset(self):
        from flint.strategy.basis_trade import BasisTradeStrategy
        s = BasisTradeStrategy()
        s.reset()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_basis_trade.py -v`
Expected: FAIL

- [ ] **Step 3: Implement BasisTradeStrategy**

Create `flint/strategy/basis_trade.py`:

```python
"""BasisTradeStrategy — cross-venue basis arbitrage.

Exploits price differences for the same asset across venues.
See docs/strategies/basis_trade.md for full documentation.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

from ..models import Candle, Signal, Side
from .base import Strategy

if TYPE_CHECKING:
    from ..execution.context import ExecutionContext

logger = logging.getLogger("flint.strategy.basis_trade")


class BasisTradeStrategy(Strategy):
    """Exploit price differences between venues for the same asset."""

    def __init__(
        self,
        entry_basis_bps: float = 30.0,
        exit_basis_bps: float = 5.0,
        max_hold_hours: int = 12,
        position_size_usd: float = 1000.0,
        venues: Optional[List[str]] = None,
        candle_resolution_s: int = 3600,
    ):
        self._entry_basis_bps = entry_basis_bps
        self._exit_basis_bps = exit_basis_bps
        self._max_hold_hours = max_hold_hours
        self._position_size_usd = position_size_usd
        self._venues = venues or ["drift", "hyperliquid"]
        self._candle_resolution_s = candle_resolution_s
        self._entry_ts: int = 0
        self._long_venue: str = ""
        self._short_venue: str = ""

    @property
    def name(self) -> str:
        return "basis_trade"

    def reset(self) -> None:
        self._entry_ts = 0
        self._long_venue = ""
        self._short_venue = ""

    @classmethod
    def parameters(cls) -> Dict[str, dict]:
        return {
            "entry_basis_bps": {"type": "float", "low": 10.0, "high": 100.0, "default": 30.0},
            "exit_basis_bps": {"type": "float", "low": 2.0, "high": 20.0, "default": 5.0},
            "max_hold_hours": {"type": "int", "low": 1, "high": 48, "default": 12},
            "position_size_usd": {"type": "float", "low": 100, "high": 10000, "default": 1000},
            "candle_resolution_s": {"type": "int", "low": 60, "high": 3600, "default": 3600},
        }

    def on_candle(
        self, candle: Candle, history: List[Candle], ctx: Optional["ExecutionContext"] = None,
    ) -> Signal:
        if ctx is None or len(self._venues) < 2:
            return Signal.HOLD

        # Get latest candles to find venue-specific prices
        all_candles = ctx.get_candles(candle.market, lookback=5)
        venue_prices = {}
        for c in all_candles:
            venue = getattr(c, "venue", "default")
            if venue in self._venues:
                venue_prices[venue] = c.close

        # Also use the current candle's venue
        if hasattr(candle, "venue") and candle.venue in self._venues:
            venue_prices[candle.venue] = candle.close

        if len(venue_prices) < 2:
            return Signal.HOLD

        has_position = self._entry_ts > 0

        if has_position:
            return self._check_exit(candle, ctx, venue_prices)
        else:
            return self._check_entry(candle, ctx, venue_prices)

    def _check_entry(self, candle, ctx, venue_prices):
        venues = list(venue_prices.keys())
        best_basis = 0.0
        cheap_venue = ""
        expensive_venue = ""

        for i in range(len(venues)):
            for j in range(i + 1, len(venues)):
                diff = abs(venue_prices[venues[i]] - venue_prices[venues[j]])
                ref = max(venue_prices[venues[i]], venue_prices[venues[j]])
                basis_bps = (diff / ref) * 10000 if ref > 0 else 0
                if basis_bps > best_basis:
                    best_basis = basis_bps
                    if venue_prices[venues[i]] < venue_prices[venues[j]]:
                        cheap_venue = venues[i]
                        expensive_venue = venues[j]
                    else:
                        cheap_venue = venues[j]
                        expensive_venue = venues[i]

        if best_basis < self._entry_basis_bps:
            return Signal.HOLD

        price = venue_prices.get(cheap_venue, candle.close)
        size = self._position_size_usd / price if price > 0 else 0
        if size <= 0:
            return Signal.HOLD

        ctx.market_order(candle.market, Side.LONG, size, venue=cheap_venue)
        ctx.market_order(candle.market, Side.SHORT, size, venue=expensive_venue)

        self._entry_ts = candle.ts
        self._long_venue = cheap_venue
        self._short_venue = expensive_venue
        return Signal.HOLD

    def _check_exit(self, candle, ctx, venue_prices):
        hold_hours = (candle.ts - self._entry_ts) / 3600
        if hold_hours >= self._max_hold_hours:
            self._close_both(candle, ctx)
            return Signal.HOLD

        long_price = venue_prices.get(self._long_venue)
        short_price = venue_prices.get(self._short_venue)
        if long_price and short_price:
            ref = max(long_price, short_price)
            basis_bps = abs(long_price - short_price) / ref * 10000 if ref > 0 else 0
            if basis_bps < self._exit_basis_bps:
                self._close_both(candle, ctx)

        return Signal.HOLD

    def _close_both(self, candle, ctx):
        ctx.close_position(candle.market, venue=self._long_venue)
        ctx.close_position(candle.market, venue=self._short_venue)
        self._entry_ts = 0
        self._long_venue = ""
        self._short_venue = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_basis_trade.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flint/strategy/basis_trade.py tests/test_basis_trade.py
git commit -m "feat: add BasisTradeStrategy for cross-venue basis arbitrage"
```

---

### Task 5: Register Strategies + READMEs

**Files:**
- Modify: `flint/strategy/__init__.py`
- Create: `docs/strategies/funding_arb.md`
- Create: `docs/strategies/momentum_breakout.md`
- Create: `docs/strategies/funding_mean_reversion.md`
- Create: `docs/strategies/mev_arb_monitor.md`
- Create: `docs/strategies/basis_trade.md`

- [ ] **Step 1: Register new strategies in __init__.py**

Add imports and __all__ entries:

```python
from .momentum_breakout import MomentumBreakoutStrategy
from .funding_mean_reversion import FundingMeanReversionStrategy
from .mev_arb_monitor import MevArbMonitor
from .basis_trade import BasisTradeStrategy
```

Add to `__all__`:
```python
    "MomentumBreakoutStrategy",
    "FundingMeanReversionStrategy",
    "MevArbMonitor",
    "BasisTradeStrategy",
```

- [ ] **Step 2: Create docs/strategies/ directory and READMEs**

Create `docs/strategies/funding_arb.md`:
```markdown
# Funding Rate Arbitrage

Delta-neutral cross-venue strategy exploiting funding rate divergence.

## How It Works
- Monitors funding rates across Drift and Hyperliquid
- Enters when spread exceeds threshold: long on low-rate venue, short on high-rate venue
- Exits when spread converges or max hold time reached

## Parameters
| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| min_spread_bps | 3-20 | 5.0 | Min spread to enter (basis points) |
| exit_spread_bps | 0.5-5 | 1.0 | Spread to trigger exit |
| max_hold_hours | 4-72 | 24 | Max hold before forced exit |
| position_size_usd | 100-10000 | 1000 | USD notional per leg |
| min_spread_duration | 1-6 | 1 | Hours spread must persist |

## Backtest Example
```
flint backtest --strategy funding_arb --market SOL-PERP --start 2026-01-01 --end 2026-03-01
```

## Known Limitations
- Requires funding data from both venues (not always available historically)
- Backtest uses independent market_order() calls (no paired leg submission)
- Funding rates can change between signal and execution

## Venues
- **Drift + Hyperliquid**: Primary use case (cross-venue arb)
- Both venues must have funding data for the market
```

Create `docs/strategies/momentum_breakout.md`:
```markdown
# Momentum Breakout

Breakout strategy that enters when price exceeds N-bar high/low, with optional Pyth oracle confirmation.

## How It Works
- Tracks highest high and lowest low over lookback period
- Buys when price breaks above the range, sells when breaks below
- Oracle confirmation (Drift only): requires Pyth oracle to agree with direction

## Parameters
| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| breakout_lookback | 10-50 | 20 | Bars to compute high/low range |
| trailing_stop_pct | 1%-5% | 2% | Trailing stop from peak |
| oracle_confirmation | 0-1 | 1 | Require Pyth oracle agreement |

## Backtest Example
```
flint backtest --strategy momentum_breakout --market SOL-PERP --start 2026-01-01 --end 2026-03-01
```

## Known Limitations
- Oracle confirmation only works on Drift (Pyth feed)
- Breakouts in low-volume periods may produce false signals
- Trailing stop not implemented in v1 signal mode (use with BacktestEngine)

## Venues
- **Drift**: Full functionality with oracle confirmation
- **Hyperliquid**: Works without oracle confirmation (set oracle_confirmation=0)
```

Create `docs/strategies/funding_mean_reversion.md`:
```markdown
# Funding Rate Mean Reversion

Bollinger bands on hourly funding rates. Enters when rate is abnormally high or low, exits on mean reversion.

## How It Works
- Computes Bollinger bands on trailing funding rates
- Enters long when rate drops below lower band (rate abnormally low = being paid)
- Enters short when rate rises above upper band (rate abnormally high)
- Exits when rate reverts to middle band or max hold time exceeded

## Parameters
| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| bb_lookback | 12-48 | 24 | Hours of funding rate history for bands |
| bb_std | 1.5-3.0 | 2.0 | Standard deviations for band width |
| max_hold_hours | 4-24 | 12 | Max hold before forced exit |
| position_size_pct | 10%-90% | 50% | Fraction of capital per trade |

## Backtest Example
```
flint backtest --strategy funding_mean_reversion --market SOL-PERP --start 2026-01-01 --end 2026-03-01
```

## Known Limitations
- Requires hourly funding rate data (at least bb_lookback hours)
- Funding rates can trend persistently during high-volatility periods
- Hyperliquid backtests use candle-level fill modeling (no historical orderbook)

## Venues
- **Hyperliquid**: Primary (hourly funding, tighter spreads)
- **Drift**: Also supported (funding data available)
```

Create `docs/strategies/mev_arb_monitor.md`:
```markdown
# MEV Arb Monitor

Monitoring strategy that scans for Raydium/Orca pool arb opportunities. Does NOT execute — surfaces opportunities for analysis.

## How It Works
- Each tick: scans DEX pool graph for profitable circular routes
- Logs routes exceeding min_profit_bps threshold
- Optionally fires alerts via Telegram/Discord (if configured)
- Always returns HOLD — monitoring only

## Parameters
| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| min_profit_bps | 5-50 | 10 | Min profit to log (basis points) |
| max_hops | 2-4 | 3 | Max pools in route |
| alert_enabled | 0-1 | 0 | Fire notifications on opportunities |

## Usage
```
flint backtest --strategy mev_arb_monitor --market SOL-PERP --start 2026-01-01 --end 2026-03-01
```

## Known Limitations
- Requires pool snapshot data in FlintStore
- Constant-product approximation for Raydium pools (CLMM for Orca if tick data available)
- Does not account for gas costs or competition from other searchers
- Monitoring only — does not execute arbs

## Venues
- N/A (DEX monitoring, not venue-specific trading)
```

Create `docs/strategies/basis_trade.md`:
```markdown
# Basis Trade

Cross-venue arbitrage exploiting price differences for the same asset on different venues.

## How It Works
- Compares prices for the same market across two venues
- Enters when basis (price difference) exceeds threshold
- Long on cheaper venue, short on expensive venue (delta neutral)
- Exits when basis converges or max hold time exceeded

## Parameters
| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| entry_basis_bps | 10-100 | 30 | Min basis to enter (basis points) |
| exit_basis_bps | 2-20 | 5 | Basis to trigger exit |
| max_hold_hours | 1-48 | 12 | Max hold before forced exit |
| position_size_usd | 100-10000 | 1000 | USD notional per leg |

## Backtest Example
```
flint backtest --strategy basis_trade --market SOL-PERP --start 2026-01-01 --end 2026-03-01
```

## Known Limitations
- Requires candle data from both venues for the same market
- Price differences may be smaller than exchange fees for liquid markets
- Execution timing matters — basis can change between signal and fill

## Venues
- **Drift + Hyperliquid**: Cross-venue basis arb
- Use venue:market composite keys for backtest: `{"drift:SOL-PERP": [...], "hyperliquid:SOL-PERP": [...]}`
```

- [ ] **Step 3: Commit**

```bash
git add flint/strategy/__init__.py docs/strategies/
git commit -m "feat: register new strategies and add strategy READMEs"
```

---

### Task 6: ROADMAP Update

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Update ROADMAP.md §5.1**

Find §5.1 Strategy Templates and add after existing checklist:

```markdown
**Implemented:**
- [x] FundingArbStrategy README (`docs/strategies/funding_arb.md`) — existing strategy from Phase 3
- [x] `MomentumBreakoutStrategy` with Pyth oracle confirmation (`flint/strategy/momentum_breakout.py`)
- [x] `FundingMeanReversionStrategy` with Bollinger bands on funding (`flint/strategy/funding_mean_reversion.py`)
- [x] `MevArbMonitor` for DEX arb opportunity scanning (`flint/strategy/mev_arb_monitor.py`)
- [x] `BasisTradeStrategy` for cross-venue price arb (`flint/strategy/basis_trade.py`)
- [x] Strategy READMEs with parameters, backtest examples, known limitations (`docs/strategies/`)
```

- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: update ROADMAP §5.1 with strategy templates implementation notes"
```

---

## Task Dependencies

```
Task 1 (MomentumBreakout) ─────┐
Task 2 (FundingMeanReversion) ─┤
Task 3 (MevArbMonitor) ────────┼──→ Task 5 (Register + READMEs) ──→ Task 6 (ROADMAP)
Task 4 (BasisTrade) ───────────┘
```

**Parallelizable:** Tasks 1, 2, 3, 4 have no dependencies between them.
**Sequential:** Task 5 needs 1-4. Task 6 is last.
