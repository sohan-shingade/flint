"""The canonical legacy-vs-Nautilus parity harness (§19.4, plan §A8).

One entry point — :func:`run_both` — drives the same feed + strategy through
both engines and returns their event logs. Two diff layers, applied in order by
:func:`assert_parity`:

* **Layer (a) — input parity.** :func:`input_parity_rows` extracts the exact
  inputs both engines feed the *shared pure functions* — the funding settlement
  arguments (rate, settled rate, cap flag, interval, price basis, oracle price,
  position size, amount) and the liquidation-check inputs (mark, equity,
  maintenance, size, side, margin mode, liq/bankruptcy price). Diffing these
  first localizes any divergence to *reach* (ordering / fill path), not to the
  math (§19.4a): a layer-(a) pass with a layer-(b) fail means the same numbers
  reached the pure functions but the event stream around them diverged.

* **Layer (b) — full byte diff.** :func:`full_rows` is the whole event stream by
  ``(ts, seq)``: event-kind sequence, fill decisions/prices/sizes, order
  transitions, FUNDING/LIQUIDATION payloads (including Decimal-string amounts),
  and per-bar EQUITY Decimal-strings. Exact equality, no tolerances — the shared
  pure modules ARE the single implementation (§6.0), so equality is by
  construction. The only admitted divergence is the ``engine`` name stamped on
  the ``run_started`` / ``run_finished`` lifecycle events, which is stripped.
"""

from __future__ import annotations

from typing import Any, Callable

from flint.adapters import InMemoryUserData
from flint.engine.api import EngineFeed, EngineRunSpec
from flint.engine.portfolio import EventLog
from flint.engine.portfolio.events import FUNDING, LIQUIDATION
from flint.engine.select import engine_for
from flint.engine.state import PortfolioState
from flint.ports import TenantContext

LEGACY = "legacy-bar"
NAUTILUS = "nautilus"

# The one honest divergence: the engine name on the run-lifecycle events (§A6).
_LIFECYCLE = ("run_started", "run_finished")


def run_engine(
    engine_name: str,
    make_strategy: Callable[[], object],
    feed: EngineFeed,
    spec: EngineRunSpec,
) -> tuple[EventLog, PortfolioState]:
    """Run one engine on a fresh log; return ``(log, final_state)``."""
    log = EventLog(
        InMemoryUserData(), TenantContext.local(), run_id=f"{engine_name}-{id(feed)}"
    )
    state = engine_for(engine_name)().run(
        feed, make_strategy(), event_log=log, spec=spec
    )
    return log, state


def run_both(
    make_strategy: Callable[[], object],
    feed: EngineFeed,
    spec: EngineRunSpec,
) -> tuple[EventLog, EventLog]:
    """Drive one feed + strategy through both engines. Fresh feed per engine is
    the caller's job when a feed is stateful; the goldens pass a builder so each
    ``run_both`` gets its own feed objects."""
    legacy_log, _ = run_engine(LEGACY, make_strategy, feed, spec)
    nautilus_log, _ = run_engine(NAUTILUS, make_strategy, feed, spec)
    return legacy_log, nautilus_log


# --- layer (b): full byte diff -----------------------------------------------


def full_rows(log: EventLog) -> list[dict[str, Any]]:
    """Every event row by ``(ts, seq)`` with the lifecycle ``engine`` field
    dropped — the layer-(b) comparison basis (§19.4b)."""
    rows: list[dict[str, Any]] = []
    for e in log.read():
        row = e.to_row()
        if row["kind"] in _LIFECYCLE:
            row["payload"] = {k: v for k, v in row["payload"].items() if k != "engine"}
        rows.append(row)
    return rows


# --- layer (a): input parity -------------------------------------------------

# The exact arguments ``funding/settlement.py`` is called with (§6.4). Every key
# is a direct input to ``clamp_funding_rate`` / ``settlement_payment`` or the
# ``settlement_price_for`` oracle, plus the resulting Decimal-string amount.
_FUNDING_INPUTS = (
    "market",
    "venue",
    "rate_hourly",
    "settled_rate_hourly",
    "rate_capped",
    "interval_s",
    "price_basis",
    "oracle_price",
    "size",
    "amount",
)

# The exact inputs ``liquidation/cascade.py`` evaluates the check on (§6.5).
_LIQ_INPUTS = (
    "market",
    "side",
    "size",
    "margin_mode",
    "mark_price",
    "equity",
    "maintenance",
    "liq_price",
    "bankruptcy_price",
    "intrabar_ambiguous",
)


