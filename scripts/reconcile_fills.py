#!/usr/bin/env python
"""Reconciliation tooling — diff engine-recorded fills vs actual venue fills.

Phase 1 T1.4. Answers the question firms ask first: "Did the engine's fills
match what actually happened on the exchange?"

Usage:
    # Compare a paper-session's fills against a user-provided venue export:
    python scripts/reconcile_fills.py \\
        --session <session_id> \\
        --fills-csv ./data/custom/drift_fills_2026-04.csv \\
        --out artifacts/reconciliation/drift_jul2026.md

Input CSV schema (see docs/reference/custom-data-schema.md#fills):

    ts_epoch_s,market,venue,side,size,price,fee,order_id

Matching algorithm:
    For each engine fill, find the nearest venue fill by timestamp within
    a configurable window (default 60s). Match on (market, venue, side, size).
    Compute per-match price delta, ts delta. Report unmatched fills on
    either side.

Emits a markdown report with: summary counts, per-match delta stats
(p50/p95/p99 on price + ts), full unmatched list.

CI gate (Phase 5): price-delta p95 > 10bps or orphan fills > 5% fails.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Sequence, Tuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class Fill:
    __slots__ = ("ts", "market", "venue", "side", "size", "price", "fee",
                 "order_id", "source")

    def __init__(self, ts: int, market: str, venue: str, side: str,
                 size: float, price: float, fee: float = 0.0,
                 order_id: str = "", source: str = ""):
        self.ts = int(ts)
        self.market = market
        self.venue = venue
        self.side = side  # "long" / "short"
        self.size = float(size)
        self.price = float(price)
        self.fee = float(fee)
        self.order_id = order_id or ""
        self.source = source  # "engine" / "venue"

    def __repr__(self):
        return (f"Fill({self.source}, ts={self.ts}, market={self.market}, "
                f"venue={self.venue}, {self.side} {self.size}@{self.price})")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_engine_fills(session_id: str) -> List[Fill]:
    """Pull engine-recorded fills for a paper session from FlintStore."""
    from flint.store import FlintStore

    store = FlintStore()
    raw = store.get_live_fills(session_id)
    fills: List[Fill] = []
    for r in raw:
        # get_live_fills returns a list of dicts.
        fills.append(Fill(
            ts=r["ts"],
            market=r["market"],
            venue=r.get("venue", "default"),
            side=r["side"],
            size=r["size"],
            price=r["price"],
            fee=r.get("fee", 0.0),
            order_id=str(r.get("order_id", "")),
            source="engine",
        ))
    return fills


class CSVSchemaError(ValueError):
    """Raised when an uploaded venue fill CSV is missing required columns
    or has malformed rows. Distinguishable from generic ValueError so the
    API layer can return a 400 with a helpful message."""


def _parse_venue_fills_reader(reader: csv.DictReader, source_label: str) -> List[Fill]:
    """Shared parsing logic — used by both the CLI loader and the
    HTTP `POST /paper/{id}/reconciliation` route."""
    required = {"ts_epoch_s", "market", "venue", "side", "size", "price"}
    if not required.issubset(reader.fieldnames or set()):
        missing = required - set(reader.fieldnames or [])
        raise CSVSchemaError(
            f"{source_label}: missing required columns {sorted(missing)}"
        )
    fills: List[Fill] = []
    for i, row in enumerate(reader, start=2):
        side = (row.get("side") or "").lower()
        if side not in {"long", "short", "buy", "sell"}:
            raise CSVSchemaError(f"{source_label}:{i} invalid side {side!r}")
        side = "long" if side in {"long", "buy"} else "short"
        fills.append(Fill(
            ts=int(row["ts_epoch_s"]),
            market=row["market"],
            venue=row["venue"],
            side=side,
            size=float(row["size"]),
            price=float(row["price"]),
            fee=float(row.get("fee", 0.0) or 0.0),
            order_id=row.get("order_id", ""),
            source="venue",
        ))
    return fills


def load_venue_fills_csv(path: Path) -> List[Fill]:
    """Parse venue-exported fill CSV from disk. See custom-data-schema.md#fills."""
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        try:
            return _parse_venue_fills_reader(reader, str(path))
        except CSVSchemaError as e:
            # CLI surfaces schema errors as SystemExit (the original behavior)
            raise SystemExit(str(e))


def parse_venue_fills_csv_text(text: str) -> List[Fill]:
    """Parse venue fills from an in-memory CSV string — used by the
    `POST /api/v1/paper/{id}/reconciliation` multipart upload route."""
    import io
    reader = csv.DictReader(io.StringIO(text))
    return _parse_venue_fills_reader(reader, "<upload>")


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _size_close(a: float, b: float, size_tol_pct: float = 0.001) -> bool:
    """Sizes within 0.1% relative."""
    if a == 0 and b == 0:
        return True
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom <= size_tol_pct


