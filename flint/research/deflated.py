"""Deflated Sharpe Ratio — correcting for selection across many trials (D22, §11.1).

Walk-forward controls overfit for *one* strategy version; it cannot correct for
*selecting* the best of thousands of trials against the same out-of-sample window. A
Sharpe of 1.8 found in 10 trials and the same 1.8 found in 10,000 trials are very
different evidence, and the Deflated Sharpe Ratio says so: it is the probability that
the observed Sharpe exceeds the Sharpe you would expect to find *by luck alone* given
how many trials you ran and how much their Sharpes varied.

Source: Bailey, D. H. and López de Prado, M. (2014), "The Deflated Sharpe Ratio:
Correcting for Selection Bias, Backtest Overfitting, and Non-Normality", The Journal
of Portfolio Management 40 (5), 94-107. https://doi.org/10.3905/jpm.2014.40.5.094

Formulas (all Sharpes are non-annualized / per-observation, in the same frequency):

    Probabilistic Sharpe Ratio at a benchmark SR*:

        PSR(SR*) = Z[ (SR_hat - SR*) · sqrt(n - 1)
                      / sqrt(1 - g3·SR_hat + ((g4 - 1)/4)·SR_hat^2) ]

    where Z is the standard-normal CDF, n the number of returns, g3 the skewness and
    g4 the (non-excess) kurtosis of the returns, and SR_hat the observed Sharpe.

    Deflated Sharpe Ratio = PSR evaluated at the expected maximum Sharpe under the
    null of zero skill across N trials:

        SR0 = sqrt(V) · [ (1 - g)·Z^-1(1 - 1/N) + g·Z^-1(1 - 1/(N·e)) ]

    where V is the variance of the N trials' Sharpe estimates, g the Euler-Mascheroni
    constant, e Euler's number, and Z^-1 the standard-normal inverse CDF. As N grows,
    SR0 rises, so the same observed Sharpe deflates toward 0.5 and below — the
    multiple-testing penalty that keeps an uncapped optimizer honest (D22, §11).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist
from typing import TYPE_CHECKING

from .tearsheet import sharpe

if TYPE_CHECKING:
    from .walkforward import WalkForwardResult

# Euler-Mascheroni constant — the weight in the expected-maximum-Sharpe estimator.
EULER_MASCHERONI = 0.5772156649015329

_NORMAL = NormalDist()


def _central_moment(returns: Sequence[float], k: int) -> float:
    n = len(returns)
    mu = sum(returns) / n
    return sum((r - mu) ** k for r in returns) / n


def skewness(returns: Sequence[float]) -> float:
    """Population skewness ``m3 / m2**1.5`` (0.0 for a degenerate, zero-variance series)."""
    if len(returns) < 2:
        return 0.0
    m2 = _central_moment(returns, 2)
    if m2 == 0.0:
        return 0.0
    return _central_moment(returns, 3) / (m2**1.5)


def kurtosis(returns: Sequence[float], *, excess: bool = False) -> float:
    """Population kurtosis ``m4 / m2**2``; non-excess by default (normal = 3.0).

    A degenerate zero-variance series returns the normal value (3.0, or 0.0 excess).
    """
    normal = 3.0
    if len(returns) < 2:
        return (normal - 3.0) if excess else normal
    m2 = _central_moment(returns, 2)
    if m2 == 0.0:
        return (normal - 3.0) if excess else normal
    k = _central_moment(returns, 4) / (m2**2)
    return k - 3.0 if excess else k


def _variance(xs: Sequence[float]) -> float:
    """Sample variance (ddof=1) of the trial Sharpes."""
    n = len(xs)
    mu = sum(xs) / n
    return sum((x - mu) ** 2 for x in xs) / (n - 1)


def expected_max_sharpe(trial_sharpes: Sequence[float]) -> float:
    """The Sharpe you would expect to find by luck alone across ``N`` trials (SR0).

    Needs at least two trials (a variance and an order statistic are undefined for
    one). ``V`` is the sample variance of the trial Sharpes; the bracketed term is the
    expected maximum of ``N`` standard normals (Bailey & López de Prado, 2014).
    """
    n_trials = len(trial_sharpes)
    if n_trials < 2:
        raise ValueError("expected_max_sharpe needs at least 2 trials")
    sd = math.sqrt(_variance(trial_sharpes))
    g = EULER_MASCHERONI
    return sd * (
        (1.0 - g) * _NORMAL.inv_cdf(1.0 - 1.0 / n_trials)
        + g * _NORMAL.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    )


def probabilistic_sharpe(
    observed_sharpe: float,
    benchmark_sharpe: float,
    n_returns: int,
    skew: float,
    kurt: float,
) -> float:
    """PSR: probability the true Sharpe exceeds ``benchmark_sharpe`` (Bailey/LdP 2014).

    All Sharpes are per-observation. ``kurt`` is non-excess. Raises if the returns'
    moments make the estimator's variance non-positive (a pathological input).
    """
    if n_returns < 2:
        raise ValueError("probabilistic_sharpe needs at least 2 returns")
    radicand = 1.0 - skew * observed_sharpe + ((kurt - 1.0) / 4.0) * observed_sharpe**2
    if radicand <= 0.0:
        raise ValueError(
            f"non-positive Sharpe-estimator variance ({radicand}); moments are pathological"
        )
    z = (observed_sharpe - benchmark_sharpe) * math.sqrt(n_returns - 1) / math.sqrt(
        radicand
    )
    return _NORMAL.cdf(z)


@dataclass(frozen=True)
class DeflatedSharpe:
    """The DSR and every input that produced it — so the number is auditable, not a
    black box. ``dsr`` is a probability in [0, 1]; a value below 0.5 means the observed
    Sharpe is *worse* than what selection across ``n_trials`` would produce by chance."""

    dsr: float
    observed_sharpe: float
    expected_max_sharpe: float
    n_trials: int
    n_returns: int
    skew: float
    kurtosis: float
    trial_sharpe_variance: float


def deflated_sharpe(
    returns: Sequence[float],
    trial_sharpes: Sequence[float],
) -> DeflatedSharpe:
    """Deflate the selected strategy's Sharpe by the trials that produced it (D22).

    ``returns`` is the selected strategy's per-bar return series (its track record);
    ``trial_sharpes`` is the Sharpe of *every* trial the optimizer evaluated during
    selection — pull these from a walk-forward result with :func:`trial_sharpes`. Both
    must be the same (per-bar) frequency for the deflation to be meaningful.
    """
    observed = sharpe(returns)
    skew = skewness(returns)
    kurt = kurtosis(returns)
    sr0 = expected_max_sharpe(trial_sharpes)
    dsr = probabilistic_sharpe(observed, sr0, len(returns), skew, kurt)
    return DeflatedSharpe(
        dsr=dsr,
        observed_sharpe=observed,
        expected_max_sharpe=sr0,
        n_trials=len(trial_sharpes),
        n_returns=len(returns),
        skew=skew,
        kurtosis=kurt,
        trial_sharpe_variance=_variance(trial_sharpes),
    )


def trial_sharpes(result: WalkForwardResult) -> tuple[float, ...]:
    """Pool every trial's score across all walk-forward windows — the ``N`` trials and
    their variance the DSR deflates by (the objective is the per-trial Sharpe, §11)."""
    return tuple(s for w in result.windows for s in w.trial_scores)


__all__ = [
    "EULER_MASCHERONI",
    "skewness",
    "kurtosis",
    "expected_max_sharpe",
    "probabilistic_sharpe",
    "DeflatedSharpe",
    "deflated_sharpe",
    "trial_sharpes",
]
