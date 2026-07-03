"""Performance metrics — the §11.1 definitions, stated once and locked by goldens.

Two tools both reporting "Sharpe 1.8" under different conventions is the credibility
gap Flint closes, so the conventions are pinned here and nowhere else (§11.1):

* **Returns** — arithmetic, per bar, on **total account equity including accrued-but-
  unpaid funding**. The caller supplies that equity curve; these functions never
  reconstruct it, so what "equity" means is fixed at the source (a funding-arb that
  looks flat on realized PnL still shows its unrealized swings — hiding them defeats
  the product).
* **Annualization** — **365 days** (crypto trades weekends): ``bars_per_year =
  365·86400 / resolution_s`` and the factor is ``√(bars_per_year)``, printed on the
  summary next to the annualized numbers.
* **Sharpe** — mean(excess) / std, risk-free = 0 (configurable), sample std (ddof=1).
* **Sortino** — mean(excess) / downside deviation vs a 0 target.
* **Max drawdown** — largest peak-to-trough decline of the total-equity curve
  (incl. unrealized), as a non-negative fraction.
* **Effective evaluated range** (§6.3) — carried on every summary; two runs with
  different ranges are not comparable.

The Deflated Sharpe Ratio lives in :mod:`flint.research.deflated`; :class:`PerformanceReport`
composes it with these metrics and the engine's full-cost decomposition so the trust
report **always shows raw Sharpe + DSR + trial count** side by side (§11, D22).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from flint.engine.portfolio import FILL
from flint.engine.portfolio.events import EQUITY
from flint.engine.tearsheet import Tearsheet, build_tearsheet

if TYPE_CHECKING:  # avoid an import cycle — deflated imports sharpe() from here
    from flint.engine.portfolio import Event

    from .deflated import DeflatedSharpe

# 365-day year (crypto trades weekends), in seconds — the annualization base (§11.1).
SECONDS_PER_YEAR_365 = 365 * 86_400


def bar_returns(equity: Sequence[float]) -> list[float]:
    """Per-bar arithmetic returns of an equity curve: ``(E_t - E_{t-1}) / E_{t-1}``.

    ``equity`` is the total-account-equity series (incl. unrealized PnL + accrued
    unpaid funding, §11.1). A non-positive prior equity yields no return for that
    step (it is skipped) — a blown-up account has no meaningful percentage return.
    """
    out: list[float] = []
    for prev, cur in zip(equity, equity[1:]):
        prev_f = float(prev)
        if prev_f <= 0.0:
            continue
        out.append((float(cur) - prev_f) / prev_f)
    return out


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _sample_std(xs: Sequence[float]) -> float:
    """Sample standard deviation (ddof=1); 0.0 for fewer than two points."""
    n = len(xs)
    if n < 2:
        return 0.0
    mu = _mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))


def sharpe(returns: Sequence[float], *, rf: float = 0.0) -> float:
    """Per-bar Sharpe: ``mean(r - rf) / std(r)``, sample std, rf=0 by default (§11.1).

    Returns 0.0 when there is no dispersion or fewer than two returns — an undefined
    ratio is reported as zero rather than raising, so a degenerate run still tearsheets.
    """
    if len(returns) < 2:
        return 0.0
    sd = _sample_std(returns)
    if sd == 0.0:
        return 0.0
    return (_mean(returns) - rf) / sd


def downside_deviation(returns: Sequence[float], *, target: float = 0.0) -> float:
    """Root-mean-square of the below-target returns (over all n), vs ``target`` (§11.1)."""
    if not returns:
        return 0.0
    sq = [min(r - target, 0.0) ** 2 for r in returns]
    return math.sqrt(sum(sq) / len(returns))


def sortino(returns: Sequence[float], *, target: float = 0.0) -> float:
    """Per-bar Sortino: ``mean(r - target) / downside_deviation`` (§11.1).

    With no downside dispersion the ratio is ``+inf`` if the mean excess is positive
    (all gains, no downside) and 0.0 otherwise — reported honestly, not clamped.
    """
    if not returns:
        return 0.0
    dd = downside_deviation(returns, target=target)
    excess = _mean(returns) - target
    if dd == 0.0:
        return math.inf if excess > 0.0 else 0.0
    return excess / dd


def max_drawdown(equity: Sequence[float]) -> float:
    """Largest peak-to-trough decline of ``equity`` as a non-negative fraction (§11.1).

    Computed on the total-equity curve (incl. unrealized), so an arb that is flat on
    realized PnL still reports its violent unrealized swings.
    """
    peak = -math.inf
    worst = 0.0
    for e in equity:
        e_f = float(e)
        if e_f > peak:
            peak = e_f
        if peak > 0.0:
            dd = (peak - e_f) / peak
            if dd > worst:
                worst = dd
    return worst


def bars_per_year(resolution_s: int) -> float:
    """How many bars of ``resolution_s`` fit in a 365-day year (§11.1)."""
    if resolution_s <= 0:
        raise ValueError("resolution_s must be > 0")
    return SECONDS_PER_YEAR_365 / resolution_s


def annualization_factor(resolution_s: int) -> float:
    """The √(bars-per-year) factor printed on the tearsheet (§11.1)."""
    return math.sqrt(bars_per_year(resolution_s))


@dataclass(frozen=True)
class MetricSummary:
    """The §11.1 risk/return numbers with their annualization factor and the effective
    evaluated range they cover — the range is stated next to the metrics because two
    runs over different ranges are not comparable."""

    sharpe: float
    annualized_sharpe: float
    sortino: float
    annualized_sortino: float
    max_drawdown: float
    mean_bar_return: float
    n_returns: int
    resolution_s: int
    bars_per_year: float
    annualization_factor: float
    evaluated_start_ts: int | None = None
    evaluated_end_ts: int | None = None


def summarize_equity(
    equity: Sequence[float],
    *,
    resolution_s: int,
    rf: float = 0.0,
    evaluated_start_ts: int | None = None,
    evaluated_end_ts: int | None = None,
) -> MetricSummary:
    """Fold a total-equity curve into the §11.1 metric summary.

    ``equity`` is the per-bar total account equity (incl. unrealized + accrued unpaid
    funding). ``resolution_s`` sets the 365-day annualization. The evaluated range is
    carried through verbatim (the post-gate effective range, §6.3).
    """
    rets = bar_returns(equity)
    factor = annualization_factor(resolution_s)
    raw_sharpe = sharpe(rets, rf=rf)
    raw_sortino = sortino(rets, target=rf)
    return MetricSummary(
        sharpe=raw_sharpe,
        annualized_sharpe=raw_sharpe * factor,
        sortino=raw_sortino,
        annualized_sortino=(
            raw_sortino * factor if math.isfinite(raw_sortino) else raw_sortino
        ),
        max_drawdown=max_drawdown(equity),
        mean_bar_return=_mean(rets),
        n_returns=len(rets),
        resolution_s=resolution_s,
        bars_per_year=bars_per_year(resolution_s),
        annualization_factor=factor,
        evaluated_start_ts=evaluated_start_ts,
        evaluated_end_ts=evaluated_end_ts,
    )


def win_rate_from_events(events: Iterable[Event]) -> float | None:
    """Fraction of closing trades that realized a positive PnL (§11 tearsheet).

    Only fills that realized PnL (position-reducing) count as decided trades; opens
    (realized_pnl == 0) are excluded. ``None`` when there are no decided trades.
    """
    decided = 0
    wins = 0
    for ev in events:
        if ev.kind != FILL:
            continue
        pnl = float(ev.payload.get("realized_pnl", 0.0))
        if pnl == 0.0:
            continue
        decided += 1
        if pnl > 0.0:
            wins += 1
    return wins / decided if decided else None


def equity_series_from_events(events: Iterable[Event]) -> list[float]:
    """Fold the per-bar EQUITY event stream into a total-equity curve (§6.1/§11.1).

    The trivial extractor promised by the 6.2 carry-forward, now that slice 7.3 emits an
    additive per-bar ``EQUITY`` event: one snapshot per bar carrying total equity (cash +
    unrealized, inclusive of accrued unpaid funding — Decimal-as-str) at the bar boundary.
    Taken in event order, that is exactly the total-equity series ``build_report`` and
    ``summarize_equity`` expect. Non-EQUITY events are ignored; ``EQUITY`` is additive and
    changes no other kind's ordering, so folding it is side-effect-free.

    ``build_report``'s explicit-``equity`` parameter stays the stable interface — a caller
    that has an engine event log now passes ``equity_series_from_events(events)`` instead
    of threading an equity series by hand.
    """
    return [float(ev.payload["equity"]) for ev in events if ev.kind == EQUITY]


@dataclass(frozen=True)
class PerformanceReport:
    """The trust report: §11.1 metrics + the engine's full-cost decomposition + the
    Deflated Sharpe and its trial count. :meth:`describe` **always** surfaces raw
    Sharpe, DSR, and trial count together (§11, D22) — even when no optimization ran,
    in which case DSR is reported as honestly unavailable, not omitted."""

    metrics: MetricSummary
    cost: Tearsheet | None = None
    deflated: "DeflatedSharpe | None" = None
    n_trials: int = 0
    win_rate: float | None = None

    def describe(self) -> str:
        m = self.metrics
        rng = (
            f"[{m.evaluated_start_ts}, {m.evaluated_end_ts}]"
            if m.evaluated_start_ts is not None
            else "[unspecified]"
        )
        dsr = (
            f"{self.deflated.dsr:.4f} over {self.deflated.n_trials} trials"
            if self.deflated is not None
            else f"n/a ({self.n_trials} trials — no DSR computed)"
        )
        lines = [
            f"evaluated range {rng}  (resolution {m.resolution_s}s, "
            f"×√{m.bars_per_year:.0f}/yr = {m.annualization_factor:.4f})",
            f"Sharpe (raw): {m.sharpe:.4f}   annualized: {m.annualized_sharpe:.4f}",
            f"Deflated Sharpe: {dsr}",
            f"trials: {self.n_trials}",
            f"Sortino (raw): {m.sortino:.4f}   annualized: {m.annualized_sortino:.4f}",
            f"max drawdown: {m.max_drawdown:.4%}",
        ]
        if self.win_rate is not None:
            lines.append(f"win rate: {self.win_rate:.4%}")
        if self.cost is not None:
            lines.append(
                f"cost: net {self.cost.net_pnl} "
                f"(trading {self.cost.trading_pnl}, funding {self.cost.funding}, "
                f"fees {self.cost.fees}, slippage {self.cost.slippage_cost})"
            )
        return "\n".join(lines)


def build_report(
    equity: Sequence[float],
    *,
    resolution_s: int,
    events: Iterable[Event] | None = None,
    deflated: "DeflatedSharpe | None" = None,
    rf: float = 0.0,
    evaluated_start_ts: int | None = None,
    evaluated_end_ts: int | None = None,
) -> PerformanceReport:
    """Assemble the full trust report (§11).

    Metrics come from ``equity`` (§11.1); the full-cost decomposition and win rate come
    from the engine event log if ``events`` is given; ``deflated`` supplies the DSR and
    the trial count. When the range is not passed explicitly it is taken from the cost
    decomposition so the report still states the range it covers.
    """
    cost = None
    win_rate = None
    events_list = list(events) if events is not None else None
    if events_list is not None:
        cost = build_tearsheet(events_list)
        win_rate = win_rate_from_events(events_list)
        if evaluated_start_ts is None:
            evaluated_start_ts = cost.evaluated_start_ts
        if evaluated_end_ts is None:
            evaluated_end_ts = cost.evaluated_end_ts

    metrics = summarize_equity(
        equity,
        resolution_s=resolution_s,
        rf=rf,
        evaluated_start_ts=evaluated_start_ts,
        evaluated_end_ts=evaluated_end_ts,
    )
    return PerformanceReport(
        metrics=metrics,
        cost=cost,
        deflated=deflated,
        n_trials=deflated.n_trials if deflated is not None else 0,
        win_rate=win_rate,
    )


__all__ = [
    "SECONDS_PER_YEAR_365",
    "bar_returns",
    "sharpe",
    "downside_deviation",
    "sortino",
    "max_drawdown",
    "bars_per_year",
    "annualization_factor",
    "MetricSummary",
    "summarize_equity",
    "win_rate_from_events",
    "equity_series_from_events",
    "PerformanceReport",
    "build_report",
]
