# Tutorial 6 — Custom Data Provider

End state: you can add a new data source (candles, funding, or something else) to Flint, have it enable via `flint.yaml`, and be queryable by strategies.

Prereq: Tutorial 2 finished.

Time: ~20 minutes.

## When to do this

Add a custom provider when:

- A venue or data source isn't covered by the 26 built-in providers
- You have a private data feed (internal risk data, proprietary signals)
- You want to aggregate / transform existing providers before they hit the store

Don't add one for:

- Another CEX — `CCXTProvider` already covers 100+
- A different Solana token — `BirdeyeProvider` + `CoinGeckoProvider` cover most
- A different Hyperliquid market — `HyperliquidCandleProvider` already covers the listed perps

## The contract

A candle provider subclasses `DataProvider` and implements at minimum `fetch_candles` + `is_available`. See `flint/providers/registry.py` for the base class.

```python
class DataProvider:
    name: str                            # unique key, e.g. "my_provider"
    data_types: list[str]                # ["candles"], ["funding"], or mix
    requires_api_key: bool = False

    def fetch_candles(self, market, resolution_s, start_ts, end_ts, on_progress=None) -> list[Candle]: ...
    def is_available(self) -> bool: ...
    def close(self) -> None: ...
```

## Step 1 — Write the provider

```python
# flint/providers/my_provider.py
from __future__ import annotations
import time
from typing import List, Optional, Callable

import httpx

from flint.models import Candle
from flint.providers.registry import DataProvider


class MyProvider(DataProvider):
    name = "my_provider"
    data_types = ["candles"]
    requires_api_key = False

    def __init__(self, base_url: str = "https://api.example.com"):
        self._client = httpx.Client(base_url=base_url, timeout=30.0)

    def is_available(self) -> bool:
        try:
            r = self._client.get("/health", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    def fetch_candles(
        self,
        market: str,
        resolution_s: int,
        start_ts: int,
        end_ts: int,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[Candle]:
        """Fetch candles from example.com.

        Returns frozen Candle dataclasses. Empty list on any error (callers handle).
        """
        try:
            # Example API pages by day — chunk your fetch so big ranges don't 429.
            symbol = market.replace("-PERP", "_PERP")
            params = {"symbol": symbol, "interval_s": resolution_s,
                      "from": start_ts, "to": end_ts, "limit": 1000}
            r = self._client.get("/candles", params=params)
            r.raise_for_status()
            rows = r.json().get("data", [])
        except Exception:
            return []

        return [
            Candle(
                ts=int(row["ts"]),
                open=float(row["o"]), high=float(row["h"]),
                low=float(row["l"]), close=float(row["c"]),
                volume=float(row["v"]),
                market=market,
                resolution_s=resolution_s,
                venue=self.name,
            )
            for row in rows
            if start_ts <= row["ts"] <= end_ts
        ]

    def close(self) -> None:
        self._client.close()
```

Notes:

- Return **empty list** on failure, not raise. Callers loop across providers; an exception stops the chain.
- Use `venue=self.name` on every candle. That's the key by which data is persisted + queried.
- Fetch in **chunks** for big ranges. Most public APIs cap at 1000 rows; pagination lives here.
- Close HTTP clients in `close()`. Consumers call it explicitly.

## Step 2 — Register in `providers/__init__.py`

```python
# flint/providers/__init__.py
from .my_provider import MyProvider

_PROVIDER_CLASSES = {
    # ... existing providers ...
    "my_provider": MyProvider,
}
```

Now `list_providers()` includes it and the CLI / API can see it.

## Step 3 — Wire into the registry

Edit `flint/cli.py` → `_build_registry`:

```python
def _build_registry(providers_cfg: dict):
    ...
    from flint.providers.my_provider import MyProvider
    registry.register(MyProvider())
    ...
```

Or for dependency injection, add it in the server's lifespan in `flint/api/main.py`.

## Step 4 — Enable in `flint.yaml`

```yaml
providers:
  my_provider:
    enabled: true
    base_url: https://api.example.com
```

Or via CLI:

```bash
flint data provider enable my_provider
```

If the provider needs an API key:

```bash
flint data provider enable my_provider --api-key $MYKEY
# appends FLINT_MY_PROVIDER_API_KEY=... to .env
```

## Step 5 — Verify

```bash
flint data provider status
#  Provider       Enabled  Available  API Key  Data Types
#  my_provider    yes      yes        —        candles
```

API: `GET /api/v1/data/providers` → your provider appears in the list.

## Step 6 — Use it

The provider is now part of the download chain. When you:

```bash
curl -X POST localhost:8000/api/v1/data/download \
  -d '{"market":"MYTOK-PERP","start_ts":...,"end_ts":...}'
```

…the registry tries all enabled providers, stores the first successful result in the `candles` table keyed by `venue="my_provider"`.

Strategies that want to read from a specific venue:

```python
# In strategy code:
candles = store.query_candles("MYTOK-PERP", 3600, venue="my_provider")
# Or via ctx:
candles = ctx.get_candles("MYTOK-PERP", lookback=50)  # if multi-market ctx is enabled
```

## Funding, OI, or other data types

Same shape, different base class / methods:

**Funding provider:**

```python
class MyFundingProvider(DataProvider):
    name = "my_venue"
    data_types = ["funding"]

    def fetch_funding(self, market, start_ts, end_ts) -> List[FundingRate]: ...
```

Output goes to `venue_funding_rates` keyed by `(venue=self.name, market, ts)`. The built-in funding providers (`flint/providers/funding_rates.py`) are the template.

**Open interest:**

```python
class MyOIProvider(DataProvider):
    name = "my_venue"
    data_types = ["open_interest"]

    def fetch_open_interest(self, market) -> List[OpenInterest]: ...
```

## Testing

Add a test under `tests/providers/`:

```python
# tests/providers/test_my_provider.py
from unittest.mock import patch, MagicMock
from flint.providers.my_provider import MyProvider


def test_fetch_candles_happy_path():
    with patch("flint.providers.my_provider.httpx.Client") as m:
        m.return_value.get.return_value.json.return_value = {
            "data": [{"ts": 1700000000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}]
        }
        m.return_value.get.return_value.raise_for_status = MagicMock()
        p = MyProvider()
        candles = p.fetch_candles("MYTOK-PERP", 3600, 1700000000, 1700010000)
        assert len(candles) == 1
        assert candles[0].venue == "my_provider"
```

All tests in Flint are mocked — no network hits in CI. Follow that pattern.

## Gotchas

- **Timestamp units.** Flint uses **unix seconds**. If the API returns milliseconds, divide by 1000.
- **OHLCV ordering.** `open, high, low, close, volume`. Don't mix up high/low on reversed-ordered APIs.
- **Deduplication.** The store upserts by `(venue, market, resolution_s, ts)`. Duplicate rows from the same provider are silently replaced — that's fine.
- **Rate limits.** Add `time.sleep()` between pages if the API rate-limits. Better: use `httpx`'s retry + backoff.
- **Thread safety.** Don't share state between `fetch_*` calls — the registry may parallelize downloads.

## What's next

- [reference/data-providers.md](../reference/data-providers.md) — full catalog of built-in providers
- [how-to/download-data.md](../how-to/download-data.md) — bulk-download recipe
- [concepts/architecture.md](../concepts/architecture.md) — where providers fit
