"""The ``Lab`` — the notebook/script surface over ``services/`` (§12, D8).

``Lab`` is the primary surface for the wedge user: a couple of lines in a notebook run
a funding-gated backtest, a walk-forward optimize, or start a paper session, and the
result object renders a trust tearsheet. Like every surface it drives the domain **only**
through ``services/`` under a :class:`~flint.ports.TenantContext` — it never touches the
engine or a store directly (§4, §17). The ports default to the local in-memory adapters
so ``Lab()`` is runnable out of the box, and are injectable so tests wire fixtures (D26).

The tearsheet is the honesty contract made visible (carry-forward (i), §11): it **always**
prints the raw Sharpe next to the Deflated Sharpe and the trial count — reporting DSR as
``n/a`` for an un-tuned single backtest rather than omitting it — with the annualization
factor and the exact evaluated range beside the metrics, and the per-run timing breakdown
(§19.2) so "why is my backtest slow" is answerable from the output.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from flint.data import DataManager
from flint.ports import TenantContext, UserDataPort
from flint.services import (
    BacktestRequest,
    OptimizeRequest,
    new_run_id,
    paper_snapshot,
    run_backtest,
    run_optimization,
    start_paper,
)

if TYPE_CHECKING:
    from flint.data.ingest.recorders import WsMessageSource
    from flint.live import PaperSession, RunResult
    from flint.research import ParamSpace


class Lab:
    """A tenant-scoped handle for backtests, optimizations, and paper sessions."""

    def __init__(
        self,
        tenant: TenantContext | None = None,
        *,
        user_data: UserDataPort | None = None,
        data: DataManager | None = None,
    ) -> None:
        from flint.adapters import InMemoryUserData

        self.tenant = tenant or TenantContext.local()
        self.user_data = user_data or InMemoryUserData()
        self.data = data or DataManager()

    # -- backtest --------------------------------------------------------------

    def backtest(
        self,
        strategy: str,
        *,
        universe: Sequence[str] = ("SOL-PERP",),
        venues: Sequence[str] = ("hyperliquid",),
        start_ms: int,
        end_ms: int,
        resolution_s: int = 3600,
        seed: int = 0,
        initial_capital: str = "100000",
        fill_mode: str = "auto",
        overrides: Mapping[str, Any] | None = None,
        signal_venues: Sequence[str] = (),
        run_id: str | None = None,
    ) -> "BacktestResult":
        """Run ``strategy`` over the range and return a :class:`BacktestResult`.

        A funding-gap over the range is a *rejected* result carried as data (§19.1),
        not an exception — inspect :attr:`BacktestResult.rejected`.
        """
        request = BacktestRequest(
            run_id=run_id or new_run_id(),
            strategy=strategy,
            universe=tuple(universe),
            venues=tuple(venues),
            start_ms=start_ms,
            end_ms=end_ms,
            resolution_s=resolution_s,
            fill_mode=fill_mode,
            seed=seed,
            initial_capital=initial_capital,
            overrides=dict(overrides or {}),
            signal_venues=tuple(signal_venues),
        )
        outcome = run_backtest(
            self.tenant, request, user_data=self.user_data, data=self.data
        )
        return BacktestResult(
            run_id=outcome.run_id,
            verdict=outcome.verdict,
            summary=dict(outcome.summary),
        )

    # -- optimize --------------------------------------------------------------

    def optimize(
        self,
        strategy: str,
        *,
        params: Sequence["ParamSpace | str"],
        universe: Sequence[str] = ("SOL-PERP",),
        venues: Sequence[str] = ("hyperliquid",),
        start_ms: int,
        end_ms: int,
        resolution_s: int = 3600,
        n_windows: int = 3,
        n_trials: int = 20,
        purge_bars: int = 0,
        embargo_bars: int = 0,
        label_horizon_bars: int = 1,
        direction: str = "maximize",
        seed: int = 0,
        initial_capital: str = "100000",
        run_id: str | None = None,
    ) -> "OptimizeResult":
        """Walk-forward search ``strategy`` over ``params`` and return the winner.

        ``params`` accepts :class:`~flint.research.ParamSpace` objects or the CLI
        ``name=lo:hi[:step]`` string form. The selected params are re-run over the full
        range and their Sharpe is deflated by the whole trial family (D22).
        """
        from flint.research import ParamSpace

        spaces = tuple(
            p if isinstance(p, ParamSpace) else ParamSpace.parse(p) for p in params
        )
        request = OptimizeRequest(
            run_id=run_id or new_run_id(),
            strategy=strategy,
            params=spaces,
            universe=tuple(universe),
            venues=tuple(venues),
            start_ms=start_ms,
            end_ms=end_ms,
            resolution_s=resolution_s,
            n_windows=n_windows,
            n_trials=n_trials,
            purge_bars=purge_bars,
            embargo_bars=embargo_bars,
            label_horizon_bars=label_horizon_bars,
            direction=direction,
            seed=seed,
            initial_capital=initial_capital,
        )
        outcome = run_optimization(
            self.tenant, request, user_data=self.user_data, data=self.data
        )
        return OptimizeResult(
            run_id=outcome.run_id,
            verdict=outcome.verdict,
            summary=dict(outcome.summary),
        )

    # -- paper -----------------------------------------------------------------

    def paper(
        self,
        strategy: str,
        *,
        market: str,
        resolution_s: int = 3600,
        initial_capital: str = "100000",
        seed: int = 0,
        overrides: Mapping[str, Any] | None = None,
        run_id: str | None = None,
    ) -> "PaperHandle":
        """Start a fresh paper session and return a handle to feed + monitor it.

        Paper trading is the same engine fed live rather than by replay (the parity
        promise); the returned handle carries the persisted ``run_id`` and streams its
        monitor snapshot. Its :meth:`PaperHandle.feed` drives one live-feed connection.
        """
        rid = run_id or new_run_id()
        session = start_paper(
            self.tenant,
            user_data=self.user_data,
            run_id=rid,
            strategy=strategy,
            market=market,
            resolution_s=resolution_s,
            initial_capital=initial_capital,
            seed=seed,
            overrides=dict(overrides or {}),
        )
        return PaperHandle(
            run_id=rid,
            tenant=self.tenant,
            user_data=self.user_data,
            session=session,
        )


# -- result objects ----------------------------------------------------------


@dataclass(frozen=True)
class BacktestResult:
    """A finished backtest: the summary blob plus a rendered trust tearsheet."""

    run_id: str
    verdict: str
    summary: Mapping[str, Any]

    @property
    def rejected(self) -> bool:
        return self.verdict == "rejected"

    @property
    def rejection(self) -> Mapping[str, Any] | None:
        return self.summary.get("rejected")

    @property
    def metrics(self) -> Mapping[str, Any]:
        return self.summary.get("metrics", {})

    def tearsheet(self) -> str:
        """The §11 trust tearsheet: raw Sharpe + DSR + trials + timing (carry-forward i)."""
        return _render_tearsheet(self.run_id, self.summary)


@dataclass(frozen=True)
class OptimizeResult:
    """A finished walk-forward search: the winner's tearsheet + the fold evidence."""

    run_id: str
    verdict: str
    summary: Mapping[str, Any]

    @property
    def metrics(self) -> Mapping[str, Any]:
        return self.summary.get("metrics", {})

    @property
    def best_params(self) -> Mapping[str, Any]:
        return self.summary.get("optimize", {}).get("best_params", {})

    def tearsheet(self) -> str:
        base = _render_tearsheet(self.run_id, self.summary)
        return base + "\n" + _render_optimize_block(self.summary.get("optimize", {}))


