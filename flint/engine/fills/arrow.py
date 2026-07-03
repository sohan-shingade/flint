"""Arrow-native Tier-A taker book-walk — the §19.4 depth-replay hot path.

The §19.4 throughput spike (`docs/redesign/spike-report.md`) fails the Tier-A
depth-replay budget by ~4× on the naive row-by-row path: `to_pylist` allocates a
Python object per level per snapshot, the interpreter dispatches per level, and
`Decimal(str(x))` lands in the innermost loop. None of that scales to ~47 M
snapshots in 15 min. This module is the design note's fix
(`docs/redesign/ARROW-FILL-PATH.md`): keep depth in Arrow, walk the book with
columnar kernels, batch by width, reduce money at batch boundaries only.

**The gate is bit-identity, not tolerance.** Every fill this kernel produces must
be byte-for-byte the fill `ClobFillModel._taker` (§6.3) produces on the same
snapshot — a vectorization that silently changed fill semantics would be worse
than the slow path. Bit-identity is achieved, not hoped for:

* Snapshots are bucketed by book width `L`, so each bucket is a dense `(n, L)`
  matrix and every per-snapshot reduction runs as one vectorized 2-D op.
* `np.cumsum(P*S, axis=1)` and `np.cumsum(S, axis=1)` reproduce the scalar's
  `notional += price*take` / `filled += take` *exactly* — `cumsum` is a sequential
  left-to-right scan per row, the same IEEE-754 rounding as the Python loop
  (verified against the scalar in the parity test).
* `np.subtract.accumulate([size, S], axis=1)` reproduces the scalar's
  `remaining -= take` subtraction chain exactly (a subtraction chain is *not*
  `order.size - cumsum`; only `subtract.accumulate` matches its rounding).
* `take_k = min(remaining_k, avail_k)` is `np.minimum` — the same clamp the scalar
  applies at the completing level.

Price rounding (`_round_sig`) and the `Decimal` fee are **not** vectorized: they
run once per *actual* fill in `materialize`, reusing the scalar's own functions,
so price/fee are bit-identical by reuse, not by re-derivation. Fills are sparse
(one per live order, not one per snapshot), so that cost is immaterial — the
book-walk arithmetic is the §19.4 bottleneck and that is what is batched.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyarrow as pa

from flint.core.models import Fill, Order, Side, TimeInForce

from ..money import money
from .base import FillResult
from .clob import _EPS, _round_sig


@dataclass(frozen=True)
class TakerWalk:
    """Vectorized book-walk result for a batch of snapshots (one order each).

    Every array is length ``n`` (the snapshot count) and indexed in the depth
    table's snapshot order. ``ok`` is the reject mask: ``False`` where the scalar
    would have returned ``None`` (no depth, touch beyond the band, FOK partial).
    ``vwap``/``slippage_bps`` are meaningful only where ``ok``.
    """

    filled: np.ndarray  # (n,) float64 — filled size (0 where not ok)
    vwap: np.ndarray  # (n,) float64 — VWAP fill price (0 where not ok)
    slippage_bps: np.ndarray  # (n,) float64
    is_partial: np.ndarray  # (n,) bool
    clipped: np.ndarray  # (n,) bool — an oracle-band clip stopped the walk
    ok: np.ndarray  # (n,) bool — a fill resulted


def depth_level_arrays(
    depth: pa.Table, side: Side
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Zero-copy flat level arrays for the side a taker crosses (asks buy, bids sell).

    Returns ``(px, sz, level_offsets)`` where ``px``/``sz`` are flat float64 views
    over every level in the table (best-first within each snapshot) and
    ``level_offsets`` (length ``n+1``) delimits each snapshot's levels. No Python
    objects are materialized — the whole point (§19.4 root cause).
    """
    buy = side is Side.LONG
    col = depth.column("asks" if buy else "bids").combine_chunks()
    level_offsets = np.asarray(col.offsets)  # snapshot -> level-index boundaries
    inner = col.values  # list<float64>, one [px, sz] per level
    flat = inner.values.to_numpy(zero_copy_only=True)  # [px, sz, px, sz, ...]
    # DEPTH_SCHEMA guarantees exactly 2 floats/level, contiguous → strided views.
    return flat[0::2], flat[1::2], level_offsets


