import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AsciiFire from '../components/AsciiFire'
import CollectorStatus from '../components/CollectorStatus'

interface MarketData {
  market: string
  resolution_s: number
  candle_count: number
  first_ts: number
  last_ts: number
}

export default function Dashboard() {
  const [markets, setMarkets] = useState<MarketData[]>([])
  const [health, setHealth] = useState<string>('...')
  const [entered, setEntered] = useState(false)

  useEffect(() => {
    requestAnimationFrame(() => setEntered(true))

    fetch('/api/v1/health')
      .then((r) => r.json())
      .then(() => setHealth('ONLINE'))
      .catch(() => setHealth('OFFLINE'))

    fetch('/api/v1/data/markets')
      .then((r) => r.json())
      .then((d) => setMarkets(d.markets || []))
      .catch(() => {})
  }, [])

  const fmtDate = (ts: number) =>
    new Date(ts * 1000).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })

  const totalCandles = markets.reduce((s, m) => s + m.candle_count, 0)

  return (
    <div>
      {/* ============================================================
          HERO — fire animation + title
          ============================================================ */}
      <div className={`relative -mx-6 -mt-8 px-6 transition-opacity duration-1000 ${entered ? 'opacity-100' : 'opacity-0'}`}>
        <div className="min-h-[90vh] flex flex-col items-center justify-center relative">
          {/* top-left system tag */}
          <div
            className="absolute top-8 left-6 text-[10px] text-ghost/40 tracking-[0.3em]"
            style={{ animation: 'fadeUp 0.6s ease 0.3s both' }}
          >
            FLINT v0.1.0 / {new Date().getFullYear()}
          </div>

          {/* top-right status */}
          <div
            className="absolute top-8 right-6 flex items-center gap-3 text-[10px] tracking-[0.2em]"
            style={{ animation: 'fadeUp 0.6s ease 0.5s both' }}
          >
            <span className={health === 'ONLINE' ? 'text-phosphor' : 'text-loss'}>
              {health}
            </span>
            <span className="w-1.5 h-1.5 rounded-full bg-phosphor animate-pulse" />
          </div>

          {/* fire animation — the centerpiece */}
          <div className="mb-2" style={{ animation: 'fadeUp 0.8s ease 0.2s both' }}>
            <AsciiFire cols={90} rows={32} />
          </div>

          {/* title below fire */}
          <div className="text-center" style={{ animation: 'fadeUp 1s ease 5s both' }}>
            <h1 className="font-[var(--font-display)] text-5xl sm:text-6xl md:text-7xl lg:text-8xl text-white/90 font-normal leading-[0.95] mb-2">
              Flint
            </h1>
            <p className="font-[var(--font-display)] italic text-lg sm:text-xl md:text-2xl text-amber/70 mb-4">
              Strike alpha on Solana
            </p>
            <div className="flex justify-center gap-8 text-[10px] text-ghost/40 tracking-[0.2em]">
              {['BACKTEST', 'TRADE', 'EXTRACT'].map((s) => (
                <span key={s}>{s}</span>
              ))}
            </div>
          </div>
        </div>

        {/* divider */}
        <div className="h-px bg-gradient-to-r from-transparent via-amber/30 to-transparent" />
      </div>

      {/* ============================================================
          STATUS CARDS
          ============================================================ */}
      <div className="mt-10 grid grid-cols-1 sm:grid-cols-4 gap-px bg-border">
        {[
          {
            label: 'SYS.STATUS',
            value: health,
            color: health === 'ONLINE' ? 'text-phosphor' : 'text-loss',
            sub: 'api.flint.local:8000',
          },
          { label: 'MARKETS', value: String(markets.length), color: 'text-amber', sub: 'perp markets indexed' },
          { label: 'CANDLES', value: totalCandles.toLocaleString(), color: 'text-white/80', sub: '1h resolution bars' },
          { label: 'PROTOCOL', value: 'DRIFT', color: 'text-amber', sub: 'v2 mainnet-beta' },
        ].map((card, i) => (
          <div
            key={card.label}
            className="bg-surface p-5 hover:bg-panel transition-colors relative"
            style={{ animation: `fadeUp 0.4s ease ${1.2 + i * 0.1}s both` }}
          >
            <div className="text-[9px] text-ghost/50 tracking-[0.25em] mb-2">{card.label}</div>
            <div className={`text-2xl font-semibold ${card.color} tracking-tight tabular-nums`}>{card.value}</div>
            <div className="text-[10px] text-ghost/30 mt-1">{card.sub}</div>
          </div>
        ))}
      </div>

      {/* ============================================================
          GUIDE — HOW TO USE
          ============================================================ */}
      <div className="mt-12" style={{ animation: 'fadeUp 0.6s ease 1.6s both' }}>
        <div className="flex items-center gap-3 mb-6">
          <div className="w-2 h-2 bg-amber/60" />
          <h2 className="text-[11px] text-ghost tracking-[0.25em]">GETTING STARTED</h2>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-px bg-border">
          {/* Step 1 */}
          <div className="bg-surface p-6 group hover:bg-panel transition-colors">
            <div className="text-[10px] text-amber/40 tracking-[0.2em] mb-3">01</div>
            <Link to="/backtest" className="block">
              <h3 className="text-sm text-white/80 font-medium mb-2 group-hover:text-amber transition-colors">
                Backtest Lab
              </h3>
            </Link>
            <p className="text-[11px] text-ghost/60 leading-relaxed mb-3">
              Run strategy backtests on historical Drift perp data.
              Configure MA crossover periods, pick a market and date range,
              then hit run. Results show equity curves, drawdown, Sharpe,
              Sortino, and a full trade log.
            </p>
            <div className="text-[10px] text-ghost/30 border-t border-border pt-2 mt-auto">
              <span className="text-amber/50">{'->'}</span> SOL-PERP, BTC-PERP, ETH-PERP
            </div>
          </div>

          {/* Step 2 */}
          <div className="bg-surface p-6 group hover:bg-panel transition-colors">
            <div className="text-[10px] text-amber/40 tracking-[0.2em] mb-3">02</div>
            <Link to="/data" className="block">
              <h3 className="text-sm text-white/80 font-medium mb-2 group-hover:text-amber transition-colors">
                Data Explorer
              </h3>
            </Link>
            <p className="text-[11px] text-ghost/60 leading-relaxed mb-3">
              Browse OHLCV candle data stored in the local DuckDB.
              Select a market, load historical data, and view price
              and volume charts. All data sourced free from Drift's
              public S3 bucket.
            </p>
            <div className="text-[10px] text-ghost/30 border-t border-border pt-2 mt-auto">
              <span className="text-amber/50">{'->'}</span> 1h candles, unlimited history
            </div>
          </div>

          {/* Step 3 */}
          <div className="bg-surface p-6 group hover:bg-panel transition-colors">
            <div className="text-[10px] text-amber/40 tracking-[0.2em] mb-3">03</div>
            <Link to="/mev" className="block">
              <h3 className="text-sm text-white/80 font-medium mb-2 group-hover:text-amber transition-colors">
                MEV Scanner
              </h3>
            </Link>
            <p className="text-[11px] text-ghost/60 leading-relaxed mb-3">
              Detect arbitrage routes across AMM pools using
              constant-product math and DFS graph search. Scan for
              liquidation opportunities on Drift positions. Run the
              built-in demo or supply your own pool data.
            </p>
            <div className="text-[10px] text-ghost/30 border-t border-border pt-2 mt-auto">
              <span className="text-amber/50">{'->'}</span> arb detection, liquidation scan
            </div>
          </div>
        </div>
      </div>

      {/* ============================================================
          QUICK START TERMINAL
          ============================================================ */}
      <div className="mt-10" style={{ animation: 'fadeUp 0.6s ease 1.8s both' }}>
        <div className="border border-border bg-surface/80">
          <div className="px-4 py-2 border-b border-border flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-loss/60" />
            <span className="w-2 h-2 rounded-full bg-amber/60" />
            <span className="w-2 h-2 rounded-full bg-gain/60" />
            <span className="text-[10px] text-ghost/40 tracking-wider ml-2">terminal</span>
          </div>
          <div className="p-5 text-[11px] leading-relaxed">
            <div className="text-ghost/40 mb-2"># Quick start — backtesting in 3 commands</div>
            <div className="mb-1">
              <span className="text-amber/60">$</span>{' '}
              <span className="text-terminal">pip install -e ".[dev]"</span>
              <span className="text-ghost/30 ml-4"># install flint + dependencies</span>
            </div>
            <div className="mb-1">
              <span className="text-amber/60">$</span>{' '}
              <span className="text-terminal">uvicorn flint.api.main:app --port 8000</span>
              <span className="text-ghost/30 ml-4"># start API backend</span>
            </div>
            <div className="mb-1">
              <span className="text-amber/60">$</span>{' '}
              <span className="text-terminal">cd ui && npm run dev</span>
              <span className="text-ghost/30 ml-4"># start this UI</span>
            </div>
            <div className="mt-4 text-ghost/40">
              # Or run a backtest directly from Python:
            </div>
            <div className="mb-1">
              <span className="text-amber/60">$</span>{' '}
              <span className="text-terminal">python scripts/run_backtest.py</span>
            </div>
            <div className="text-ghost/30 mt-3 text-[10px]">
              All data is free. No API keys needed for backtesting. Data from Drift's public S3 bucket.
            </div>
          </div>
        </div>
      </div>

      {/* ============================================================
          DATA TABLE
          ============================================================ */}
      {markets.length > 0 && (
        <div className="mt-10" style={{ animation: 'fadeUp 0.6s ease 2s both' }}>
          <div className="border border-border bg-surface/60 backdrop-blur">
            <div className="px-4 py-3 border-b border-border flex items-center gap-3">
              <span className="w-1.5 h-1.5 bg-amber/50" />
              <span className="text-[10px] text-ghost tracking-[0.2em]">DATA.COVERAGE</span>
              <span className="text-[10px] text-amber/40 ml-auto">{markets.length} markets</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-ghost/40 border-b border-border text-[10px] tracking-[0.15em]">
                    <th className="py-2.5 px-4">MARKET</th>
                    <th className="py-2.5 px-4">RES</th>
                    <th className="py-2.5 px-4 text-right">RECORDS</th>
                    <th className="py-2.5 px-4">FIRST</th>
                    <th className="py-2.5 px-4">LAST</th>
                  </tr>
                </thead>
                <tbody>
                  {markets.map((m, i) => (
                    <tr
                      key={i}
                      className="border-b border-border/30 hover:bg-amber-glow transition-colors"
                    >
                      <td className="py-2 px-4 text-amber font-medium">{m.market}</td>
                      <td className="py-2 px-4 text-ghost">
                        {m.resolution_s >= 3600 ? `${m.resolution_s / 3600}h` : `${m.resolution_s / 60}m`}
                      </td>
                      <td className="py-2 px-4 text-right text-white/70 tabular-nums">{m.candle_count.toLocaleString()}</td>
                      <td className="py-2 px-4 text-ghost/40">{fmtDate(m.first_ts)}</td>
                      <td className="py-2 px-4 text-ghost/40">{fmtDate(m.last_ts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ============================================================
          COLLECTOR STATUS
          ============================================================ */}
      <div className="mt-10" style={{ animation: 'fadeUp 0.6s ease 2.2s both' }}>
        <CollectorStatus />
      </div>

      {/* spacer */}
      <div className="h-16" />
    </div>
  )
}