@dataclass(frozen=True)
class PaperHandle:
    """A running paper session: feed it a live connection, read its monitor snapshot."""

    run_id: str
    tenant: TenantContext
    user_data: UserDataPort
    session: "PaperSession"

    def feed(self, source: "WsMessageSource") -> "RunResult":
        """Consume one live-feed connection through the session's engine (§6.7)."""
        return self.session.feed(source)

    def snapshot(self) -> dict[str, Any]:
        """The current monitor snapshot (positions, funding, liq distance, drift)."""
        return paper_snapshot(self.tenant, self.run_id, user_data=self.user_data)


# -- rendering ---------------------------------------------------------------


def _ms(range_dict: Mapping[str, Any] | None) -> str:
    if not range_dict:
        return "[unspecified]"
    return f"[{range_dict.get('start_ms')}, {range_dict.get('end_ms')}]"


def _render_tearsheet(run_id: str, summary: Mapping[str, Any]) -> str:
    lines = [f"run {run_id}  ({summary.get('strategy', '?')})"]
    if summary.get("verdict") == "rejected":
        rej = summary.get("rejected", {})
        lines.append(f"  REJECTED [{rej.get('code')}]: {rej.get('message')}")
        if rej.get("missing"):
            lines.append(f"    missing: {rej['missing']}")
        if rej.get("hint"):
            lines.append(f"    fix: {rej['hint']}")
        return "\n".join(lines)

    m = summary.get("metrics", {})
    eff = summary.get("effective_range")
    lines.append(f"  evaluated range {_ms(eff)}")
    af = m.get("annualization_factor")
    lines.append(
        f"  Sharpe (raw): {_f(m.get('sharpe'))}   "
        f"annualized: {_f(m.get('annualized_sharpe'))}"
        + (f"   (× {_f(af)}/yr)" if af is not None else "")
    )
    lines.append(f"  Deflated Sharpe: {_dsr(summary)}")
    lines.append(
        f"  Sortino (raw): {_f(m.get('sortino'))}   "
        f"annualized: {_f(m.get('annualized_sortino'))}"
    )
    lines.append(f"  max drawdown: {_pct(m.get('max_drawdown'))}")
    if summary.get("win_rate") is not None:
        lines.append(f"  win rate: {_pct(summary['win_rate'])}")

    cost = summary.get("cost")
    if cost:
        lines.append(
            f"  cost: trading {_f(cost.get('trading_pnl'))}, "
            f"funding {_f(cost.get('funding'))}, fees {_f(cost.get('fees'))}, "
            f"slippage {_f(cost.get('slippage'))} "
            f"({cost.get('funding_settlements')} funding settlements)"
        )

    lines.extend(_render_timing(summary.get("timing", {})))

    rejections = summary.get("rejections") or []
    if rejections:
        lines.append(f"  in-run rejections: {len(rejections)}")
        for r in rejections[:5]:
            lines.append(
                f"    - {r.get('reason')}: {r.get('venue')}/{r.get('market')} "
                f"{r.get('action')} ({r.get('detail')})"
            )
    for fid in summary.get("fidelity_lines", []) or []:
        lines.append(f"  fidelity {fid}")
    return "\n".join(lines)


