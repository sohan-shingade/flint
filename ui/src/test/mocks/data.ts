/**
 * Simulated data factories for Flint UI tests.
 * Each factory produces deterministic data matching the exact API response shapes.
 */

const BASE_TS = 1704067200 // 2024-01-01 00:00:00 UTC

export function makeCandles(n = 100, startTs = BASE_TS, resolution = 3600) {
  const candles = []
  let price = 100.0
  for (let i = 0; i < n; i++) {
    const open = price
    const change = (Math.sin(i * 0.1) * 2) + (i % 7 === 0 ? 3 : -1)
    const close = open + change
    const high = Math.max(open, close) + Math.abs(change) * 0.5
    const low = Math.min(open, close) - Math.abs(change) * 0.3
    candles.push({
      ts: startTs + i * resolution,
      open: +open.toFixed(2),
      high: +high.toFixed(2),
      low: +low.toFixed(2),
      close: +close.toFixed(2),
      volume: 1000 + (i * 17 % 500),
      venue: 'pyth',
    })
    price = close
  }
  return candles
}

export function makeTrades(n = 10, startTs = BASE_TS) {
  const trades = []
  for (let i = 0; i < n; i++) {
    const side = i % 2 === 0 ? 'long' : 'short'
    const entryPrice = 100 + i * 2
    const exitPrice = side === 'long' ? entryPrice + 5 - i * 0.3 : entryPrice - 5 + i * 0.3
    const size = 0.5 + (i * 0.1)
    const pnl = side === 'long'
      ? (exitPrice - entryPrice) * size
      : (entryPrice - exitPrice) * size
    trades.push({
      entry_ts: startTs + i * 86400,
      exit_ts: startTs + i * 86400 + 43200,
      side,
      entry_price: +entryPrice.toFixed(2),
      exit_price: +exitPrice.toFixed(2),
      size: +size.toFixed(4),
      pnl: +pnl.toFixed(2),
      fee: +(size * entryPrice * 0.001).toFixed(4),
      market: 'SOL-PERP',
      venue: 'drift',
    })
  }
  return trades
}

export function makeEquityCurve(n = 100, startTs = BASE_TS, initialCapital = 10000) {
  const curve: [number, number][] = []
  let equity = initialCapital
  for (let i = 0; i < n; i++) {
    equity += (Math.sin(i * 0.15) * 50) + 5
    curve.push([startTs + i * 3600, +equity.toFixed(2)])
  }
  return curve
}

export function makeDrawdownCurve(n = 100, startTs = BASE_TS) {
  const curve: [number, number][] = []
  for (let i = 0; i < n; i++) {
    const dd = -Math.abs(Math.sin(i * 0.1) * 0.05)
    curve.push([startTs + i * 3600, +dd.toFixed(4)])
  }
  return curve
}

export function makeBacktestResult() {
  const trades = makeTrades(10)
  const equityCurve = makeEquityCurve(100)
  const drawdownCurve = makeDrawdownCurve(100)
  const totalPnl = trades.reduce((s, t) => s + t.pnl, 0)
  const wins = trades.filter(t => t.pnl > 0)

  return {
    strategy_name: 'custom',
    market: 'SOL-PERP',
    resolution_s: 3600,
    period_start: BASE_TS,
    period_end: BASE_TS + 100 * 3600,
    initial_capital: 10000,
    metrics: {
      total_pnl: +totalPnl.toFixed(2),
      total_return: +(totalPnl / 10000).toFixed(4),
      sharpe_ratio: 1.45,
      sortino_ratio: 2.1,
      calmar_ratio: 3.2,
      max_drawdown: 0.078,
      win_rate: wins.length / trades.length,
      profit_factor: 2.3,
      avg_win: +(wins.reduce((s, t) => s + t.pnl, 0) / Math.max(wins.length, 1)).toFixed(2),
      avg_loss: -15.5,
      total_trades: trades.length,
      total_fees: 5.2,
      avg_trade_duration_h: 12,
      max_consecutive_wins: 4,
      max_consecutive_losses: 2,
      expectancy: 12.5,
    },
    trades,
    equity_curve: equityCurve,
    drawdown_curve: drawdownCurve,
    buy_hold_equity: equityCurve.map(([ts, eq]) => [ts, eq * 0.95] as [number, number]),
    monte_carlo: {
      ci_5: -200,
      median: 450,
      ci_95: 1200,
      mean: 480,
      std: 350,
      prob_profit: 0.72,
    },
    monthly_returns: {
      '2024': { 1: 0.05, 2: -0.02, 3: 0.08 },
    },
    data_quality: {
      total_candles: 100,
      gap_count: 0,
      gap_pct: 0,
      warnings: [],
    },
    instrument_exposure: {},
  }
}

export function makeMarketInfo(markets?: string[]) {
  const defaultMarkets = ['SOL-PERP', 'BTC-PERP', 'ETH-PERP', 'WIF-PERP', 'JUP-PERP']
  return (markets || defaultMarkets).map(m => ({
    market: m,
    resolution_s: 3600,
    candle_count: 2000,
    first_ts: BASE_TS,
    last_ts: BASE_TS + 2000 * 3600,
  }))
}

