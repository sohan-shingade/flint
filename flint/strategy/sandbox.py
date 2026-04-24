"""User-strategy sandbox — subprocess isolation with wall-clock + memory limits.

Phase 2 T2.4. Complements the existing AST-based import whitelist in loader.py:
the whitelist blocks dangerous static imports; this sandbox blocks runtime
resource abuse (infinite loops, memory bombs) that the whitelist can't catch.

Design
------
- `multiprocessing.Process` with the `spawn` start method (no parent-state
  leak; fresh interpreter per run).
- Child applies `resource.setrlimit(RLIMIT_AS)` best-effort (hard cap on
  address space; enforced on Linux, advisory on macOS).
- Parent waits `timeout_s` via `process.join`; kills on timeout.
- Results and exceptions marshal back through a `multiprocessing.Pipe`.

This is the path used for `strategies/user/*.py` and any code uploaded via
`/api/v1/user-strategies`. Built-in strategies under `flint/strategy/`
continue to run in-process (trusted).

Usage
-----
    from flint.strategy.sandbox import run_strategy_in_sandbox

    result = run_strategy_in_sandbox(
        code=user_code_str,
        candles=[c.__dict__ for c in candles],  # dicts for picklability
        initial_capital=10_000.0,
        fee_rate=0.0005,
        timeout_s=300,
        memory_mb=1024,
    )
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class StrategyTimeoutError(Exception):
    """Raised when a sandboxed strategy exceeds its wall-clock budget."""


class StrategyMemoryError(Exception):
    """Raised when a sandboxed strategy trips the memory limit."""


class StrategyExecutionError(Exception):
    """Raised when a sandboxed strategy crashes or exits without a result."""


# ---------------------------------------------------------------------------
# Child entry point
# ---------------------------------------------------------------------------

def _apply_memory_limit(memory_mb: int) -> None:
    """Best-effort RSS / address-space cap. Silent on platforms that don't
    support it — Linux and macOS support RLIMIT_AS; Windows does not."""
    if memory_mb <= 0:
        return
    try:
        import resource
    except ImportError:
        return  # Windows / unsupported
    cap = memory_mb * 1024 * 1024
    # Try RLIMIT_AS first (total virtual memory, hardest cap).
    for limit_name in ("RLIMIT_AS", "RLIMIT_DATA", "RLIMIT_RSS"):
        limit = getattr(resource, limit_name, None)
        if limit is None:
            continue
        try:
            resource.setrlimit(limit, (cap, cap))
            return
        except (ValueError, OSError):
            continue


def _child_run(code: str,
               candles_data: List[Dict[str, Any]],
               params: Optional[Dict[str, Any]],
               initial_capital: float,
               fee_rate: float,
               seed: Optional[int],
               memory_mb: int,
               conn) -> None:
    """Subprocess entry: load strategy, run backtest, marshal result.

    Anything raised becomes part of the payload so the parent can re-raise
    the right type instead of getting a generic subprocess error.
    """
    try:
        _apply_memory_limit(memory_mb)

        # Import inside the child so parent doesn't pay the cost when a run
        # times out before the child enters this path.
        from flint.backtest.engine import BacktestEngine
        from flint.models import Candle
        from flint.strategy.loader import load_user_strategy

        # Reconstruct candles from plain dicts (picklable through spawn).
        candles = [Candle(**d) for d in candles_data]

        strategy = load_user_strategy(code, params)
        engine = BacktestEngine(
            strategy=strategy,
            initial_capital=initial_capital,
            fee_rate=fee_rate,
            seed=seed,
        )
        result = engine.run(candles)
        conn.send(("ok", result))
    except MemoryError:
        conn.send(("memory", None))
    except Exception as e:
        # Stringify to avoid unpicklable types leaking through the pipe
        conn.send(("error", f"{type(e).__name__}: {e}"))
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Parent driver
# ---------------------------------------------------------------------------

@dataclass
class SandboxConfig:
    timeout_s: float = 300.0
    memory_mb: int = 1024
    kill_grace_s: float = 5.0


def _candles_to_dicts(candles: List[Any]) -> List[Dict[str, Any]]:
    """Convert Candle dataclass instances to plain dicts for spawn-safe
    transport. Accepts anything with the Candle attribute shape."""
    out = []
    for c in candles:
        if isinstance(c, dict):
            out.append(c)
            continue
        out.append({
            "ts": int(c.ts),
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume),
            "market": str(c.market),
            "resolution_s": int(c.resolution_s),
            "venue": str(getattr(c, "venue", "default")),
        })
    return out


def run_strategy_in_sandbox(
    code: str,
    candles: List[Any],
    initial_capital: float = 10_000.0,
    fee_rate: float = 0.0005,
    params: Optional[Dict[str, Any]] = None,
    seed: Optional[int] = None,
    config: Optional[SandboxConfig] = None,
):
    """Run user strategy code against `candles` inside an isolated subprocess.

    Raises
    ------
    StrategyTimeoutError: wall-clock budget exhausted
    StrategyMemoryError: RLIMIT_AS tripped
    StrategyLoadError: strategy code fails the AST whitelist
    StrategyExecutionError: child exited without producing a result
    Any other exception raised inside the strategy arrives as
        StrategyExecutionError with the child-side traceback inlined.
    """
    cfg = config or SandboxConfig()
    candles_data = _candles_to_dicts(candles)

    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)

    process = ctx.Process(
        target=_child_run,
        args=(code, candles_data, params or {}, initial_capital, fee_rate,
              seed, cfg.memory_mb, child_conn),
        daemon=True,
    )
    process.start()
    # Close our copy of the child end so recv() sees EOF if the child dies.
    child_conn.close()

    try:
        process.join(timeout=cfg.timeout_s)
        if process.is_alive():
            process.terminate()
            process.join(cfg.kill_grace_s)
            if process.is_alive():
                try:
                    process.kill()
                except Exception:
                    pass
                process.join(cfg.kill_grace_s)
            raise StrategyTimeoutError(
                f"Strategy exceeded wall-clock budget of {cfg.timeout_s}s"
            )

        if not parent_conn.poll():
            raise StrategyExecutionError(
                f"Strategy subprocess exited without result "
                f"(exit code {process.exitcode}); "
                "likely killed by the OS (memory/OOM) or crashed natively"
            )

        status, payload = parent_conn.recv()
        if status == "ok":
            return payload
        if status == "memory":
            raise StrategyMemoryError(
                f"Strategy exceeded memory limit of {cfg.memory_mb} MB"
            )
        if status == "error":
            # Re-raise as StrategyExecutionError so callers can distinguish
            # user-bug exceptions from sandbox-enforced limits.
            raise StrategyExecutionError(payload)
        raise StrategyExecutionError(f"unknown sandbox status {status!r}")
    finally:
        try:
            parent_conn.close()
        except Exception:
            pass
        if process.is_alive():
            process.kill()
