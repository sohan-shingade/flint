import { BLANK_TEMPLATE } from './blank'
import { MA_CROSSOVER_TEMPLATE } from './ma-crossover'
import { RSI_TEMPLATE } from './rsi'
import { MEAN_REVERSION_TEMPLATE } from './mean-reversion'
import { BREAKOUT_TEMPLATE } from './breakout'
import { FUNDING_HARVEST_TEMPLATE } from './funding-harvest'
import { GRID_TEMPLATE } from './grid'
import { DUAL_TF_TEMPLATE } from './dual-tf'
import { CROSS_MARKET_TEMPLATE } from './cross-market'
import { STOP_LOSS_TEMPLATE } from './stop-loss'
import { VOLATILITY_TEMPLATE } from './volatility'
import { MULTI_INDICATOR_TEMPLATE } from './multi-indicator'
import { SCALPER_TEMPLATE } from './scalper'
import { VWAP_REVERSION_TEMPLATE } from './vwap-reversion'
import { MACD_TEMPLATE } from './macd'
import { ATR_BREAKOUT_TEMPLATE } from './atr-breakout'
import { RSI_MACD_TEMPLATE } from './rsi-macd'
import { MULTI_VENUE_FUNDING_TEMPLATE } from './multi-venue-funding'
import { ORDERBOOK_MOMENTUM_TEMPLATE } from './orderbook-momentum'
import { CROSS_VENUE_PAIRS_TEMPLATE } from './cross-venue-pairs'
import { LEVERAGED_GRID_TEMPLATE } from './leveraged-grid'
import { BETA_HEDGED_TEMPLATE } from './beta-hedged'
import { FUNDING_ARB_TEMPLATE } from './funding-arb'
import { MOMENTUM_BREAKOUT_TEMPLATE } from './momentum-breakout'
import { FUNDING_MEAN_REVERSION_TEMPLATE } from './funding-mean-reversion'
import { BASIS_TRADE_TEMPLATE } from './basis-trade'

export interface TemplateInfo {
  label: string
  code: string
  category: string
  hint?: string
}

