/**
 * MSW request handlers for all Flint API endpoints.
 * These intercept fetch() calls during tests and return simulated data.
 */
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import {
  makeCandles,
  makeMarketInfo,
  makeAvailableMarkets,
  makeDataCheck,
  makeMultiMarketCheck,
  makeFundingByVenue,
  makeVenueVolume,
  makeStrategyCatalog,
  makeUserStrategies,
  makeBacktestResult,
  makeOptResult,
  makePaperPortfolio,
  makeSessionStatus,
  makeSessionTrades,
  makeJournalRuns,
  makeLiveSessions,
  makeFills,
} from './data'

// Track polling state for backtest/optimize endpoints
let backtestPollCount = 0
let optimizePollCount = 0

export const handlers = [
  // ── System ──
  http.get('/api/v1/health', () => {
    return HttpResponse.json({ status: 'ok', version: '0.3.0' })
  }),

  http.get('/api/v1/system/status', () => {
    return HttpResponse.json({ initialized: true, db_path: '/tmp/flint.db' })
  }),

  // ── Data ──
  http.get('/api/v1/data/markets', () => {
    return HttpResponse.json({ markets: makeMarketInfo() })
  }),

  http.get('/api/v1/data/available-markets', () => {
    return HttpResponse.json({ markets: makeAvailableMarkets() })
  }),

  http.get('/api/v1/data/ohlcv', ({ request }) => {
    const url = new URL(request.url)
    const market = url.searchParams.get('market') || 'SOL-PERP'
    const resolution = parseInt(url.searchParams.get('resolution_s') || '3600')
    const limit = parseInt(url.searchParams.get('limit') || '100')
    return HttpResponse.json({
      market,
      resolution_s: resolution,
      venue: 'all',
      count: limit,
      candles: makeCandles(Math.min(limit, 200)),
    })
  }),

  http.get('/api/v1/data/check', () => {
    return HttpResponse.json(makeDataCheck(true))
  }),

  http.post('/api/v1/data/check-markets', () => {
    return HttpResponse.json(makeMultiMarketCheck(true))
  }),

  http.get('/api/v1/data/funding', () => {
    return HttpResponse.json(makeFundingByVenue())
  }),

  http.get('/api/v1/data/volume', () => {
    return HttpResponse.json(makeVenueVolume())
  }),

  http.post('/api/v1/data/download', () => {
    return HttpResponse.json({
      downloaded: 500,
      existing: 100,
      funding_fetched: 24,
      warnings: [],
    })
  }),

  http.get('/api/v1/data/freshness', () => {
    return HttpResponse.json({
      markets: { 'SOL-PERP': { last_ts: Date.now() / 1000, age_hours: 1 } },
    })
  }),

  http.get('/api/v1/data/correlation', () => {
    return HttpResponse.json({
      markets: ['SOL-PERP', 'BTC-PERP'],
      matrix: [[1, 0.85], [0.85, 1]],
    })
  }),

  http.get('/api/v1/data/borrow-rates', () => {
    return HttpResponse.json({ count: 0, rates: [] })
  }),

  http.get('/api/v1/data/providers', () => {
    return HttpResponse.json({
      providers: [{ name: 'drift_candles', available: true, data_types: ['candles'] }],
    })
  }),

  // ── Strategies ──
  http.get('/api/v1/strategies', () => {
    return HttpResponse.json({ strategies: makeStrategyCatalog() })
  }),

  http.get('/api/v1/user-strategies', () => {
    return HttpResponse.json({ strategies: makeUserStrategies() })
  }),

  http.post('/api/v1/user-strategies', () => {
    return HttpResponse.json({ ok: true })
  }),

  http.get('/api/v1/user-strategies/:name', ({ params }) => {
    return HttpResponse.json({
      name: params.name,
      code: `# Strategy: ${params.name}\nfrom flint.strategy import Strategy, Signal\n\nclass MyStrategy(Strategy):\n    def on_candle(self, candle, ctx):\n        return Signal.HOLD\n`,
    })
  }),

  http.delete('/api/v1/user-strategies/:name', () => {
    return HttpResponse.json({ deleted: true })
  }),

  http.post('/api/v1/user-strategies/validate', () => {
    return HttpResponse.json({ valid: true })
  }),

  // ── Backtest ──
  http.post('/api/v1/backtest/run', () => {
    backtestPollCount = 0
    return HttpResponse.json({ id: 'test-run-1', status: 'running' })
  }),

  http.get('/api/v1/backtest/:id/results', () => {
    backtestPollCount++
    if (backtestPollCount <= 1) {
      return HttpResponse.json({
        status: 'running',
        progress: {
          phase: 'backtest',
          pct: 50,
          detail: 'Processing candles...',
          elapsed_s: 1.2,
          candles: 1000,
        },
      })
    }
    return HttpResponse.json({
      status: 'complete',
      results: makeBacktestResult(),
      progress: {
        phase: 'done',
        pct: 100,
        detail: 'Complete',
        elapsed_s: 2.5,
        candles: 2000,
      },
    })
  }),

  http.post('/api/v1/backtest/run-regimes', () => {
    return HttpResponse.json({ id: 'regime-run-1', status: 'running' })
  }),

  http.get('/api/v1/backtest/compare', () => {
    return HttpResponse.json({ runs: [] })
  }),

  // ── Optimization ──
  http.post('/api/v1/optimize/run', () => {
    optimizePollCount = 0
    return HttpResponse.json({ id: 'opt-1', status: 'running' })
  }),

  http.get('/api/v1/optimize/:id/results', () => {
    optimizePollCount++
    if (optimizePollCount <= 1) {
      return HttpResponse.json({
        status: 'running',
        progress: {
          phase: 'optimizing',
          pct: 40,
          detail: 'Trial 20/50',
          elapsed_s: 5.0,
        },
      })
    }
    return HttpResponse.json({
      status: 'complete',
      results: makeOptResult(),
    })
  }),

  // ── Paper Trading ──
  http.get('/api/v1/paper/portfolio', () => {
    return HttpResponse.json(makePaperPortfolio())
  }),

  http.get('/api/v1/paper/status/:sessionId', ({ params }) => {
    return HttpResponse.json(makeSessionStatus(params.sessionId as string))
  }),

  http.get('/api/v1/paper/trades/:sessionId', () => {
    return HttpResponse.json({ trades: makeSessionTrades() })
  }),

  http.get('/api/v1/paper/:sessionId/equity-history', () => {
    return HttpResponse.json({
      equity_curve: Array.from({ length: 50 }, (_, i) => ({
        ts: 1704067200 + i * 3600,
        equity: 10000 + i * 10 + Math.sin(i) * 50,
        is_replay: i < 10,
      })),
    })
  }),

  http.post('/api/v1/paper/start', () => {
    return HttpResponse.json({ session_id: 'sess-new-1', status: 'running' })
  }),

  http.post('/api/v1/paper/deploy', () => {
    return HttpResponse.json({ session_id: 'sess-deployed-1', status: 'deployed' })
  }),

  http.post('/api/v1/paper/stop', () => {
    return HttpResponse.json({ session_id: 'sess-001', stopped: true })
  }),

  http.post('/api/v1/paper/kill', () => {
    return HttpResponse.json({ session_id: 'sess-001', killed: true })
  }),

  http.get('/api/v1/paper/sessions', () => {
    return HttpResponse.json({ sessions: [] })
  }),

  http.post('/api/v1/paper/redeploy', () => {
    return HttpResponse.json({ old_session_id: 'sess-001', new_session_id: 'sess-003', status: 'redeployed' })
  }),

  http.post('/api/v1/paper/redeploy-all', () => {
    return HttpResponse.json({ redeployed: 1 })
  }),

  // ── Journal ──
  http.get('/api/v1/journal/runs', () => {
    return HttpResponse.json({ runs: makeJournalRuns() })
  }),

  http.get('/api/v1/journal/runs/:runId', ({ params }) => {
    return HttpResponse.json({
      run_id: params.runId,
      strategy_name: 'MA Crossover',
      total_pnl: 1250,
    })
  }),

  http.delete('/api/v1/journal/runs/:runId', () => {
    return HttpResponse.json({ deleted: true })
  }),

  // ── Live Monitor ──
  http.get('/api/v1/live/sessions', () => {
    return HttpResponse.json({ sessions: makeLiveSessions() })
  }),

  http.get('/api/v1/live/equity/:sessionId', () => {
    return HttpResponse.json({
      equity: Array.from({ length: 20 }, (_, i) => ({
        ts: 1704067200 + i * 300,
        equity: 10000 + i * 10,
        cash: 9500 + i * 5,
        unrealized_pnl: i * 5,
      })),
    })
  }),

  http.get('/api/v1/live/fills', () => {
    return HttpResponse.json({ fills: makeFills() })
  }),

  // ── MEV ──
  http.post('/api/v1/mev/scan/arb', () => {
    return HttpResponse.json({ routes: [], scan_time_ms: 120 })
  }),

  // ── Collector ──
  http.get('/api/v1/collector/status', () => {
    return HttpResponse.json({ collectors: [] })
  }),
]

export const server = setupServer(...handlers)

/**
 * Helper: reset backtest poll counter (call in tests that need fresh polling)
 */
export function resetPollCounters() {
  backtestPollCount = 0
  optimizePollCount = 0
}