def input_parity_rows(log: EventLog) -> list[dict[str, Any]]:
    """The settlement / liq-check inputs keyed by ``(ts, kind, market)`` (§19.4a).

    Each row is ``{kind, ts, market, inputs}`` where ``inputs`` is the subset of
    the payload that feeds a shared pure function. Deliberately **order- and
    seq-independent**: the rows are sorted by ``(ts, kind, market)`` and carry no
    writer ``seq``, because layer (a) answers only *did the same inputs reach the
    same pure math?* — not *in what interleaving were they emitted?* That is the
    localizer contract (§19.4a): when a multi-market run reorders the per-market
    event stream, layer (a) still passes (the settlement math is unchanged) and
    layer (b) — the strict ``(ts, seq)`` byte diff — is the one that fails,
    pinning the divergence to ordering / fill-path rather than the math.
    """
    rows: list[dict[str, Any]] = []
    for e in log.read():
        if e.kind == FUNDING:
            keys = _FUNDING_INPUTS
        elif e.kind == LIQUIDATION:
            keys = _LIQ_INPUTS
        else:
            continue
        rows.append(
            {
                "kind": e.kind,
                "ts": e.ts,
                "market": e.payload.get("market"),
                "inputs": {k: e.payload[k] for k in keys if k in e.payload},
            }
        )
    rows.sort(key=lambda r: (r["ts"], r["kind"], r["market"] or ""))
    return rows


# --- the diff report ---------------------------------------------------------


def _first_divergence(a: list[dict], b: list[dict]) -> str:
    """A readable message pinpointing the first differing row (or a length gap)."""
    for i, (ra, rb) in enumerate(zip(a, b)):
        if ra != rb:
            diffs = _dict_diff(ra, rb)
            return f"row {i} differs:\n  legacy  : {ra}\n  nautilus: {rb}\n  fields  : {diffs}"
    if len(a) != len(b):
        longer, side, extra_at = (a, "legacy", len(b)) if len(a) > len(b) else (b, "nautilus", len(a))
        return f"length differs: legacy={len(a)} nautilus={len(b)}; {side} has extra row {extra_at}: {longer[extra_at]}"
    return "no divergence"


def _dict_diff(a: dict, b: dict) -> dict[str, tuple[Any, Any]]:
    out: dict[str, tuple[Any, Any]] = {}
    if a.get("kind") != b.get("kind"):
        out["kind"] = (a.get("kind"), b.get("kind"))
    pa, pb = a.get("payload", a.get("inputs", {})), b.get("payload", b.get("inputs", {}))
    for k in set(pa) | set(pb):
        if pa.get(k) != pb.get(k):
            out[k] = (pa.get(k), pb.get(k))
    for k in ("ts", "seq"):
        if a.get(k) != b.get(k):
            out[k] = (a.get(k), b.get(k))
    return out


def assert_parity(legacy_log: EventLog, nautilus_log: EventLog) -> None:
    """Both §19.4 layers, layer (a) first so a divergence is localized.

    Layer (a) is a *localizer*, not a weaker check: it runs first so that when a
    golden breaks, the failure message says whether the same inputs reached the
    pure functions (⇒ ordering/fill-path bug) or not (⇒ math/reach bug). Layer
    (b) is the byte-exact contract.
    """
    la, lb = input_parity_rows(legacy_log), input_parity_rows(nautilus_log)
    assert la == lb, "layer (a) input parity FAILED — divergence is in reach/ordering:\n" + _first_divergence(la, lb)

    fa, fb = full_rows(legacy_log), full_rows(nautilus_log)
    assert fa == fb, (
        "layer (b) full byte diff FAILED"
        + (" (layer (a) passed ⇒ divergence is ordering / fill-path, not the pure math)" if la == lb else "")
        + ":\n"
        + _first_divergence(fa, fb)
    )


def layer_a_matches(legacy_log: EventLog, nautilus_log: EventLog) -> bool:
    """True iff the settlement / liq-check inputs match (order-independent, §19.4a)."""
    return input_parity_rows(legacy_log) == input_parity_rows(nautilus_log)


def layer_b_matches(legacy_log: EventLog, nautilus_log: EventLog) -> bool:
    """True iff the full event stream is byte-identical by ``(ts, seq)`` (§19.4b)."""
    return full_rows(legacy_log) == full_rows(nautilus_log)


def parity_diff(legacy_log: EventLog, nautilus_log: EventLog) -> str:
    """The divergence report without asserting — used to document an xfail."""
    la, lb = input_parity_rows(legacy_log), input_parity_rows(nautilus_log)
    if la != lb:
        return "layer (a) input parity divergence:\n" + _first_divergence(la, lb)
    fa, fb = full_rows(legacy_log), full_rows(nautilus_log)
    if fa != fb:
        return "layer (b) full-diff divergence (layer (a) clean):\n" + _first_divergence(fa, fb)
    return "no divergence"