export function makeAvailableMarkets() {
  return [
    { market: 'SOL-PERP', source: 'drift', type: 'perp' },
    { market: 'BTC-PERP', source: 'drift', type: 'perp' },
    { market: 'ETH-PERP', source: 'drift', type: 'perp' },
    { market: 'WIF-PERP', source: 'drift', type: 'perp' },
    { market: 'JUP-PERP', source: 'drift', type: 'perp' },
    { market: 'SOL', source: 'drift', type: 'spot' },
  ]
}

export function makeDataCheck(coversRange = true) {
  return {
    has_data: true,
    covers_range: coversRange,
    will_download: !coversRange,
    candle_count: coversRange ? 2000 : 500,
    total_in_db: 2000,
    first_ts: BASE_TS,
    last_ts: BASE_TS + 2000 * 3600,
  }
}

export function makeMultiMarketCheck(ready = true) {
  return {
    ready,
    markets: {
      'SOL-PERP': { has_data: true, covers_range: ready, candle_count: 2000 },
      'BTC-PERP': { has_data: true, covers_range: ready, candle_count: 1800 },
    },
    missing: ready ? [] : ['BTC-PERP'],
  }
}

export function makeStrategyCatalog() {
  return [
    { name: 'ma_crossover', display_name: 'MA Crossover', description: 'SMA crossover trend following', type: 'single_venue', venues: [], params: { fast_period: 10, slow_period: 30 } },
    { name: 'rsi', display_name: 'RSI Mean Reversion', description: 'RSI-based mean reversion', type: 'single_venue', venues: [], params: { period: 14, oversold: 30, overbought: 70 } },
    { name: 'bollinger', display_name: 'Bollinger Bands', description: 'Bollinger band bounces', type: 'single_venue', venues: [], params: { period: 20, num_std: 2.0 } },
    { name: 'funding_harvest', display_name: 'Funding Harvest', description: 'Capture funding rate payments', type: 'single_venue', venues: [], needs_funding: true, params: { entry_threshold: 0.001 } },
    { name: 'multi_venue_funding', display_name: 'Multi-Venue Funding', description: 'Cross-venue funding arb', type: 'multi_venue', venues: ['drift', 'hyperliquid'], params: {} },
  ]
}

export function makeUserStrategies() {
  return [
    { name: 'my_strategy', file: 'my_strategy.py' },
    { name: 'test_rsi', file: 'test_rsi.py' },
  ]
}

export function makePaperSession(overrides: Record<string, any> = {}) {
  return {
    session_id: 'sess-001',
    strategy_name: 'MA Crossover',
    market: 'SOL-PERP',
    venue: 'drift',
    equity: 10500.0,
    pnl: 500.0,
    status: 'live',
    initial_capital: 10000,
    ...overrides,
  }
}

export function makePaperPortfolio(sessions?: any[]) {
  const strats = sessions || [
    makePaperSession(),
    makePaperSession({ session_id: 'sess-002', strategy_name: 'RSI', pnl: -200, equity: 9800, status: 'stopped' }),
  ]
  return {
    total_equity: strats.reduce((s, st) => s + st.equity, 0),
    total_pnl: strats.reduce((s, st) => s + st.pnl, 0),
    total_initial_capital: strats.reduce((s, st) => s + (st.initial_capital || 10000), 0),
    active_sessions: strats.filter(s => s.status === 'live').length,
    total_sessions: strats.length,
    per_strategy: strats,
  }
}

export function makeSessionStatus(sessionId = 'sess-001') {
  return {
    session_id: sessionId,
    phase: 'live',
    status: 'live',
    strategy: 'MA Crossover',
    market: 'SOL-PERP',
    equity: 10500.0,
    cash: 9000.0,
    unrealized_pnl: 300.0,
    realized_pnl: 200.0,
    pnl: 500.0,
    positions: [
      { market: 'SOL-PERP', side: 'long', entry_price: 100.0, size: 1.5, unrealized_pnl: 300.0 },
    ],
    pending_orders: [],
    total_trades: 8,
    total_fees: 4.5,
    initial_capital: 10000,
    risk_status: { ok: true },
    equity_curve: makeEquityCurve(50).map(([ts, eq]) => ({ ts, equity: eq })),
    metrics: {},
    started_at: BASE_TS,
    leverage: 1.5,
    free_margin: 8000,
    margin_used: 2000,
  }
}

export function makeSessionTrades() {
  return makeTrades(5).map(t => ({
    ...t,
    quantity: t.size,
  }))
}

