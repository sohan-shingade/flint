"""flint/services — in-process service layer.

D-4.7-full (Wave 1): callable functions that wrap the core logic shared
between FastAPI routes (`flint/api/routes/*.py`) and the MCP server
(`flint/mcp_server.py`). Routes become thin adapters that translate
HTTP↔dict; MCP tools call services directly with no `flint serve`
required.

Module layout:
- `strategies` — name+params → Strategy instance (shared builder map)
- `backtest`   — synchronous one-shot backtest runner
- `data`       — OHLCV / funding / metadata queries against FlintStore
- `journal`    — past-run browsing + comparison
- `paper`      — paper-engine session lifecycle (still requires a daemon
                  process holding the asyncio loop; MCP queries state)
"""
from __future__ import annotations
