# Built-in Strategy Templates

20 strategies ship with Flint. All are importable from `flint.strategy` and selectable by name in the API/CLI/MCP. Source: `flint/strategy/*.py`.

## Catalog

| Name | Class | Category | Venues | Funding needed |
|---|---|---|---|---|
| `ma_crossover` | `MACrossoverStrategy` | trend | any | no |
| `ema_crossover` | `EMACrossoverStrategy` | trend | any | no |
| `momentum` | `MomentumStrategy` | trend | any | no |
| `breakout_momentum` | `BreakoutMomentumStrategy` | trend | any | no |
| `momentum_breakout` | `MomentumBreakoutStrategy` | trend | any | oracle pref |
| `dual_timeframe` | `DualTimeframeStrategy` | trend | any | no |
| `macd_divergence` | `MACDDivergenceStrategy` | trend | any | no |
| `atr_breakout` | `ATRBreakoutStrategy` | trend | any | no |
| `rsi` | `RSIStrategy` | mean-rev | any | no |
| `bollinger` | `BollingerStrategy` | mean-rev | any | no |
| `mean_reversion` | `MeanReversionStrategy` | mean-rev | any | no |
| `vwap_reversion` | `VWAPReversionStrategy` | mean-rev | any | no |
| `rsi_macd_combo` | `RSIMACDComboStrategy` | multi-signal | any | no |
| `funding_harvest` | `FundingHarvestStrategy` | defi | perp | yes |
| `funding_arb` | `FundingArbStrategy` | defi | cross-venue | yes |
| `funding_mean_reversion` | `FundingMeanReversionStrategy` | defi | perp | yes |
| `multi_venue_funding` | `MultiVenueFundingStrategy` | defi | cross-venue | yes |
| `basis_trade` | `BasisTradeStrategy` | defi | cross-venue | yes |
| `grid_trader` | `GridTraderStrategy` | defi | any | no |
| `mev_arb_monitor` | `MevArbMonitor` | monitor | — | no |

## Default parameters

| Name | Parameters |
|---|---|
| `ma_crossover` | `fast_period=10`, `slow_period=30` |
| `ema_crossover` | `fast_period=12`, `slow_period=26` |
| `momentum` | `lookback=24`, `threshold_pct=5.0` |
| `breakout_momentum` | — |
| `momentum_breakout` | `breakout_lookback=20`, `trailing_stop_pct=0.02`, `oracle_confirmation=1`, `candle_resolution_s=3600` |
| `dual_timeframe` | — |
| `macd_divergence` | `fast=12`, `slow=26`, `signal=9` |
| `atr_breakout` | `period=20`, `atr_period=14`, `multiplier=2.0` |
| `rsi` | `period=14`, `oversold=30`, `overbought=70` |
| `bollinger` | `period=20`, `num_std=2.0` |
| `mean_reversion` | `period=20`, `entry_z=2.0`, `exit_z=0.5`, `stop_loss_pct=0.05` |
| `vwap_reversion` | `period=20`, `entry_pct=2.0`, `exit_pct=0.5` |
| `rsi_macd_combo` | `rsi_period=14`, `macd_fast=12`, `macd_slow=26`, `macd_signal=9`, `rsi_oversold=30`, `rsi_overbought=70` |
| `funding_harvest` | `entry_threshold=0.001`, `exit_threshold=0.0002`, `stop_loss_pct=0.05`, `lookback=8` |
| `funding_arb` | `min_spread_bps=5.0`, `exit_spread_bps=1.0`, `max_hold_hours=24`, `position_size_usd=1000`, `min_spread_duration=1`, `candle_resolution_s=60` |
| `funding_mean_reversion` | `bb_lookback=24`, `bb_std=2.0`, `max_hold_hours=12`, `position_size_pct=0.5`, `candle_resolution_s=3600` |
| `multi_venue_funding` | `entry_threshold=0.0005`, `exit_threshold=0.0001`, `lookback=12` |
| `basis_trade` | `entry_basis_bps=30.0`, `exit_basis_bps=5.0`, `max_hold_hours=12`, `position_size_usd=1000`, `candle_resolution_s=3600` |
| `grid_trader` | — |
| `mev_arb_monitor` | `min_profit_bps=10.0`, `max_hops=3`, `alert_enabled=0`, `candle_resolution_s=60` |

## Notes

- **Funding strategies** require funding-rate data; download from 7 venues with `POST /data/download` or `flint data download` (funding auto-fetches for `-PERP` markets).
- **Cross-venue** strategies (`funding_arb`, `multi_venue_funding`, `basis_trade`) position on two venues simultaneously; enable `margin_tracking=true` and configure `capital_allocation` in the backtest request.
- **`mev_arb_monitor`** is observation-only — logs opportunities but does not submit orders. Useful for data collection before writing an MEV strategy.
- **`grid_trader`** places a ladder of limit orders around a mid price; best on range-bound markets.

## Per-strategy guides

In-depth writeups for select strategies:

- [`funding_arb`](../strategies/funding_arb.md) — cross-venue funding spread capture
- [`funding_mean_reversion`](../strategies/funding_mean_reversion.md) — Bollinger bands on funding
- [`momentum_breakout`](../strategies/momentum_breakout.md) — volume-confirmed breakout
- [`basis_trade`](../strategies/basis_trade.md) — spot-futures basis
- [`mev_arb_monitor`](../strategies/mev_arb_monitor.md) — MEV opportunity scanner

## Choosing a starting strategy

| Starting point | Why |
|---|---|
| `momentum` | Simple, few params, works out of the box |
| `rsi_macd_combo` | Harder to overfit — two filters must agree |
| `funding_harvest` | Makes money from funding payments alone; good on SOL/ETH perps in high-funding regimes |
| `mean_reversion` | Good baseline for range-bound regimes |

Avoid starting with `funding_arb` or `basis_trade` — they require more data, careful cost modeling, and cross-venue capital. See [tutorials/05-cross-venue-funding-arb.md](../tutorials/05-cross-venue-funding-arb.md).