def _render_timing(timing: Mapping[str, Any]) -> list[str]:
    if not timing:
        return []
    total = sum(v for v in timing.values() if isinstance(v, (int, float)))
    parts = "  ".join(f"{k.replace('_ms', '')} {_f(v)}ms" for k, v in timing.items())
    # engine_run is the whole per-bar loop (fills + strategy + funding); see §19.2.
    return [f"  timing (total {_f(total)}ms): {parts}"]


def _render_optimize_block(block: Mapping[str, Any]) -> str:
    if not block:
        return ""
    lines = [
        f"  walk-forward: {block.get('n_windows')} windows, "
        f"{block.get('total_trials')} trials total, "
        f"mean OOS {_f(block.get('mean_oos'))}",
        f"  best params: {block.get('best_params')}",
    ]
    for w in block.get("windows", []):
        lines.append(
            f"    window {w.get('index')}: OOS {_f(w.get('oos_score'))} "
            f"(train {_f(w.get('train_score'))}, {w.get('n_trials')} trials)"
        )
    for warn in block.get("warnings", []):
        lines.append(f"    ! {warn}")
    return "\n".join(lines)


def _dsr(summary: Mapping[str, Any]) -> str:
    d = summary.get("deflated_sharpe")
    n = summary.get("n_trials", 0)
    if not d:
        return f"n/a ({n} trials — no DSR computed)"
    return f"{_f(d.get('dsr'))} over {d.get('n_trials', n)} trials"


def _f(value: Any) -> str:
    return f"{value:.4f}" if isinstance(value, (int, float)) else str(value)


def _pct(value: Any) -> str:
    return f"{value:.4%}" if isinstance(value, (int, float)) else str(value)
