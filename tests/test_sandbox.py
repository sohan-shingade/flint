"""Sandbox skeleton: I/O, resource limits, and the expected-fail escape suite (§8.3).

The security tests encode the design thesis: **isolation is structural, not
syntactic**. Some assert the syntactic gate blocks the obvious path (denied
imports, removed builtins). The most important one lets a strategy *succeed* at
a `getattr`/`__subclasses__` escape to the `os` module — and proves it still
learns no secret, because the child's environment is empty by construction.
"""

from __future__ import annotations

import pytest

from flint.ports import ResourceQuota
from flint.strategy.sandbox import (
    SandboxTimeout,
    SandboxViolation,
    StrategyError,
    os_layer_available,
    run_strategy_sandboxed,
)

FAST = ResourceQuota(cpu_seconds=5, mem_bytes=2 * 1024**3, wall_seconds=15)


# --- I/O and the Arrow/dict pipe boundary ----------------------------------


def test_returns_dict_result():
    src = "def run(x):\n    return {'echo': x['a'] + 1}"
    assert run_strategy_sandboxed(src, "run", {"a": 41}, quota=FAST) == {"echo": 42}


def test_arrow_table_round_trips_and_is_usable_via_methods():
    import pyarrow as pa

    table = pa.table({"ts": [1, 2, 3], "close": [1.0, 2.0, 3.0]})
    # The strategy uses the Table's methods — it cannot import pyarrow (below).
    src = "def run(t):\n    return {'n': t.num_rows, 'cols': t.column_names}"
    assert run_strategy_sandboxed(src, "run", table, quota=FAST) == {
        "n": 3,
        "cols": ["ts", "close"],
    }


def test_default_entry_and_none_input():
    assert run_strategy_sandboxed("def run(x):\n    return x is None", quota=FAST) is True


# --- import allowlist (lint-grade UX; denied -> Strategy/ImportError) -------


@pytest.mark.parametrize(
    "module",
    ["os", "sys", "subprocess", "socket", "requests", "pickle", "importlib", "ctypes"],
)
def test_denied_imports_are_blocked(module):
    src = f"import {module}\ndef run(x):\n    return 1"
    with pytest.raises(StrategyError) as exc:
        run_strategy_sandboxed(src, "run", None, quota=FAST)
    assert exc.value.error_type == "ImportError"


@pytest.mark.parametrize(
    "module,expr",
    [
        ("math", "math.floor(3.7) == 3"),
        ("statistics", "statistics.mean([1, 2, 3]) == 2"),
        ("collections", "collections.Counter('aab')['a'] == 2"),
        ("itertools", "len(list(itertools.repeat(0, 4))) == 4"),
    ],
)
def test_allowed_imports_work(module, expr):
    src = f"import {module}\ndef run(x):\n    return {expr}"
    assert run_strategy_sandboxed(src, "run", None, quota=FAST) is True


# --- minimal __builtins__ ---------------------------------------------------


@pytest.mark.parametrize("call", ["open('/etc/passwd')", "eval('1')", "exec('1')", "compile('1','','eval')"])
def test_dangerous_builtins_are_absent(call):
    src = f"def run(x):\n    return {call}"
    with pytest.raises(StrategyError) as exc:
        run_strategy_sandboxed(src, "run", None, quota=FAST)
    assert exc.value.error_type == "NameError"


def test_safe_builtins_are_present():
    src = "def run(x):\n    return len(list(range(getattr(x, 'bit_length')())))"
    # getattr + range + list + len all available; 5.bit_length() == 3 -> range(3)
    assert run_strategy_sandboxed(src, "run", 5, quota=FAST) == 3


# --- the boundary: structural, not syntactic (the crown-jewel tests) --------


def test_secret_unreachable_even_after_a_real_os_escape(monkeypatch):
    # A secret exists in the PARENT's environment...
    monkeypatch.setenv("FLINT_SECRET__local__HL_KEY", "topsecret-must-not-leak")
    # ...and the strategy genuinely bypasses the import gate to reach `os`
    # (no __import__), then reads the environment.
    src = """
def run(x):
    os_mod = None
    for cls in object.__subclasses__():
        g = getattr(getattr(cls, '__init__', None), '__globals__', None)
        if g and 'os' in g:
            os_mod = g['os']
            break
    if os_mod is None:
        return {'reached_os': False, 'leaked': {}}
    env = dict(os_mod.environ)
    leaked = {k: v for k, v in env.items() if 'SECRET' in k or k.startswith('FLINT')}
    return {'reached_os': os_mod is not None, 'leaked': leaked}
"""
    result = run_strategy_sandboxed(src, "run", None, quota=FAST)
    # The escape to os may well succeed — that is the point. What must hold is
    # that no secret is reachable, because the child env is empty by construction.
    assert result["leaked"] == {}


def test_child_shares_no_memory_with_parent():
    # `store`, `config`, etc. are parent objects; the child is a fresh process,
    # so an unqualified reference is simply undefined.
    src = "def run(x):\n    return store"
    with pytest.raises(StrategyError) as exc:
        run_strategy_sandboxed(src, "run", None, quota=FAST)
    assert exc.value.error_type == "NameError"


# --- resource limits --------------------------------------------------------


def test_cpu_busy_loop_hits_cpu_limit():
    src = "def run(x):\n    while True:\n        pass"
    quota = ResourceQuota(cpu_seconds=1, mem_bytes=2 * 1024**3, wall_seconds=20)
    with pytest.raises(SandboxTimeout):
        run_strategy_sandboxed(src, "run", None, quota=quota)


def test_wall_clock_deadline_is_enforced():
    # High CPU limit, low wall limit: the parent's wall-clock kill fires.
    src = "def run(x):\n    while True:\n        pass"
    quota = ResourceQuota(cpu_seconds=30, mem_bytes=2 * 1024**3, wall_seconds=1)
    with pytest.raises(SandboxTimeout):
        run_strategy_sandboxed(src, "run", None, quota=quota)


def test_output_cap_is_enforced():
    src = "def run(x):\n    return 'x' * 9_000_000"  # > 8 MiB cap
    with pytest.raises(SandboxViolation):
        run_strategy_sandboxed(src, "run", None, quota=FAST)


# --- misc contract ----------------------------------------------------------


def test_strategy_exception_surfaces_with_type():
    src = "def run(x):\n    raise ValueError('boom')"
    with pytest.raises(StrategyError) as exc:
        run_strategy_sandboxed(src, "run", None, quota=FAST)
    assert exc.value.error_type == "ValueError"
    assert "boom" in exc.value.message


def test_missing_entry_is_an_error():
    with pytest.raises(StrategyError) as exc:
        run_strategy_sandboxed("x = 1", "run", None, quota=FAST)
    assert exc.value.error_type == "NameError"


def test_no_in_process_fallback_in_public_api():
    import flint.strategy.sandbox as sb

    # Exactly one execution entrypoint in the public API; no unsandboxed /
    # in-process escape hatch (the round-1 hole was a fallback path).
    execish = [
        n for n in sb.__all__
        if ("run" in n.lower() or "exec" in n.lower())
    ]
    assert execish == ["run_strategy_sandboxed"]


def test_os_layer_reports_unavailable_on_the_floor():
    # Documents that the OS jail is not wired up yet; the floor stands alone.
    assert os_layer_available() is False