def taker_walk_batch(
    px: np.ndarray,
    sz: np.ndarray,
    level_offsets: np.ndarray,
    *,
    buy: bool,
    order_size: float,
    band_hi: np.ndarray | float | None = None,
    band_lo: np.ndarray | float | None = None,
    eps: float = _EPS,
) -> TakerWalk:
    """Walk every snapshot's book for a fixed-size taker order, vectorized (§6.3).

    Mirrors ``ClobFillModel._taker`` bit-for-bit: cross the touch, walk depth for a
    VWAP, honour the Hyperliquid oracle band (a prefix clip since depth is
    price-sorted), clamp the completing level with ``min(remaining, avail)``.
    ``band_hi`` (buy) / ``band_lo`` (sell) may be a scalar or a per-snapshot array;
    ``None`` = no band. Snapshots are processed in width buckets so each reduction
    is a single 2-D kernel.
    """
    n = len(level_offsets) - 1
    filled = np.zeros(n, dtype=np.float64)
    vwap = np.zeros(n, dtype=np.float64)
    slippage = np.zeros(n, dtype=np.float64)
    is_partial = np.zeros(n, dtype=bool)
    clipped = np.zeros(n, dtype=bool)
    ok = np.zeros(n, dtype=bool)
    if n == 0:
        return TakerWalk(filled, vwap, slippage, is_partial, clipped, ok)

    widths = np.diff(level_offsets)  # levels per snapshot
    band = band_hi if buy else band_lo
    band_arr = _as_per_snapshot(band, n)

    # Bucket by width so each bucket reshapes to a dense (n_L, L) matrix. Real HL
    # l2Book is fixed-depth (~20 levels), so this is typically one bucket.
    for width in np.unique(widths):
        rows = np.flatnonzero(widths == width)
        if width == 0:
            continue  # empty book → no fill (ok stays False)
        _walk_bucket(
            rows=rows,
            width=int(width),
            level_offsets=level_offsets,
            px=px,
            sz=sz,
            order_size=order_size,
            band_row=None if band_arr is None else band_arr[rows],
            buy=buy,
            eps=eps,
            out=(filled, vwap, slippage, is_partial, clipped, ok),
        )
    return TakerWalk(filled, vwap, slippage, is_partial, clipped, ok)


def _as_per_snapshot(band: np.ndarray | float | None, n: int) -> np.ndarray | None:
    if band is None:
        return None
    arr = np.asarray(band, dtype=np.float64)
    return np.full(n, float(arr)) if arr.ndim == 0 else arr


def _walk_bucket(
    *,
    rows: np.ndarray,
    width: int,
    level_offsets: np.ndarray,
    px: np.ndarray,
    sz: np.ndarray,
    order_size: float,
    band_row: np.ndarray | None,
    buy: bool,
    eps: float,
    out: tuple[np.ndarray, ...],
) -> None:
    """Fill one width bucket in place. All rows here have exactly ``width`` levels."""
    filled, vwap, slippage, is_partial, clipped, ok = out
    starts = level_offsets[rows]  # first level index of each snapshot
    idx = starts[:, None] + np.arange(width)[None, :]  # (m, width) gather matrix
    P = px[idx]  # (m, width) prices, best-first
    S = sz[idx]  # (m, width) sizes

    # -- usable prefix per row: band cut and zero-size cut both truncate (break) --
    if band_row is None:
        m_band = np.full(len(rows), width)
    else:
        in_band = P <= band_row[:, None] if buy else P >= band_row[:, None]
        m_band = _first_false(in_band, width)  # prices are sorted → a prefix
    m_size = _first_true(S <= 0.0, width)  # take<=0 breaks the walk
    limit = np.minimum(m_band, m_size)  # levels 0..limit-1 are walkable
    col = np.arange(width)

    # -- reductions: cumsum reproduces `+= take`; subtract.accumulate the `remaining` chain --
    csize = np.cumsum(S, axis=1)  # filled after full levels
    cnot = np.cumsum(P * S, axis=1)  # notional after full levels
    rem = np.subtract.accumulate(
        np.concatenate([np.full((len(rows), 1), order_size), S], axis=1), axis=1
    )  # (m, width+1): rem[:,0]=order_size, rem[:,i+1]=remaining after full take of level i

    # completion level k = first walkable level whose full take clears the order
    done = (rem[:, 1:] <= eps) & (col[None, :] < limit[:, None])
    completed = done.any(axis=1)
    k = done.argmax(axis=1)  # valid only where completed

    r = np.arange(len(rows))
    # -- completed rows: full levels 0..k-1 + a clamped partial at k --
    if completed.any():
        kc = k[completed]
        rc = r[completed]
        take_k = np.minimum(rem[rc, kc], S[rc, kc])  # min(remaining, avail)
        pre = np.where(kc > 0, csize[rc, np.maximum(kc - 1, 0)], 0.0)
        pre_not = np.where(kc > 0, cnot[rc, np.maximum(kc - 1, 0)], 0.0)
        f = pre + take_k
        notn = pre_not + P[rc, kc] * take_k
        _emit(rows[rc], f, notn, P[rc, 0], order_size, False, eps, out)

    # -- not completed: order ran past walkable depth → partial (or reject) --
    part = ~completed
    if part.any():
        rp = r[part]
        lim = limit[part]
        has = lim > 0
        rp2 = rp[has]
        li = lim[has] - 1  # last walkable level
        f = csize[rp2, li]
        notn = cnot[rp2, li]
        # band clip drives the flag only when the band (not a zero size / book end)
        # stopped the walk before completion (scalar checks the band first).
        clip = (m_band[rp2] < width) & (m_band[rp2] <= m_size[rp2])
        _emit(rows[rp2], f, notn, P[rp2, 0], order_size, clip, eps, out)