export function makeJournalRuns() {
  return [
    {
      run_id: 'run-001',
      strategy_name: 'MA Crossover',
      market: 'SOL-PERP',
      resolution_s: 3600,
      start_ts: BASE_TS,
      end_ts: BASE_TS + 90 * 86400,
      initial_capital: 10000,
      created_at: BASE_TS + 100 * 86400,
      total_pnl: 1250.0,
      win_rate: 0.6,
      sharpe_ratio: 1.8,
      max_drawdown: 0.05,
      total_trades: 45,
      total_fees: 22.5,
    },
    {
      run_id: 'run-002',
      strategy_name: 'RSI',
      market: 'ETH-PERP',
      resolution_s: 3600,
      start_ts: BASE_TS,
      end_ts: BASE_TS + 60 * 86400,
      initial_capital: 10000,
      created_at: BASE_TS + 95 * 86400,
      total_pnl: -320.0,
      win_rate: 0.4,
      sharpe_ratio: -0.3,
      max_drawdown: 0.12,
      total_trades: 30,
      total_fees: 15.0,
    },
  ]
}

export function makeRegimeResult() {
  return {
    mode: 'regime_test',
    per_regime: {
      'etf_bull_run': {
        strategy_name: 'custom', market: 'SOL-PERP', resolution_s: 3600,
        period_start: '2024-01-10', period_end: '2024-05-31',
        initial_capital: 10000,
        metrics: { total_pnl: 500, sharpe_ratio: 1.2, max_drawdown: 0.08, total_trades: 15, winning_trades: 9, losing_trades: 6, win_rate: 0.6 },
        equity_curve: Array.from({ length: 50 }, (_, i) => [1704844800 + i * 3600, 10000 + i * 10]),
      },
      'summer_correction': {
        strategy_name: 'custom', market: 'SOL-PERP', resolution_s: 3600,
        period_start: '2024-06-01', period_end: '2024-08-31',
        initial_capital: 10000,
        metrics: { total_pnl: -200, sharpe_ratio: -0.5, max_drawdown: 0.15, total_trades: 8, winning_trades: 3, losing_trades: 5, win_rate: 0.375 },
        equity_curve: Array.from({ length: 50 }, (_, i) => [1717200000 + i * 3600, 10000 - i * 4]),
      },
    },
    aggregate: {
      avg_sharpe: 0.35,
      worst_drawdown: 0.15,
      best_regime: 'etf_bull_run',
      worst_regime: 'summer_correction',
      regime_summary: [
        { id: 'etf_bull_run', label: 'ETF Bull Run', regime_type: 'bull', sharpe: 1.2, max_drawdown: 0.08, total_pnl: 500, total_trades: 15, has_error: false, win_rate: 0.6 },
        { id: 'summer_correction', label: 'Summer Correction', regime_type: 'bear', sharpe: -0.5, max_drawdown: 0.15, total_pnl: -200, total_trades: 8, has_error: false, win_rate: 0.375 },
      ],
    },
  }
}

export function makeOptResult() {
  return {
    best_params: { fast_period: 8, slow_period: 25 },
    best_value: 2.1,
    metric: 'sharpe_ratio',
    n_trials: 50,
    trials: Array.from({ length: 5 }, (_, i) => ({
      params: { fast_period: 8 + i, slow_period: 25 + i },
      metric_value: 2.1 - i * 0.2,
      total_pnl: 800 - i * 100,
      sharpe_ratio: 2.1 - i * 0.2,
      max_drawdown: 0.05 + i * 0.01,
      win_rate: 0.6 - i * 0.02,
      total_trades: 40 + i * 2,
    })),
    top_trials: [],
    convergence: Array.from({ length: 10 }, (_, i) => [i, 1.5 + i * 0.06] as [number, number | null]),
    param_importance: { fast_period: 0.65, slow_period: 0.35 },
    best_backtest_metrics: { total_pnl: 800, sharpe_ratio: 2.1, max_drawdown: 0.05 },
    strategy_name: 'custom',
    market: 'SOL-PERP',
    candles: 2000,
  }
}

export function makeFundingByVenue() {
  const rates = Array.from({ length: 24 }, (_, i) => ({
    ts: BASE_TS + i * 3600,
    rate: 0.0001 * (1 + Math.sin(i * 0.3)),
  }))
  return {
    venues: {
      drift: rates,
      binance: rates.map(r => ({ ...r, rate: r.rate * 1.2 })),
    },
  }
}

export function makeVenueVolume() {
  return {
    venues: {
      drift: Array.from({ length: 24 }, (_, i) => ({
        ts: BASE_TS + i * 3600,
        volume: 50000 + i * 1000,
      })),
    },
  }
}

export function makeLiveSessions() {
  return [
    {
      session_id: 'live-001',
      strategy: 'MA Crossover',
      market: 'SOL-PERP',
      status: 'running',
      equity: 10200,
      pnl: 200,
    },
  ]
}

export function makeFills() {
  return Array.from({ length: 5 }, (_, i) => ({
    fill_id: `fill-${i}`,
    ts: BASE_TS + i * 7200,
    market: 'SOL-PERP',
    side: i % 2 === 0 ? 'buy' : 'sell',
    size: 0.5 + i * 0.1,
    price: 100 + i * 2,
    fee: 0.05,
    impact_bps: 2 + i,
    latency_ms: 50 + i * 10,
    tx_cost: 0.001,
    venue: 'drift',
  }))
}