export const TEMPLATES: Record<string, TemplateInfo> = {
  blank:       { label: 'Blank Strategy',       code: BLANK_TEMPLATE,           category: 'basic' },
  ma_crossover:{ label: 'MA Crossover',         code: MA_CROSSOVER_TEMPLATE,    category: 'trend',
    hint: 'Any perp market, 1h. Needs 30+ days of data.' },
  rsi:         { label: 'RSI Mean Reversion',    code: RSI_TEMPLATE,             category: 'mean-rev',
    hint: 'Any market, 1h. Needs 15+ candles.' },
  mean_rev:    { label: 'Z-Score Reversion',     code: MEAN_REVERSION_TEMPLATE,  category: 'mean-rev',
    hint: 'Any market, 1h. Best in ranging markets.' },
  breakout:    { label: 'Breakout Momentum',     code: BREAKOUT_TEMPLATE,        category: 'trend',
    hint: 'Any market, 1h-4h. Volume confirmation.' },
  vol_breakout:{ label: 'Volatility Breakout',   code: VOLATILITY_TEMPLATE,      category: 'trend',
    hint: 'Any market, 1h. Trades squeeze-to-expansion. 30+ days.' },
  multi_ind:   { label: 'Multi-Indicator',       code: MULTI_INDICATOR_TEMPLATE, category: 'mean-rev',
    hint: 'Any market, 1h. RSI + MACD + volume confluence. Fewer but higher-quality signals.' },
  scalper:     { label: 'Scalper (VWAP)',        code: SCALPER_TEMPLATE,         category: 'mean-rev',
    hint: 'Any market, best on 5m-15m. High frequency, small gains. Needs tight spreads.' },
  funding:     { label: 'Funding Harvest',       code: FUNDING_HARVEST_TEMPLATE, category: 'solana',
    hint: 'Perp markets only. Uses REAL funding rates — download funding data in Data Explorer first. 1h.' },
  grid:        { label: 'Grid Trader',           code: GRID_TEMPLATE,            category: 'solana',
    hint: 'Any perp. Best in ranging/sideways markets. 1h.' },
  dual_tf:     { label: 'Dual Timeframe',        code: DUAL_TF_TEMPLATE,         category: 'trend',
    hint: 'Any market, 1h. Trend + momentum alignment. 60+ days.' },
  vwap_rev:    { label: 'VWAP Reversion',        code: VWAP_REVERSION_TEMPLATE,  category: 'mean-rev',
    hint: 'Any market, 1h. Buy below VWAP, sell at reversion. Best in range-bound markets.' },
  macd:        { label: 'MACD Crossover',        code: MACD_TEMPLATE,            category: 'trend',
    hint: 'Any market, 1h. Classic MACD/signal line crossover.' },
  atr:         { label: 'ATR Breakout',          code: ATR_BREAKOUT_TEMPLATE,    category: 'trend',
    hint: 'Any market, 1h. Volatility-adaptive channel breakout.' },
  rsi_macd:    { label: 'RSI + MACD Combo',      code: RSI_MACD_TEMPLATE,        category: 'mean-rev',
    hint: 'Any market, 1h. Only trades when RSI AND MACD agree. High-quality signals.' },
  mv_funding:  { label: 'Multi-Venue Funding',   code: MULTI_VENUE_FUNDING_TEMPLATE, category: 'solana',
    hint: 'Perp only. Cross-venue funding arbitrage — download funding for 2+ venues in Data Explorer first.' },
  cross_mkt:   { label: 'BTC Correlation',       code: CROSS_MARKET_TEMPLATE,    category: 'advanced',
    hint: 'Run on SOL-PERP. Uses ctx.get_candles("BTC-PERP") for cross-market signals. Needs BTC data in DB.' },
  stop_loss:   { label: 'Momentum + Stops (v2)', code: STOP_LOSS_TEMPLATE,       category: 'advanced',
    hint: 'Any perp, 1h. Demonstrates v2 context API: market_order + stop_order + take_profit_order.' },
  ob_momentum: { label: 'Orderbook Momentum',   code: ORDERBOOK_MOMENTUM_TEMPLATE, category: 'advanced',
    hint: 'Uses ctx.get_orderbook() + ctx.get_impact_price() to check liquidity before trading. Needs orderbook data.' },
  xv_pairs:    { label: 'Cross-Venue Pairs',    code: CROSS_VENUE_PAIRS_TEMPLATE,  category: 'advanced',
    hint: 'SOL/BTC pairs on Drift + Hyperliquid. Multi-venue + multi-market. Enable MARGIN TRACKING. Needs both markets.' },
  lev_grid:    { label: 'Leveraged Grid',        code: LEVERAGED_GRID_TEMPLATE,     category: 'advanced',
    hint: 'DCA grid with leverage monitoring. Enable MARGIN TRACKING to see leverage limits + liquidation risk.' },
  beta_hedged: { label: 'Beta-Hedged MR',      code: BETA_HEDGED_TEMPLATE,        category: 'advanced',
    hint: 'SOL/BTC residual mean reversion. Needs BTC-PERP data. Uses rolling beta to hedge market risk.' },
  funding_arb: { label: 'Funding Arb (Cross-Venue)', code: FUNDING_ARB_TEMPLATE, category: 'advanced',
    hint: 'Drift + Hyperliquid. Delta-neutral funding arb. Needs funding data from both venues.' },
  momentum_bk: { label: 'Momentum Breakout', code: MOMENTUM_BREAKOUT_TEMPLATE, category: 'trend',
    hint: 'Any market, 1h. N-bar high/low breakout with optional Pyth oracle confirmation.' },
  funding_mr:  { label: 'Funding Mean Reversion', code: FUNDING_MEAN_REVERSION_TEMPLATE, category: 'mean-rev',
    hint: 'Any perp, 1h. Bollinger bands on funding rate. Needs funding data downloaded.' },
  basis_trade: { label: 'Basis Trade (Cross-Venue)', code: BASIS_TRADE_TEMPLATE, category: 'advanced',
    hint: 'Drift + Hyperliquid. Cross-venue price basis arbitrage. Needs data from both venues.' },
}
