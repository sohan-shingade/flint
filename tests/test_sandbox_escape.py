"""Sandbox escape tests — Phase 2 T2.4.c.

Each hostile payload must be rejected either at AST validation time
(flint/strategy/loader.py whitelist) or enforced at runtime
(flint/strategy/sandbox.py wall-clock / memory limits).

Slow tests (infinite loop, memory bomb) use shorter limits so CI stays fast.
"""
from __future__ import annotations

import os
import sys

import pytest

from flint.strategy.loader import StrategyLoadError, load_user_strategy, validate_strategy_code
from flint.strategy.sandbox import (
    SandboxConfig,
    StrategyExecutionError,
    StrategyMemoryError,
    StrategyTimeoutError,
    run_strategy_in_sandbox,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_strategy(body: str) -> str:
    """Wrap a hostile body inside a valid Strategy skeleton so AST gates
    decide on the body, not on the class shape."""
    return f'''
from flint.strategy.base import Strategy
from flint.models import Signal


class HostileStrategy(Strategy):
    @property
    def name(self):
        return "Hostile"

    def reset(self):
        pass

    def on_candle(self, candle, history, ctx=None):
{body}
        return Signal.HOLD
'''


def _candles(n=5):
    from flint.models import Candle
    return [
        Candle(ts=i * 60, open=100, high=101, low=99, close=100.5,
               volume=1000, market="SOL-PERP", resolution_s=60)
        for i in range(n)
    ]


def _should_reject(code: str, expected_substring: str = ""):
    """Assert loader rejects code at AST/validation time."""
    with pytest.raises(StrategyLoadError) as exc:
        load_user_strategy(code)
    if expected_substring:
        assert expected_substring.lower() in str(exc.value).lower()


# ---------------------------------------------------------------------------
# 1-7. AST-whitelist rejections (fast — never reach sandbox)
# ---------------------------------------------------------------------------

class TestASTWhitelistRejections:
    def test_os_system_blocked_by_import(self):
        code = _minimal_strategy('        import os\n        os.system("ls")\n')
        _should_reject(code, "blocked import")

    def test_subprocess_blocked_by_import(self):
        code = _minimal_strategy('        import subprocess\n        subprocess.Popen(["ls"])\n')
        _should_reject(code, "blocked import")

    def test_open_builtin_blocked(self):
        code = _minimal_strategy('        open("/etc/passwd")\n')
        _should_reject(code, "open")

    def test_eval_blocked(self):
        code = _minimal_strategy('        eval("1+1")\n')
        _should_reject(code, "eval")

    def test_exec_blocked(self):
        code = _minimal_strategy('        exec("print(1)")\n')
        _should_reject(code, "exec")

    def test_dunder_import_blocked(self):
        code = _minimal_strategy('        __import__("os")\n')
        _should_reject(code, "__import__")

    def test_file_write_attribute_blocked(self):
        # `pathlib.Path(...).unlink()` references blocked attribute `unlink`
        code = _minimal_strategy(
            '        x = 1\n        y = x.unlink()\n')
        _should_reject(code, "unlink")

    def test_socket_import_blocked(self):
        code = _minimal_strategy(
            '        import socket\n        s = socket.socket()\n')
        _should_reject(code, "blocked import")


# ---------------------------------------------------------------------------
# 8. Nested-import escape — still caught because AST sees the import statement.
# ---------------------------------------------------------------------------

class TestNestedImportEscape:
    def test_numpy_then_os_via_module_attribute(self):
        # Pretend to be clever — won't help because AST finds the raw `import os`.
        code = _minimal_strategy(
            '        import numpy as np\n        import os\n')
        _should_reject(code, "blocked import")


# ---------------------------------------------------------------------------
# 9. Happy path — a benign strategy runs to completion in the sandbox.
# ---------------------------------------------------------------------------

def _benign_strategy() -> str:
    return '''
from flint.strategy.base import Strategy
from flint.models import Signal


class Benign(Strategy):
    @property
    def name(self):
        return "Benign"

    def reset(self):
        pass

    def on_candle(self, candle, history, ctx=None):
        return Signal.HOLD
'''


class TestSandboxHappyPath:
    def test_sandbox_runs_benign_strategy(self):
        result = run_strategy_in_sandbox(
            code=_benign_strategy(),
            candles=_candles(20),
            initial_capital=10_000.0,
            fee_rate=0.0005,
            config=SandboxConfig(timeout_s=30.0, memory_mb=512),
        )
        # No trades expected from HOLD-only strategy
        assert result.total_trades == 0
        assert result.total_pnl == 0.0


# ---------------------------------------------------------------------------
# 10. Infinite loop — wall-clock budget enforced.
# ---------------------------------------------------------------------------

def _infinite_loop_strategy() -> str:
    # Infinite loop inside on_candle — would hang an in-process runner.
    return '''
from flint.strategy.base import Strategy
from flint.models import Signal


class InfiniteLoop(Strategy):
    @property
    def name(self):
        return "InfiniteLoop"

    def reset(self):
        pass

    def on_candle(self, candle, history, ctx=None):
        i = 0
        while True:
            i += 1
        return Signal.HOLD
'''


class TestSandboxTimeout:
    def test_infinite_loop_times_out(self):
        """Wall-clock budget must kill an infinite loop. Short budget so
        CI doesn't wait 5 minutes."""
        with pytest.raises(StrategyTimeoutError):
            run_strategy_in_sandbox(
                code=_infinite_loop_strategy(),
                candles=_candles(5),
                config=SandboxConfig(timeout_s=3.0, memory_mb=512,
                                     kill_grace_s=1.0),
            )


# ---------------------------------------------------------------------------
# 11. Memory bomb — RLIMIT_AS enforced on Linux, best-effort elsewhere.
# ---------------------------------------------------------------------------

def _memory_bomb_strategy() -> str:
    # Try to allocate ~10GB of numpy zeros. RLIMIT_AS at 256MB should block.
    return '''
from flint.strategy.base import Strategy
from flint.models import Signal
import numpy


class MemoryBomb(Strategy):
    @property
    def name(self):
        return "MemoryBomb"

    def reset(self):
        pass

    def on_candle(self, candle, history, ctx=None):
        # 1.25e9 float64 = ~10GB
        _big = numpy.zeros(1_250_000_000)
        return Signal.HOLD
'''


class TestSandboxMemory:
    @pytest.mark.skipif(
        sys.platform != "linux",
        reason="RLIMIT_AS only reliably enforced on Linux; macOS uses "
               "demand-paging so np.zeros allocations don't commit RSS "
               "until touched. True memory sandboxing on macOS requires "
               "cgroups-equivalent — out of scope for Phase 2 MVP."
    )
    def test_memory_bomb_blocked(self):
        """Huge numpy allocation must be rejected either by
        StrategyMemoryError (clean MemoryError catch) or by the OS killing
        the child (StrategyExecutionError). Either is acceptable — the
        strategy MUST NOT succeed."""
        with pytest.raises((StrategyMemoryError, StrategyExecutionError)):
            run_strategy_in_sandbox(
                code=_memory_bomb_strategy(),
                candles=_candles(5),
                config=SandboxConfig(timeout_s=20.0, memory_mb=256,
                                     kill_grace_s=2.0),
            )


# ---------------------------------------------------------------------------
# 12. Sandbox surfaces child-side load errors cleanly
# ---------------------------------------------------------------------------

class TestSandboxErrorHandling:
    def test_no_strategy_class_surfaces_error(self):
        """Code without a Strategy subclass fails loader.load_user_strategy
        inside the child → StrategyExecutionError back to parent."""
        bad_code = "x = 1\n"
        with pytest.raises(StrategyExecutionError):
            run_strategy_in_sandbox(
                code=bad_code,
                candles=_candles(5),
                config=SandboxConfig(timeout_s=10.0, memory_mb=256),
            )