def _match_fills(
    engine_fills: Sequence[Fill],
    venue_fills: Sequence[Fill],
    ts_window_s: int = 60,
) -> Tuple[List[Tuple[Fill, Fill]], List[Fill], List[Fill]]:
    """Greedy nearest-ts match on (market, venue, side, size).

    Returns (matches, engine_only, venue_only).
    """
    matches: List[Tuple[Fill, Fill]] = []
    venue_pool = list(venue_fills)
    venue_used = [False] * len(venue_pool)

    for ef in engine_fills:
        best_idx = -1
        best_dt = None
        for i, vf in enumerate(venue_pool):
            if venue_used[i]:
                continue
            if vf.market != ef.market or vf.venue != ef.venue or vf.side != ef.side:
                continue
            if not _size_close(vf.size, ef.size):
                continue
            dt = abs(vf.ts - ef.ts)
            if dt > ts_window_s:
                continue
            if best_dt is None or dt < best_dt:
                best_dt = dt
                best_idx = i
        if best_idx >= 0:
            matches.append((ef, venue_pool[best_idx]))
            venue_used[best_idx] = True

    engine_only = [ef for ef in engine_fills
                   if not any(m[0] is ef for m in matches)]
    venue_only = [vf for i, vf in enumerate(venue_pool) if not venue_used[i]]
    return matches, engine_only, venue_only


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    k = (len(xs) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _price_bps_delta(engine_price: float, venue_price: float) -> float:
    """Signed basis-point delta = (engine - venue) / venue * 10_000."""
    if venue_price == 0:
        return 0.0
    return (engine_price - venue_price) / venue_price * 10_000.0


def _ts_delta(engine_ts: int, venue_ts: int) -> int:
    return engine_ts - venue_ts


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _render_report(
    session_id: str,
    engine_fills: Sequence[Fill],
    venue_fills: Sequence[Fill],
    matches: Sequence[Tuple[Fill, Fill]],
    engine_only: Sequence[Fill],
    venue_only: Sequence[Fill],
    price_bps_threshold_p95: float,
    orphan_threshold_pct: float,
) -> Tuple[str, bool]:
    price_bps = [abs(_price_bps_delta(e.price, v.price)) for e, v in matches]
    ts_deltas = [abs(_ts_delta(e.ts, v.ts)) for e, v in matches]

    total = len(engine_fills) + len(venue_fills)
    orphans = len(engine_only) + len(venue_only)
    orphan_pct = (orphans / total * 100) if total else 0.0

    p50_bps = _percentile(price_bps, 0.50)
    p95_bps = _percentile(price_bps, 0.95)
    p99_bps = _percentile(price_bps, 0.99)
    p50_ts = _percentile([float(x) for x in ts_deltas], 0.50)
    p95_ts = _percentile([float(x) for x in ts_deltas], 0.95)

    price_ok = p95_bps <= price_bps_threshold_p95
    orphan_ok = orphan_pct <= orphan_threshold_pct
    all_ok = price_ok and orphan_ok

    verdict = "✓ PASS" if all_ok else "✗ FAIL"

    def check(ok):
        return "✓" if ok else "✗"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []
    lines.append(f"# Reconciliation Report — session `{session_id}`")
    lines.append("")
    lines.append(f"**Verdict:** {verdict}")
    lines.append(f"**Generated:** {now}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value | Threshold | Pass |")
    lines.append("|---|---:|---:|:---:|")
    lines.append(f"| Engine fills | `{len(engine_fills)}` | — | — |")
    lines.append(f"| Venue fills  | `{len(venue_fills)}` | — | — |")
    lines.append(f"| Matched pairs | `{len(matches)}` | — | — |")
    lines.append(f"| Engine-only (no venue match) | `{len(engine_only)}` | — | — |")
    lines.append(f"| Venue-only  (no engine match) | `{len(venue_only)}` | — | — |")
    lines.append(f"| Orphan rate | `{orphan_pct:.2f}%` "
                 f"| `≤ {orphan_threshold_pct}%` | {check(orphan_ok)} |")
    lines.append("")
    lines.append("## Price deltas (matched fills, abs bps)")
    lines.append("")
    lines.append("| Pct | Δ (bps) |")
    lines.append("|---|---:|")
    lines.append(f"| p50 | `{p50_bps:.2f}` |")
    lines.append(f"| p95 | `{p95_bps:.2f}` (threshold `≤ {price_bps_threshold_p95}`) "
                 f"{check(price_ok)} |")
    lines.append(f"| p99 | `{p99_bps:.2f}` |")
    lines.append("")
    lines.append("## Timestamp deltas (matched fills, abs seconds)")
    lines.append("")
    lines.append("| Pct | Δ (s) |")
    lines.append("|---|---:|")
    lines.append(f"| p50 | `{p50_ts:.0f}` |")
    lines.append(f"| p95 | `{p95_ts:.0f}` |")
    lines.append("")
    if engine_only:
        lines.append("## Engine-only fills (no venue match)")
        lines.append("")
        lines.append("| ts | market | venue | side | size | price |")
        lines.append("|---|---|---|---|---:|---:|")
        for ef in engine_only[:50]:
            lines.append(f"| `{ef.ts}` | `{ef.market}` | `{ef.venue}` "
                         f"| `{ef.side}` | `{ef.size}` | `${ef.price:.4f}` |")
        if len(engine_only) > 50:
            lines.append(f"| ... | _({len(engine_only) - 50} more)_ |  |  |  |  |")
        lines.append("")
    if venue_only:
        lines.append("## Venue-only fills (no engine match)")
        lines.append("")
        lines.append("| ts | market | venue | side | size | price |")
        lines.append("|---|---|---|---|---:|---:|")
        for vf in venue_only[:50]:
            lines.append(f"| `{vf.ts}` | `{vf.market}` | `{vf.venue}` "
                         f"| `{vf.side}` | `{vf.size}` | `${vf.price:.4f}` |")
        if len(venue_only) > 50:
            lines.append(f"| ... | _({len(venue_only) - 50} more)_ |  |  |  |  |")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Generated by `scripts/reconcile_fills.py` — Phase 1 T1.4.")
    return "\n".join(lines), all_ok


# ---------------------------------------------------------------------------
# Public API (used by tests)
# ---------------------------------------------------------------------------

def reconcile(
    engine_fills: Sequence[Fill],
    venue_fills: Sequence[Fill],
    ts_window_s: int = 60,
) -> dict:
    """Programmatic reconciliation — returns dict suitable for JSON.

    Used by `scripts/reconcile_fills.py` and future `/api/v1/paper/{id}/reconciliation`.
    """
    matches, engine_only, venue_only = _match_fills(
        engine_fills, venue_fills, ts_window_s=ts_window_s
    )
    price_bps = [abs(_price_bps_delta(e.price, v.price)) for e, v in matches]
    ts_deltas = [abs(_ts_delta(e.ts, v.ts)) for e, v in matches]
    return {
        "engine_count": len(engine_fills),
        "venue_count": len(venue_fills),
        "match_count": len(matches),
        "engine_only_count": len(engine_only),
        "venue_only_count": len(venue_only),
        "price_bps_p50": _percentile(price_bps, 0.50),
        "price_bps_p95": _percentile(price_bps, 0.95),
        "price_bps_p99": _percentile(price_bps, 0.99),
        "ts_delta_p50": _percentile([float(x) for x in ts_deltas], 0.50),
        "ts_delta_p95": _percentile([float(x) for x in ts_deltas], 0.95),
        # Histogram bins for UI display — log-scale buckets at the
        # boundaries traders actually look for (1 / 2 / 5 / 10 / 20 /
        # 50 / 100 bps). Each entry is {label, lo, hi, count}; the
        # last bin is open-ended (`hi` is None).
        "price_bps_histogram": _bps_histogram(price_bps),
    }


_BPS_BIN_EDGES = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0)


