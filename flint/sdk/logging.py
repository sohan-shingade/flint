"""Structured logging for the SDK/CLI — JSON by default, human on ``--verbose`` (§19.2).

The local app emits **structured JSON logs** carrying ``run_id``, ``tenant``, and the
``component`` that logged — so "what happened in this run" is greppable and machine-
readable — with a human-readable mode behind ``--verbose`` for interactive use. There is
no telemetry: these logs are written to the process's stream and nowhere else (§19.7).
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_ROOT = "flint"
# Context fields we lift out of a record's ``extra`` onto the top level of the line.
_CONTEXT = ("component", "run_id", "tenant")


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line: ``ts``, ``level``, the context fields, ``msg``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts_ms": int(record.created * 1000),
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
        }
        for key in _CONTEXT:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, default=str)


class HumanLogFormatter(logging.Formatter):
    """A readable line for interactive ``--verbose`` use, context in brackets."""

    def format(self, record: logging.LogRecord) -> str:
        ctx = " ".join(
            f"{k}={getattr(record, k)}"
            for k in _CONTEXT
            if getattr(record, k, None) is not None
        )
        head = f"{record.levelname.lower():>7}"
        prefix = f"{head} [{ctx}] " if ctx else f"{head} "
        line = prefix + record.getMessage()
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(verbose: bool = False, *, stream: Any = None) -> None:
    """Install the app log handler (idempotent): JSON by default, human if ``verbose``.

    ``verbose`` also lowers the level to ``DEBUG``; the default level is ``INFO``.
    ``stream`` defaults to stderr so structured logs never contaminate a command's
    stdout payload (a bundle JSON, a tearsheet piped elsewhere).
    """
    logger = logging.getLogger(_ROOT)
    logger.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(HumanLogFormatter() if verbose else JsonLogFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False


def get_logger(
    component: str,
    *,
    run_id: str | None = None,
    tenant: str | None = None,
) -> logging.LoggerAdapter:
    """A logger that stamps every line with ``component``/``run_id``/``tenant`` (§19.2).

    Pass further per-call structured data via ``logger.info(msg, extra={"fields": {...}})``.
    """
    base = logging.getLogger(f"{_ROOT}.{component}")
    context = {"component": component, "run_id": run_id, "tenant": tenant}
    return logging.LoggerAdapter(base, context)
