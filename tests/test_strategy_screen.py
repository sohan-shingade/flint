"""Slice 5.2 — the AST/import screen (UX) + the ctx value-object boundary (§8.3).

Two things proven here:

1. **The screen is line-precise, correct, and lint-grade only.** It flags denied
   imports, non-deterministic clocks/RNGs, the exec/filesystem builtins, the
   reflection escape chain, and unsafe deserialization — each with a line — and it
   is *opt-in* in the runner (``screen=False`` still reaches the boundary), because
   the process boundary, not the screen, is the security (§8.3).
2. **ctx is a value object across the boundary.** A live engine ctx *can* reach a
   TenantContext and the store in-process (which is exactly why untrusted code is
   never handed it in-process) — but nothing with such a reference can cross into
   the sandbox: the wire codec serializes only inert JSON/Arrow, so what a
   sandboxed strategy receives has no attribute path to credentials/store/config.
"""

from __future__ import annotations

from random import Random

import pytest

from flint.adapters import InMemoryUserData
from flint.engine import PortfolioState
from flint.engine.loop import EngineContext
from flint.engine.portfolio import EventLog
from flint.ports import ResourceQuota, TenantContext
from flint.ports.storage import UserDataPort
from flint.strategy.sandbox import (
    ScreenViolation,
    StrategyError,
    StrategyScreenError,
    run_strategy_sandboxed,
    screen_or_raise,
    screen_source,
)
from flint.strategy.sandbox.protocol import decode_value, encode_value
from flint.venues import HYPERLIQUID

FAST = ResourceQuota(cpu_seconds=5, mem_bytes=2 * 1024**3, wall_seconds=15)


def _codes(source: str) -> list[str]:
    return [v.code for v in screen_source(source)]


# --- the screen: clean source -------------------------------------------------


def test_clean_strategy_passes_with_allowed_imports():
    src = (
        "import numpy as np\n"
        "import pandas as pd\n"
        "from flint import Strategy, Signal\n"
        "from math import floor\n"
        "class S(Strategy):\n"
        "    def on_candle(self, candle, history, ctx):\n"
        "        return []\n"
    )
    assert screen_source(src) == []
    screen_or_raise(src)  # does not raise


# --- imports + non-determinism ------------------------------------------------


@pytest.mark.parametrize("module", ["os", "sys", "subprocess", "socket", "requests", "importlib"])
def test_denied_import_is_flagged_line_precise(module):
    src = f"x = 1\nimport {module}\n"
    v = screen_source(src)
    assert len(v) == 1
    assert v[0].code == "import-not-allowed"
    assert v[0].line == 2  # the import line, not line 1
    assert module in v[0].message


@pytest.mark.parametrize("module,hint", [("random", "ctx.rng"), ("time", "ctx.now"), ("datetime", "ctx.now")])
def test_nondeterministic_import_points_to_ctx(module, hint):
    v = screen_source(f"import {module}\n")
    assert v[0].code == "nondeterminism"
    assert hint in v[0].message


def test_from_import_and_relative_import():
    assert _codes("from os import getcwd\n") == ["import-not-allowed"]
    assert _codes("from . import sibling\n") == ["import-not-allowed"]
    assert _codes("from numpy import array\n") == []  # allowed root


# --- forbidden builtins + reflection + deserialization ------------------------


@pytest.mark.parametrize("call", ["eval('1')", "exec('x=1')", "compile('1','','eval')",
                                   "open('/etc/passwd')", "__import__('os')", "globals()"])
def test_forbidden_builtins_are_flagged(call):
    assert _codes(f"y = {call}\n") == ["forbidden-call"]


def test_getattr_and_setattr_are_allowed_by_the_screen():
    # These are legitimate and contained by the boundary — the screen must not
    # nag about them (policy.py keeps them in builtins).
    assert screen_source("z = getattr(x, 'foo')\nsetattr(x, 'a', 1)\n") == []


@pytest.mark.parametrize("expr", ["object.__subclasses__()", "f.__globals__",
                                  "cls.__bases__", "g.__code__.co_consts"])
def test_reflection_escape_chain_is_flagged(expr):
    assert "reflection-escape" in _codes(f"v = {expr}\n")


@pytest.mark.parametrize("call", ["pickle.loads(b)", "joblib.load('m')", "torch.load('m')",
                                  "yaml.unsafe_load(s)"])
def test_unsafe_deserialization_is_flagged(call):
    # (The module import is itself denied, but the load call earns a precise
    # message pointing at ctx.model_store.)
    codes = _codes(f"m = {call}\n")
    assert "unsafe-deserialization" in codes


# --- structure ----------------------------------------------------------------


def test_syntax_error_is_a_single_violation_not_a_raw_raise():
    v = screen_source("def run(:\n    pass\n")
    assert len(v) == 1 and v[0].code == "syntax-error"


def test_multiple_violations_are_sorted_by_position():
    src = "import os\n\ny = eval('1')\n"
    v = screen_source(src)
    assert [x.line for x in v] == [1, 3]
    assert isinstance(v[0], ScreenViolation)


def test_screen_error_message_lists_every_issue_with_lines():
    with pytest.raises(StrategyScreenError) as exc:
        screen_or_raise("import os\nz = open('f')\n")
    assert len(exc.value.violations) == 2
    assert "line 1" in str(exc.value) and "line 2" in str(exc.value)


# --- runner wiring: opt-in, never load-bearing --------------------------------


