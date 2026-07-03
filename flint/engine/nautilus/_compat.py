"""The churn firewall — every ``nautilus_trader`` import Flint uses, in one place (§A3).

Nautilus is pre-1.x and its data types (mark/funding especially) move between
minors. To bound that blast radius, **no module under ``flint/engine/nautilus/``
imports ``nautilus_trader`` directly** — they import the names they need from
here. When a pin bump renames or relocates a symbol, this file is the only place
that changes.

Importing this module asserts the exact pinned version at process start with an
actionable message, so a version drift fails loudly here rather than as a cryptic
``AttributeError`` deep inside the engine. The pin is also stamped into every run
manifest (§19.6) by the engine so a stored run records which Nautilus produced it.
"""

from __future__ import annotations

NAUTILUS_REQUIRED = "1.230.0"

try:
    import nautilus_trader as _nt
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "the 'nautilus' engine requires the optional nautilus_trader dependency, "
        "which is not installed. Install it with:\n\n"
        '    pip install -e ".[nautilus]"\n\n'
        "Candle-only workflows never need it — it is loaded lazily only when the "
        "'nautilus' engine is selected (flint/engine/select.py)."
    ) from exc

_INSTALLED = getattr(_nt, "__version__", "unknown")
if _INSTALLED != NAUTILUS_REQUIRED:
    raise ImportError(
        f"nautilus_trader {NAUTILUS_REQUIRED} is required but {_INSTALLED!r} is "
        "installed. Flint pins Nautilus exactly because its mark/funding data "
        "types churn between minors (the churn firewall lives in "
        "flint/engine/nautilus/_compat.py). Reinstall the pinned version:\n\n"
        f'    pip install -e ".[nautilus]"   # resolves nautilus_trader=={NAUTILUS_REQUIRED}\n'
    )

# --- backtest kernel ---------------------------------------------------------
from nautilus_trader.backtest.engine import (  # noqa: E402
    BacktestEngine,
    BacktestEngineConfig,
)
from nautilus_trader.backtest.models import FeeModel, FillModel  # noqa: E402
from nautilus_trader.config import (  # noqa: E402
    DataEngineConfig,
    LoggingConfig,
)

# --- common / trading base classes -------------------------------------------
from nautilus_trader.common.actor import Actor  # noqa: E402
from nautilus_trader.trading.strategy import Strategy  # noqa: E402

# --- core data base (for the custom FlintFundingRate) ------------------------
from nautilus_trader.core.data import Data  # noqa: E402

# --- model: identifiers ------------------------------------------------------
from nautilus_trader.model.identifiers import (  # noqa: E402
    ClientId,
    ClientOrderId,
    InstrumentId,
    Symbol,
    TradeId,
    Venue,
)

# --- model: data types -------------------------------------------------------
from nautilus_trader.model.data import (  # noqa: E402
    Bar,
    BarSpecification,
    BarType,
    BookOrder,
    DataType,
    IndexPriceUpdate,
    MarkPriceUpdate,
    TradeTick,
)

# --- model: order book (synthetic fill-simulation book) ----------------------
from nautilus_trader.model.book import OrderBook  # noqa: E402

# --- model: events (order lifecycle the recorder translates) -----------------
from nautilus_trader.model.events import OrderFilled  # noqa: E402

# --- model: enums ------------------------------------------------------------
from nautilus_trader.model.enums import (  # noqa: E402
    AccountType,
    AggregationSource,
    AggressorSide,
    BarAggregation,
    BookType,
    LiquiditySide,
    OmsType,
    OrderSide,
    PriceType,
    TimeInForce,
)

# --- model: instruments ------------------------------------------------------
from nautilus_trader.model.instruments import CryptoPerpetual  # noqa: E402

# --- model: value objects ----------------------------------------------------
from nautilus_trader.model.currencies import USDC  # noqa: E402
from nautilus_trader.model.objects import (  # noqa: E402
    Currency,
    Money,
    Price,
    Quantity,
)

__all__ = [
    "NAUTILUS_REQUIRED",
    "BacktestEngine",
    "BacktestEngineConfig",
    "FeeModel",
    "FillModel",
    "DataEngineConfig",
    "LoggingConfig",
    "Actor",
    "Strategy",
    "Data",
    "ClientId",
    "ClientOrderId",
    "InstrumentId",
    "Symbol",
    "TradeId",
    "Venue",
    "Bar",
    "BarSpecification",
    "BarType",
    "BookOrder",
    "DataType",
    "IndexPriceUpdate",
    "MarkPriceUpdate",
    "TradeTick",
    "OrderBook",
    "OrderFilled",
    "AccountType",
    "AggregationSource",
    "AggressorSide",
    "BarAggregation",
    "BookType",
    "LiquiditySide",
    "OmsType",
    "OrderSide",
    "PriceType",
    "TimeInForce",
    "CryptoPerpetual",
    "USDC",
    "Currency",
    "Money",
    "Price",
    "Quantity",
]
