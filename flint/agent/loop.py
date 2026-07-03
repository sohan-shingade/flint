"""The agentic development loop — author → validate → backtest → revise (§13.1).

This is the harness §13's "first-class goal" describes: an agent authors a
strategy, Flint validates and backtests it, the agent reads *structured* feedback
(never a stack trace or a human tearsheet), and refines. The loop here is the
control flow of that cycle over :class:`~flint.mcp_srv.tools.AgentTools`; the
*author* — how the next revision is produced — is injected, so a scripted author
(deterministic fixtures, D26) and a real LLM-backed author drive the identical
loop.

Each turn: ``validate_strategy`` gates the source (structured errors *before* any
run, D25); a valid strategy is backtested through the user-source path; the
result is diagnosed via ``explain_failure``. The loop converges when a run is
valid, not rejected, and carries no failure reasons — otherwise the structured
feedback is handed back to the author for the next revision.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from flint.mcp_srv.tools import AgentTools

#: An author maps the last structured :class:`Feedback` (``None`` on the first
#: turn) to the next strategy source, or ``None`` to give up.
StrategyAuthor = Callable[["Feedback | None"], "str | None"]


@dataclass(frozen=True)
class Feedback:
    """The structured signal handed back to the author to drive the next revision.

    ``stage`` says where the last attempt stopped — ``"validation"`` (screen or
    sandbox error, or a leak), ``"rejected"`` (a funding hard-gate gap), or
    ``"results"`` (it ran but did poorly). ``payload`` is the raw structured body
    from the tool, so the author reasons over machine-readable JSON, not prose.
    """

    stage: str
    payload: dict[str, Any]
    iteration: int


@dataclass
class AgentIteration:
    """One turn of the loop: the source tried and every structured response to it."""

    index: int
    source: str
    status: str = "pending"  # invalid | rejected | needs_revision | clean
    validation: dict[str, Any] | None = None
    run: dict[str, Any] | None = None
    verdict: str | None = None
    results: dict[str, Any] | None = None
    diagnosis: dict[str, Any] | None = None

    @property
    def failure_reasons(self) -> list[dict[str, Any]]:
        return list((self.diagnosis or {}).get("reasons", []))


@dataclass
class AgentSession:
    """The transcript of a loop run — every iteration, and whether it converged."""

    iterations: list[AgentIteration] = field(default_factory=list)
    converged: bool = False

    @property
    def n_iterations(self) -> int:
        return len(self.iterations)

    def final(self) -> AgentIteration | None:
        return self.iterations[-1] if self.iterations else None


def sequence_author(sources: Sequence[str]) -> StrategyAuthor:
    """An author that yields ``sources`` in order, ignoring feedback (fixtures, D26).

    Useful for a scripted acceptance run: a hand-authored sequence of revisions
    (a bad import, a runtime error, a leak, a clean strategy) exercises every
    structured-feedback branch deterministically. Returns ``None`` once exhausted.
    """
    queue = list(sources)

    def author(_feedback: Feedback | None) -> str | None:
        return queue.pop(0) if queue else None

    return author


def run_agent_loop(
    tools: AgentTools,
    author: StrategyAuthor,
    *,
    backtest_kwargs: dict[str, Any] | None = None,
    max_iterations: int = 10,
) -> AgentSession:
    """Drive the author↔Flint loop until it converges or runs out of turns (§13.1).

    On each turn the author proposes a source; the loop validates it, backtests a
    valid strategy through the user-source path, and diagnoses the result. It stops
    when a run is valid, not rejected, has no leak, and ``explain_failure`` returns
    no reasons (``converged=True``), when the author returns ``None`` (gave up), or
    at ``max_iterations``. Every structured response is recorded for inspection.
    """
    kwargs = dict(backtest_kwargs or {})
    session = AgentSession()
    feedback: Feedback | None = None

    for index in range(max_iterations):
        source = author(feedback)
        if source is None:
            break
        step = AgentIteration(index=index, source=source)
        session.iterations.append(step)

        validation = tools.validate_strategy(source)
        step.validation = validation
        if not validation["valid"]:
            step.status = "invalid"
            feedback = Feedback("validation", validation, index)
            continue

        run = tools.run_backtest(code=source, **kwargs)
        step.run = run
        step.verdict = run.get("verdict")
        if step.verdict != "ok":
            # A funding-gap rejection (or an invalid caught defensively) — data the
            # author revises against, never an error (§19.1).
            step.status = "rejected"
            feedback = Feedback("rejected", run, index)
            continue

        run_id = run["run_id"]
        results = tools.get_results(run_id)
        diagnosis = tools.explain_failure(run_id)
        step.results = results
        step.diagnosis = diagnosis

        if not step.failure_reasons and not validation.get("leak_detected"):
            step.status = "clean"
            session.converged = True
            break

        step.status = "needs_revision"
        feedback = Feedback(
            "results",
            {"results": results, "diagnosis": diagnosis, "validation": validation},
            index,
        )

    return session