def test_screen_true_raises_before_spawning_a_subprocess():
    # A denied import with screen=True is caught by the static pass — a
    # StrategyScreenError (pre-spawn), not the runtime ImportError.
    with pytest.raises(StrategyScreenError):
        run_strategy_sandboxed("import os\ndef run(x):\n    return 1", "run",
                               None, quota=FAST, screen=True)


def test_screen_false_is_the_default_and_reaches_the_boundary():
    # Same source, screen off: it runs, and the runtime import guard (the boundary)
    # rejects it — proving the screen is opt-in UX, not the gate.
    with pytest.raises(StrategyError) as exc:
        run_strategy_sandboxed("import os\ndef run(x):\n    return 1", "run",
                               None, quota=FAST)
    assert exc.value.error_type == "ImportError"


def test_screen_true_lets_clean_source_run():
    src = "def run(x):\n    return x + 1"
    assert run_strategy_sandboxed(src, "run", 41, quota=FAST, screen=True) == 42


# --- ctx as a value object across the boundary (§8.3) -------------------------

_SENSITIVE = (TenantContext, UserDataPort, EventLog, EngineContext)


def _reachable(root, *, cap=50_000):
    """BFS the object graph a holder of ``root`` could actually traverse."""
    seen: set[int] = set()
    stack = [root]
    out = []
    while stack and len(seen) < cap:
        obj = stack.pop()
        oid = id(obj)
        if oid in seen:
            continue
        seen.add(oid)
        out.append(obj)
        if isinstance(obj, (str, bytes, bytearray, int, float, bool, type(None))):
            continue
        # callables leak their bound object / closure — the classic escape edge
        for attr in ("__self__", "__func__", "__wrapped__"):
            if hasattr(obj, attr):
                stack.append(getattr(obj, attr))
        closure = getattr(obj, "__closure__", None)
        if closure:
            for cell in closure:
                try:
                    stack.append(cell.cell_contents)
                except ValueError:
                    pass
        d = getattr(obj, "__dict__", None)
        if isinstance(d, dict):
            stack.extend(d.keys())
            stack.extend(d.values())
        for klass in type(obj).__mro__:
            for name in getattr(klass, "__slots__", ()):
                try:
                    stack.append(getattr(obj, name))
                except AttributeError:
                    pass
        if isinstance(obj, dict):
            stack.extend(obj.keys())
            stack.extend(obj.values())
        elif isinstance(obj, (list, tuple, set, frozenset)):
            stack.extend(obj)
    return out


def _live_ctx():
    # The live bar lane hands the strategy an EngineContext whose ``submit_order_fn``
    # is the shim's ``_submit`` — a bound method of the ``FlintRecorder``, the single
    # in-process EventLog writer (§A6). We build that same leak shape directly: the
    # recorder holds the EventLog (hence the store and the tenant), so a holder of the
    # ctx can walk ``submit_order_fn.__self__`` straight to them. This is exactly why
    # untrusted code is never handed the in-process ctx — only its serialized value
    # object across the sandbox boundary. Needs the Nautilus recorder, so skip cleanly
    # without the extra.
    pytest.importorskip("nautilus_trader")
    from flint.engine.nautilus.translate import FlintRecorder

    store = InMemoryUserData()
    log = EventLog(store, TenantContext.local(), run_id="vo")
    recorder = FlintRecorder(
        event_log=log, shadow=PortfolioState(), engine_name="nautilus"
    )
    ctx = EngineContext(
        state=recorder._flint_shadow,
        default_venue="hyperliquid",
        now=0,
        rng=Random(0),
        venue_spec=HYPERLIQUID,
        submit_order_fn=recorder.record_placed,
    )
    return ctx


def test_walker_actually_finds_planted_sensitive_objects():
    # Positive control: the walker must reach a TenantContext hidden in a dict,
    # or the purity assertions below would be vacuous.
    planted = {"nested": [{"tenant": TenantContext.local()}]}
    assert any(isinstance(o, TenantContext) for o in _reachable(planted))


def test_live_in_process_ctx_can_reach_tenant_and_store():
    # The live engine ctx is NOT a value object: submit_order_fn.__self__ is the
    # recorder, which holds the event log, the store, and the tenant. This is why
    # untrusted code is NEVER handed it in-process — only across the boundary.
    reached = _reachable(_live_ctx())
    assert any(isinstance(o, TenantContext) for o in reached)
    assert any(isinstance(o, UserDataPort) for o in reached)


def test_boundary_refuses_to_serialize_any_live_reference():
    # Nothing with a path to the engine/store/credentials can cross into the child:
    # the wire codec accepts only inert JSON/Arrow and raises on everything else.
    ctx = _live_ctx()
    for live in (ctx, ctx.submit_order_fn, TenantContext.local(), InMemoryUserData()):
        with pytest.raises(TypeError):
            encode_value(live)


def test_decoded_sandbox_payload_has_no_path_to_credentials_store_or_config():
    # What a sandboxed strategy actually receives is the decoded wire payload —
    # inert data. Round-trip a representative account+market snapshot and confirm
    # no sensitive object is reachable from it.
    payload = {
        "account": {"venue": "hyperliquid", "equity": 10_000.0, "cross_margin_available": 8_000.0},
        "funding": {"SOL-PERP": 0.0001},
        "candles": [{"ts": 1, "close": 100.0}, {"ts": 2, "close": 101.0}],
        "now": 1_700_000_000_000,
    }
    decoded = decode_value(encode_value(payload))
    assert not any(isinstance(o, _SENSITIVE) for o in _reachable(decoded))
    assert not any(callable(o) for o in _reachable(decoded))