def _bps_histogram(values: Sequence[float]) -> list:
    """Bucket bps deltas into log-scale bins for tearsheet display.
    Returns a list of {label, lo, hi, count} dicts; the last bucket
    catches everything above 100 bps (`hi=None`)."""
    bins = []
    for i, lo in enumerate(_BPS_BIN_EDGES):
        hi = _BPS_BIN_EDGES[i + 1] if i + 1 < len(_BPS_BIN_EDGES) else None
        bins.append({"label": f"{int(lo)}-{int(hi)}" if hi is not None else f"{int(lo)}+",
                     "lo": lo, "hi": hi, "count": 0})
    for v in values:
        for b in bins:
            hi = b["hi"]
            if hi is None or v < hi:
                if v >= b["lo"]:
                    b["count"] += 1
                    break
    return bins


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--session", required=True,
                        help="Paper session ID (engine source)")
    parser.add_argument("--fills-csv", type=Path, required=True,
                        help="Venue-exported fills CSV")
    parser.add_argument("-o", "--out", type=Path, default=None,
                        help="Output markdown path. Default: "
                             "artifacts/reconciliation/{session}-{date}.md")
    parser.add_argument("--ts-window-s", type=int, default=60,
                        help="Max ts difference (seconds) to consider a match")
    parser.add_argument("--price-bps-p95-threshold", type=float, default=10.0,
                        help="Fail if matched price-delta p95 exceeds this (bps)")
    parser.add_argument("--orphan-pct-threshold", type=float, default=5.0,
                        help="Fail if orphan fills exceed this pct")
    args = parser.parse_args()

    engine_fills = load_engine_fills(args.session)
    venue_fills = load_venue_fills_csv(args.fills_csv)

    if not engine_fills:
        raise SystemExit(f"No engine fills found for session {args.session!r}")

    matches, engine_only, venue_only = _match_fills(
        engine_fills, venue_fills, ts_window_s=args.ts_window_s
    )
    markdown, ok = _render_report(
        session_id=args.session,
        engine_fills=engine_fills,
        venue_fills=venue_fills,
        matches=matches,
        engine_only=engine_only,
        venue_only=venue_only,
        price_bps_threshold_p95=args.price_bps_p95_threshold,
        orphan_threshold_pct=args.orphan_pct_threshold,
    )

    out = args.out
    if out is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out = Path("artifacts/reconciliation") / f"{args.session}-{date_str}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    print(f"wrote {out}")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
