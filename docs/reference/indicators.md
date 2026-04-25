# Indicators Reference

20 technical indicators. All live in `flint.indicators` and accept a `history: List[Candle]` plus a period. Most return a `float`; a few return tuples.

```python
from flint.indicators import sma, ema, rsi, macd, bollinger, atr
```

All indicators ignore the last candle passed in (i.e. `history` in `on_candle` does not include the current bar). If `len(history) < period`, behavior is indicator-specific — most return `float('nan')` or the last available value; guard with `if len(history) < period: return Signal.HOLD`.

## Helpers (array extractors)

| Function | Signature | Returns |
|---|---|---|
| `closes` | `(history, period=None)` | `np.ndarray` of close prices |
| `highs` | `(history, period=None)` | `np.ndarray` of highs |
| `lows` | `(history, period=None)` | `np.ndarray` of lows |
| `volumes` | `(history, period=None)` | `np.ndarray` of volumes |

## Moving averages

| Function | Signature | Formula |
|---|---|---|
| `sma` | `sma(history, period) -> float` | `mean(closes[-period:])` |
| `ema` | `ema(history, period) -> float` | `α·close + (1−α)·ema_prev`, `α = 2/(period+1)` |
| `wma` | `wma(history, period) -> float` | `sum(i·close[i]) / sum(i)` over last `period` |

## Oscillators

| Function | Signature | Returns |
|---|---|---|
| `rsi` | `rsi(history, period=14) -> float` | 0–100; `>70` overbought, `<30` oversold |
| `stochastic` | `stochastic(history, period=14) -> (k, d)` | `%K, %D` — both 0–100 |
| `macd` | `macd(history, fast=12, slow=26, signal=9) -> (macd_line, signal_line, histogram)` | Signed floats |

## Bands

| Function | Signature | Returns |
|---|---|---|
| `bollinger` | `bollinger(history, period=20, num_std=2.0) -> (upper, middle, lower)` | Price levels |
| `bollinger_width` | `bollinger_width(history, period=20, num_std=2.0) -> float` | `(upper - lower) / middle` |

## Volatility

| Function | Signature | Returns |
|---|---|---|
| `atr` | `atr(history, period=14) -> float` | Average true range (price units) |
| `volatility` | `volatility(history, period=20) -> float` | Stddev of log returns |

## Volume

| Function | Signature | Returns |
|---|---|---|
| `vwap` | `vwap(history, period=20) -> float` | Volume-weighted avg price over last `period` bars |
| `volume_ratio` | `volume_ratio(history, period=20) -> float` | `current_volume / sma(volumes, period)` |

## Momentum / trend

| Function | Signature | Returns |
|---|---|---|
| `roc` | `roc(history, period=12) -> float` | `(close − close_period_ago) / close_period_ago` (decimal) |
| `adx` | `adx(history, period=14) -> float` | 0–100; `>25` indicates trend strength |

## Statistical

| Function | Signature | Returns |
|---|---|---|
| `z_score` | `z_score(history, period=20) -> float` | `(close − mean) / stddev` |

## Structural

| Function | Signature | Returns |
|---|---|---|
| `highest_high` | `highest_high(history, period) -> float` | `max(highs[-period:])` |
| `lowest_low` | `lowest_low(history, period) -> float` | `min(lows[-period:])` |

---

## Custom indicators

Indicators are plain functions over `List[Candle]` — add your own in your strategy file:

```python
from flint.indicators import closes

def supertrend(history, period=10, multiplier=3.0):
    # your logic over closes(history), highs(history), lows(history)
    ...
```

No registration needed.

## See also

- [Python SDK reference](python-sdk.md) — `Candle` model and strategy surface
- [Strategy templates](strategy-templates.md) — which indicators each built-in uses
