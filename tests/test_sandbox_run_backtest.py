"""In-sandbox full-run backtest execution — the D25 closure (§8.3, slice 7.6).

Where ``test_sandbox.py`` proves the single-entry sandbox contains a strategy
during *validation*, these prove the SAME OS boundary contains an entire *engine
run*: a strategy compiled and walked inside the isolated child, over inert
serialized inputs, cannot read the (empty-by-construction) child environment,
import a denied module, or crash the host — it surfaces the sandbox failure
taxonomy instead. The parent-side spawn/scrub/limits machinery is shared with
``run_strategy_sandboxed`` (proven there); here we exercise the run path itself.

All inputs are hand-authored (D26): three fixed candles + marks, no funding to
settle, no network, no keys.
"""

from __future__ import annotations

import pytest

from flint.core.models.market import Candle, MarkSnapshot
from flint.ports import ResourceQuota
from flint.strategy.sandbox import StrategyError
from flint.strategy.sandbox.backtest_runner import run_backtest_in_sandbox

FAST = ResourceQuota(cpu_seconds=5, mem_bytes=2 * 1024**3, wall_seconds=30)
VENUE = "hyperliquid"
MARKET = "SOL-PERP"
T0 = 1_700_000_000_000
H = 3_600_000
_CLOSES = (100.0, 101.0, 102.0)


def _candles() -> list[Candle]:
    return [
        Candle(
            ts=T0 + i * H,
            open=c,
            high=c + 1.0,
            low=c - 1.0,
            close=c,
            volume=1000.0,
            market=MARKET,
            resolution_s=3600,
            venue=VENUE,
        )
        for i, c in enumerate(_CLOSES)
    ]


def _marks() -> dict[str, list[MarkSnapshot]]:
    return {
        MARKET: [
            MarkSnapshot(
                market=MARKET, ts=T0 + i * H, mark_price=c, index_price=c, venue=VENUE
            )
            for i, c in enumerate(_CLOSES)
        ]
    }


def _run(source: str, class_name: str, **kw):
    return run_backtest_in_sandbox(
        source,
        class_name,
        candles=_candles(),
        funding={},
        marks=_marks(),
        run_id="t",
        quota=FAST,
        **kw,
    )


NOOP = """
from flint.strategy import Strategy


class Flat(Strategy):
    def on_candle(self, candle, history, ctx):
        return []
"""


def test_clean_run_returns_an_event_stream_and_drains():
    result = _run(NOOP, "Flat")
    # The engine walked the bars inside the sandbox and emitted a real event log.
    assert len(result.events) > 0
    assert any(e["kind"] == "equity" for e in result.events)
    assert result.rejections == []
    assert isinstance(result.notes, list)


DENIED_IMPORT_AT_RUN = """
from flint.strategy import Strategy


class Importer(Strategy):
    def on_candle(self, candle, history, ctx):
        import socket  # denied by the sandbox allowlist, only reached at run time
        return []
"""


def test_denied_import_during_the_run_is_contained_as_a_strategy_error():
    with pytest.raises(StrategyError) as exc:
        _run(DENIED_IMPORT_AT_RUN, "Importer")
    assert exc.value.error_type == "ImportError"


RAISES_AT_RUN = """
from flint.strategy import Strategy


class Boom(Strategy):
    def on_candle(self, candle, history, ctx):
        raise ValueError("boom during the run")
"""


def test_strategy_raising_mid_run_surfaces_with_its_type_not_a_stack_trace():
    with pytest.raises(StrategyError) as exc:
        _run(RAISES_AT_RUN, "Boom")
    assert exc.value.error_type == "ValueError"
    assert "boom during the run" in exc.value.message


# A getattr chain built from string fragments — the static screen (which flags the
# literal __subclasses__/__globals__ dunders) never sees it, so this only matters
# at run time, exactly the "escape during the run" the boundary must contain.
OS_ESCAPE_AT_RUN = """
from flint.strategy import Strategy


class Escaper(Strategy):
    def on_candle(self, candle, history, ctx):
        subs = getattr(object, "__sub" + "classes__")()
        leaked = {}
        for kls in subs:
            g = getattr(getattr(kls, "__ini" + "t__", None), "__glob" + "als__", None)
            if g and "os" in g:
                env = dict(g["os"].environ)
                leaked = {k: v for k, v in env.items()
                          if "SECRET" in k or k.startswith("FLINT")}
                break
        raise RuntimeError("leak=" + repr(sorted(leaked)))
"""


def test_os_escape_during_the_run_finds_an_empty_environment(monkeypatch):
    # A secret exists in the PARENT env; the strategy genuinely reaches os during
    # the run and learns nothing, because the child is spawned with env={} — the
    # secret is unreachable by construction, not by filtering.
    monkeypatch.setenv("FLINT_SECRET__local__HL_KEY", "topsecret-must-not-leak")
    with pytest.raises(StrategyError) as exc:
        _run(OS_ESCAPE_AT_RUN, "Escaper")
    assert "topsecret" not in exc.value.message
    assert "leak=[]" in exc.value.message


MISSING_CLASS = """
from flint.strategy import Strategy


class Present(Strategy):
    def on_candle(self, candle, history, ctx):
        return []
"""


def test_missing_class_name_is_a_strategy_error():
    with pytest.raises(StrategyError) as exc:
        _run(MISSING_CLASS, "Absent")
    assert exc.value.error_type == "NameError"
