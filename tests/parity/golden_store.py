"""Frozen parity goldens — the serialization contract shared by recorder + tests.

The legacy bar engine was **deleted in N10** (2026-07-04). The byte-exact parity
contract it anchored (§19.4) lives on as frozen golden event logs recorded from
that engine at commit ``300e9b2``, one JSON file per scenario under ``goldens/``.
The Nautilus bar lane — now the only backtest substrate — is held to them: a
converted parity test runs *only* Nautilus and asserts its stripped event log
equals the frozen golden, byte-for-byte, zero tolerance.

These helpers are the single serialization contract both sides use:

* ``record_goldens.py`` (the one-shot recorder) called :func:`write_golden` while
  the legacy engine still existed.
* every converted parity test calls :func:`assert_matches_golden`.

Because both sides pass their rows through :func:`canonical`, a live engine log
(whose payloads carry ``Decimal`` monetary strings, ``StrEnum`` sides, and tuples)
and a JSON-loaded golden compare by plain ``==``: ``canonical`` maps everything to
its JSON-native form (``Decimal`` → its string, ``StrEnum`` → its value, tuple →
list) deterministically, so the normalization is identical on both sides.

Regenerating a golden is deliberately impossible from current history — the source
engine is gone. See ``README.md``: it requires checking out pre-N10 history.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

GOLDENS_DIR = Path(__file__).resolve().parent / "goldens"

#: Lifecycle event kinds carry the one admitted legacy/Nautilus divergence — the
#: honest ``engine`` name — which is stripped before comparison (§19.4).
_LIFECYCLE = ("run_started", "run_finished")


class _GoldenEncoder(json.JSONEncoder):
    """Serialize the non-JSON-native values event payloads carry, deterministically.

    ``Decimal`` monetary amounts serialize to their exact string (never a float —
    that would lose the byte-exact contract). ``StrEnum`` values (``Side`` etc.) are
    already ``str`` subclasses so ``json`` renders them as their value natively; this
    encoder only has to name ``Decimal``.
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def canonical(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize event rows to their JSON-native form (the great equalizer).

    Applied to *both* a live engine log and a loaded golden so they compare by
    ``==``: ``Decimal`` → string, ``StrEnum`` → value, tuple → list, all via one
    ``json`` round-trip so the mapping is identical on both sides.
    """
    return json.loads(json.dumps(rows, cls=_GoldenEncoder))


def strip_lifecycle_engine(log: Any) -> list[dict[str, Any]]:
    """Every event row, with the honest ``engine`` field dropped from lifecycle rows.

    This is the exact strip ``assert_parity`` applied to the legacy/Nautilus logs
    before diffing them (§19.4b) — the goldens were recorded through it, so the tests
    must apply it too.
    """
    rows: list[dict[str, Any]] = []
    for e in log.read():
        row = e.to_row()
        if row["kind"] in _LIFECYCLE:
            row["payload"] = {k: v for k, v in row["payload"].items() if k != "engine"}
        rows.append(row)
    return rows


def golden_path(name: str) -> Path:
    return GOLDENS_DIR / f"{name}.json"


def load_golden(name: str) -> list[dict[str, Any]]:
    with golden_path(name).open() as f:
        return json.load(f)


def write_golden(name: str, log: Any) -> None:
    """Freeze one scenario's stripped event log to ``goldens/<name>.json``.

    Called only by the recorder while the legacy engine still existed. Keys are
    sorted so the committed file is deterministic; the comparison is on the loaded
    objects, so key order never affects a test.
    """
    GOLDENS_DIR.mkdir(parents=True, exist_ok=True)
    rows = canonical(strip_lifecycle_engine(log))
    with golden_path(name).open("w") as f:
        json.dump(rows, f, indent=2, sort_keys=True)
        f.write("\n")


def _first_divergence(name: str, expected: list[dict], actual: list[dict]) -> str:
    for i, (re_, ra) in enumerate(zip(expected, actual)):
        if re_ != ra:
            fields = {
                k: (re_.get("payload", {}).get(k), ra.get("payload", {}).get(k))
                for k in set(re_.get("payload", {})) | set(ra.get("payload", {}))
                if re_.get("payload", {}).get(k) != ra.get("payload", {}).get(k)
            }
            for k in ("kind", "ts", "seq"):
                if re_.get(k) != ra.get(k):
                    fields[k] = (re_.get(k), ra.get(k))
            return (
                f"golden {name!r} row {i} differs:\n"
                f"  golden  : {re_}\n  nautilus: {ra}\n  fields  : {fields}"
            )
    if len(expected) != len(actual):
        longer, side, at = (
            (expected, "golden", len(actual))
            if len(expected) > len(actual)
            else (actual, "nautilus", len(expected))
        )
        return (
            f"golden {name!r} length differs: golden={len(expected)} "
            f"nautilus={len(actual)}; {side} has extra row {at}: {longer[at]}"
        )
    return "no divergence"


def assert_matches_golden(log: Any, name: str) -> None:
    """Assert the Nautilus engine's stripped event log equals the frozen golden.

    Byte-exact, zero tolerance (§19.4). The failure message pins the first differing
    row so a churn-induced divergence is localized, not just "not equal".
    """
    expected = load_golden(name)
    actual = canonical(strip_lifecycle_engine(log))
    assert actual == expected, _first_divergence(name, expected, actual)
