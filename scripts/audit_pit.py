#!/usr/bin/env python
"""Point-in-time audit — scan every data provider for lookahead risk.

Each provider must document its timestamp convention so downstream consumers
(backtest engine, MC, reconciliation) can trust the data. Providers that have
not been reviewed are flagged loudly.

A provider is considered PIT-safe when it declares an attribute:

    PIT_METADATA: dict = {
        "candle_ts": "bar-close",       # or "bar-open", "exchange-time"
        "funding_ts": "accrual-time",   # or "settlement-time", "poll-time"
        "orderbook_ts": "exchange-time",  # or "poll-time"
        "oi_ts": "block-time",          # or "poll-time"
        "reviewed": "2026-04-23",       # ISO date of last human audit
    }

Providers missing this declaration produce a ⚠ in the report. Providers
declaring poll-time for a field that should be exchange-time produce a ✗.

Usage:
    python scripts/audit_pit.py               # print to stdout
    python scripts/audit_pit.py -o <path>     # write markdown report

Phase 1 T1.3.a — see docs/specs/phase-1-trust-correctness.md.
"""
from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# Fields each provider should declare, and what values are ACCEPTABLE.
REQUIRED_FIELDS = {
    "candle_ts": {"bar-close", "bar-open", "exchange-time"},
    "funding_ts": {"accrual-time", "settlement-time", "exchange-time"},
    "orderbook_ts": {"exchange-time"},
    "oi_ts": {"block-time", "exchange-time"},
    "reviewed": None,  # date string — any format accepted
}

# Fields that indicate lookahead risk when present.
FORBIDDEN_VALUES = {"poll-time"}


@dataclass
class ProviderAudit:
    module_name: str
    has_metadata: bool = False
    metadata: Optional[dict] = None
    issues: List[str] = field(default_factory=list)
    verdict: str = "unknown"  # pass / warn / fail / unknown


def audit_provider(module_name: str) -> ProviderAudit:
    audit = ProviderAudit(module_name=module_name)
    try:
        mod = importlib.import_module(module_name)
    except Exception as e:
        audit.issues.append(f"import-failed: {type(e).__name__}: {e}")
        audit.verdict = "fail"
        return audit

    meta = getattr(mod, "PIT_METADATA", None)
    if meta is None:
        audit.verdict = "warn"
        audit.issues.append("no PIT_METADATA — human review required")
        return audit

    if not isinstance(meta, dict):
        audit.verdict = "fail"
        audit.issues.append(f"PIT_METADATA is {type(meta).__name__}, expected dict")
        return audit

    audit.has_metadata = True
    audit.metadata = meta

    for field_name, allowed in REQUIRED_FIELDS.items():
        val = meta.get(field_name)
        if val is None:
            audit.issues.append(f"missing field: {field_name}")
            continue
        if allowed is not None and val not in allowed:
            audit.issues.append(
                f"{field_name}={val!r} not in {sorted(allowed)}"
            )
        if val in FORBIDDEN_VALUES:
            audit.issues.append(f"{field_name} uses FORBIDDEN value {val!r}")

    audit.verdict = "fail" if any("FORBIDDEN" in x or "not in" in x for x in audit.issues) \
        else "pass" if not audit.issues else "warn"
    return audit


def discover_providers() -> List[str]:
    """Discover all provider module names under flint.providers."""
    import flint.providers as pkg
    mods = []
    for _, name, ispkg in pkgutil.iter_modules(pkg.__path__):
        if ispkg or name.startswith("_"):
            continue
        # Skip infra modules
        if name in {"registry", "base", "websocket"}:
            continue
        mods.append(f"flint.providers.{name}")
    return sorted(mods)


def render_report(audits: List[ProviderAudit]) -> str:
    n = len(audits)
    passes = sum(1 for a in audits if a.verdict == "pass")
    warns = sum(1 for a in audits if a.verdict == "warn")
    fails = sum(1 for a in audits if a.verdict == "fail")

    lines = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"# Point-in-Time Audit — {ts}")
    lines.append("")
    lines.append(
        "Every data provider must declare `PIT_METADATA` documenting its "
        "timestamp convention. Providers without a declaration are flagged "
        "for human review."
    )
    lines.append("")
    lines.append(f"**Summary:** {n} providers · {passes} ✓ pass · {warns} ⚠ warn · {fails} ✗ fail")
    lines.append("")
    lines.append("| Provider | Verdict | Issues |")
    lines.append("|---|---|---|")
    for a in audits:
        badge = {"pass": "✓", "warn": "⚠", "fail": "✗", "unknown": "?"}[a.verdict]
        issues = "; ".join(a.issues) if a.issues else "—"
        short_name = a.module_name.rsplit(".", 1)[-1]
        lines.append(f"| `{short_name}` | {badge} {a.verdict} | {issues} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Required PIT_METADATA template")
    lines.append("")
    lines.append("```python")
    lines.append('PIT_METADATA = {')
    lines.append('    "candle_ts": "bar-close",')
    lines.append('    "funding_ts": "accrual-time",')
    lines.append('    "orderbook_ts": "exchange-time",')
    lines.append('    "oi_ts": "block-time",')
    lines.append('    "reviewed": "YYYY-MM-DD",')
    lines.append('}')
    lines.append("```")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--out", type=Path, help="Write markdown report to path")
    parser.add_argument("--fail-on-warn", action="store_true",
                        help="Exit non-zero on any warn or fail verdict")
    args = parser.parse_args()

    providers = discover_providers()
    audits = [audit_provider(name) for name in providers]
    report = render_report(audits)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(report)

    if args.fail_on_warn:
        bad = [a for a in audits if a.verdict in {"warn", "fail"}]
        if bad:
            print(f"\n{len(bad)} providers have issues", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
