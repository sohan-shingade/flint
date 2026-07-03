"""The built-in template registry — the single source of truth (§8.4).

One registry that backtest, paper, and the MCP agent surface all read, so a template
is defined once and appears everywhere. A :class:`TemplateSpec` pairs the strategy
class with the metadata a surface needs to list and launch it (summary, category, and
whether it is an ML template that a runner must wrap in ``MLEngineStrategy`` + a scoped
``ModelStore`` rather than a plain ``EngineStrategy``).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..base import Strategy


class TemplateNotFound(KeyError):
    """No built-in template registered under this name (§8.4)."""


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    """One registered template: its class plus the metadata surfaces read (§8.4).

    ``is_ml`` tells a runner to use the ML lifecycle (train schedule + model store +
    seed) — set for templates whose class is an :class:`~flint.strategy.ml.MLStrategy`.
    ``param_spec`` (defaults for the optimizer/UI) is read off the class on demand.
    """

    name: str
    strategy_cls: type[Strategy]
    summary: str
    category: str  # "funding" | "basis" | "flow" | "technical" | "ml"
    is_ml: bool = False

    def param_spec(self) -> dict:
        """The template's declared knobs (name → default)."""
        return self.strategy_cls.param_spec()


_REGISTRY: dict[str, TemplateSpec] = {}


def register(spec: TemplateSpec) -> TemplateSpec:
    """Register ``spec``; raise on a duplicate name (a registry is not a merge)."""
    if spec.name in _REGISTRY:
        raise ValueError(f"template {spec.name!r} is already registered")
    _REGISTRY[spec.name] = spec
    return spec


def get_template(name: str) -> TemplateSpec:
    """Return the spec registered under ``name`` or raise :class:`TemplateNotFound`."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise TemplateNotFound(name) from None


def list_templates() -> list[TemplateSpec]:
    """Every registered template, sorted by name (stable for UIs/CLIs)."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def template_names() -> list[str]:
    """The registered template names, sorted."""
    return sorted(_REGISTRY)
