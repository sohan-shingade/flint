"""api — FastAPI REST + WebSocket surface; delegates to services/ only (§12, §17)."""

from __future__ import annotations

from .app import Deps, create_app

__all__ = ["create_app", "Deps"]
