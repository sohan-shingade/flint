"""data.livefeed — live market-data feed for paper/live; lake used for reconnect gap-replay only (§6.7).

Subscribes a venue WebSocket through the **shared** ``data.normalize`` parsers
(one parser per HL message, never two), aggregates closed bars on the venue-event
clock, and replays reconnect gaps from the lake through the same engine loop. The
feed emits bars only; the paper/live runner (slice 5.6) owns the engine wiring.
"""

from __future__ import annotations

from .aggregator import CandleAggregator, LiveBar
from .clock import PaperClock
from .feed import EngineInputs, LiveFeed, assemble_engine_inputs
from .gap import GapData, GapRecovery, GapSource, InMemoryGapSource

__all__ = [
    "CandleAggregator",
    "LiveBar",
    "PaperClock",
    "EngineInputs",
    "LiveFeed",
    "assemble_engine_inputs",
    "GapData",
    "GapRecovery",
    "GapSource",
    "InMemoryGapSource",
]