def _emit(
    dst: np.ndarray,
    filled_v: np.ndarray,
    notional_v: np.ndarray,
    touch: np.ndarray,
    order_size: float,
    clip: np.ndarray | bool,
    eps: float,
    out: tuple[np.ndarray, ...],
) -> None:
    """Scatter a computed group's fills into the output arrays (filled>0 only)."""
    filled, vwap, slippage, is_partial, clipped, ok = out
    good = filled_v > 0  # scalar: `if filled <= 0: return None`
    if not good.any():
        return
    d = dst[good]
    f = filled_v[good]
    v = notional_v[good] / f
    t = touch[good]
    filled[d] = f
    vwap[d] = v
    slippage[d] = np.abs(v - t) / t * 1e4
    is_partial[d] = f < order_size - eps
    clipped[d] = clip[good] if isinstance(clip, np.ndarray) else clip
    ok[d] = True


def _first_false(mask: np.ndarray, width: int) -> np.ndarray:
    """Index of the first ``False`` per row (or ``width`` if all True). Prefix mask."""
    bad = ~mask
    return np.where(bad.any(axis=1), bad.argmax(axis=1), width)


def _first_true(mask: np.ndarray, width: int) -> np.ndarray:
    """Index of the first ``True`` per row (or ``width`` if none)."""
    return np.where(mask.any(axis=1), mask.argmax(axis=1), width)


# --- materialization: raw walk arrays -> Fill dataclasses (sparse, per fill) -----


def materialize(
    walk: TakerWalk,
    *,
    order: Order,
    ts: np.ndarray,
    price_sig_figs: int,
    taker_fee_rate: float,
    tier: str = "A",
) -> list[FillResult | None]:
    """Turn walk arrays into per-snapshot ``FillResult``\\ s, bit-identical to scalar.

    ``_round_sig`` and the ``Decimal`` fee are the scalar's own functions — price
    and fee are identical by reuse. FOK partials become ``None`` (all-or-nothing).
    One entry per snapshot; ``None`` where the scalar would reject. This is the
    low-volume boundary (one object per fill), never the hot loop.
    """
    out: list[FillResult | None] = []
    fok = order.tif is TimeInForce.FOK
    for i in range(len(walk.filled)):
        if not walk.ok[i]:
            out.append(None)
            continue
        partial = bool(walk.is_partial[i])
        if partial and fok:
            out.append(None)  # fill-or-kill: all or nothing
            continue
        price = _round_sig(float(walk.vwap[i]), price_sig_figs)
        size = float(walk.filled[i])
        fee = float(money(price) * money(size) * money(taker_fee_rate))
        fill = Fill(
            market=order.market,
            side=order.side,
            price=price,
            size=size,
            fee=fee,
            ts=int(ts[i]),
            client_order_id=order.client_order_id,
            is_partial=partial,
            slippage_bps=float(walk.slippage_bps[i]),
            venue=order.venue,
            liquidity="taker",
            fidelity_tier=tier,
        )
        flags = ("oracle_band_clipped",) if walk.clipped[i] else ()
        out.append(FillResult(fill=fill, flags=flags))
    return out


def arrow_taker_fills(
    depth: pa.Table,
    order: Order,
    *,
    taker_fee_rate: float,
    price_sig_figs: int = 0,
    oracle_price: np.ndarray | float = 0.0,
    oracle_band_bps: float = 0.0,
    tier: str = "A",
) -> list[FillResult | None]:
    """Fill a fixed taker ``order`` against every snapshot in ``depth`` (§6.3).

    The Arrow-native equivalent of calling ``ClobFillModel().fill(order, ctx)`` per
    snapshot for a taker (market or crossing limit) — bit-identical, one pass.
    ``oracle_price`` may be scalar or a per-snapshot array; a non-positive price or
    ``oracle_band_bps <= 0`` means no band (matching ``ClobFillModel._band``).
    """
    px, sz, offsets = depth_level_arrays(depth, order.side)
    band_hi, band_lo = _band_bounds(oracle_price, oracle_band_bps, len(depth))
    walk = taker_walk_batch(
        px,
        sz,
        offsets,
        buy=order.side is Side.LONG,
        order_size=order.size,
        band_hi=band_hi,
        band_lo=band_lo,
    )
    ts = depth.column("ts").to_numpy(zero_copy_only=False)
    return materialize(
        walk,
        order=order,
        ts=ts,
        price_sig_figs=price_sig_figs,
        taker_fee_rate=taker_fee_rate,
        tier=tier,
    )


def _band_bounds(
    oracle_price: np.ndarray | float, oracle_band_bps: float, n: int
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """The (hi, lo) oracle band per snapshot, or (None, None) if not enforced."""
    price = _as_per_snapshot(oracle_price, n)
    if oracle_band_bps <= 0 or price is None:
        return None, None
    enforced = price > 0.0
    if not enforced.any():
        return None, None
    band = oracle_band_bps / 1e4
    # Where a snapshot has no oracle price, ±inf leaves its walk unclipped.
    hi = np.where(enforced, price * (1 + band), np.inf)
    lo = np.where(enforced, price * (1 - band), -np.inf)
    return hi, lo
