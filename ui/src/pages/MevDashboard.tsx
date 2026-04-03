import { useState } from 'react'
import { ScatterChart, Scatter, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'

interface ArbRoute {
  pools: string[]
  tokens: string[]
  profit: number
  profit_bps: number
  hops: number
}

export default function MevDashboard() {
  const [routes, setRoutes] = useState<ArbRoute[]>([])
  const [loading, setLoading] = useState(false)
  const [scanned, setScanned] = useState(false)

  const runDemoScan = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/v1/mev/scan/arb', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pools: [
            { pool_address: 'pool_sol_usdc', dex: 'raydium', token_a_mint: 'SOL', token_b_mint: 'USDC', reserve_a: 1000, reserve_b: 100000, fee_rate: 0.003 },
            { pool_address: 'pool_usdc_eth', dex: 'orca', token_a_mint: 'USDC', token_b_mint: 'ETH', reserve_a: 200000, reserve_b: 100, fee_rate: 0.003 },
            { pool_address: 'pool_eth_sol', dex: 'raydium', token_a_mint: 'ETH', token_b_mint: 'SOL', reserve_a: 50, reserve_b: 1050, fee_rate: 0.003 },
          ],
          start_token: 'SOL',
          amount: 10,
          min_profit_bps: 1,
        }),
      })
      const data = await res.json()
      setRoutes(data.routes || [])
      setScanned(true)
    } catch {
      setRoutes([])
    }
    setLoading(false)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-baseline gap-4">
        <h1 className="font-[var(--font-display)] text-2xl text-white/90 italic">MEV</h1>
        <span className="text-[10px] text-ghost tracking-[0.2em]">// MAXIMUM EXTRACTABLE VALUE</span>
      </div>

      {/* scanner */}
      <div className="border border-border bg-surface/60 backdrop-blur">
        <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
          <span className="w-2 h-2 bg-phosphor/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">ARB.SCANNER</span>
        </div>
        <div className="p-4">
          <p className="text-xs text-ghost/60 mb-4 max-w-lg">
            Triangular arbitrage detection across AMM pools. Demo uses
            SOL/USDC/ETH with intentional mispricing in ETH/SOL pool.
          </p>
          <button
            onClick={runDemoScan}
            disabled={loading}
            className="px-6 py-2 bg-amber text-void text-xs font-semibold tracking-[0.15em] hover:bg-amber-dim disabled:bg-border disabled:text-ghost transition-all duration-200"
          >
            {loading ? '> SCANNING...' : '> SCAN_ARB_ROUTES'}
          </button>
        </div>
      </div>

      {/* results */}
      {routes.length > 0 && (
        <div className="border border-border bg-surface/60 backdrop-blur" style={{ animation: 'fadeUp 0.4s ease' }}>
          <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
            <span className="text-[10px] text-ghost tracking-[0.2em]">ROUTES.FOUND</span>
            <span className="text-[10px] text-gain">{routes.length} profitable</span>
          </div>
          <div className="divide-y divide-border">
            {routes.map((r, i) => (
              <div
                key={i}
                className="p-4 hover:bg-amber-glow transition-colors"
                style={{ animation: `slideIn 0.3s ease ${i * 0.1}s both` }}
              >
                <div className="flex items-center gap-3 mb-2">
                  <span className="text-xs font-semibold text-gain">
                    +{r.profit_bps.toFixed(1)} bps
                  </span>
                  <span className="text-[10px] text-ghost">{r.hops} hops</span>
                  <span className="text-[10px] text-ghost/40 ml-auto">
                    route.{String(i).padStart(2, '0')}
                  </span>
                </div>
                <div className="text-xs text-terminal tracking-wider mb-1">
                  {r.tokens.map((t, j) => (
                    <span key={j}>
                      <span className="text-amber">{t}</span>
                      {j < r.tokens.length - 1 && <span className="text-ghost/30 mx-1">{'->'}</span>}
                    </span>
                  ))}
                </div>
                <div className="text-[10px] text-ghost/50">
                  net_profit: <span className="text-gain">{r.profit.toFixed(6)}</span> {r.tokens[0]}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {scanned && routes.length === 0 && (
        <div className="border border-border/50 py-12 text-center">
          <div className="text-ghost/30 text-xs tracking-[0.3em]">NO PROFITABLE ROUTES</div>
          <div className="text-ghost/20 text-[10px] mt-2">All pools at equilibrium</div>
        </div>
      )}

      {!scanned && !loading && (
        <div className="border border-border/30 py-16 text-center">
          <pre className="text-ghost/15 text-[10px] leading-relaxed">
{`  +---------+         +---------+
  |   SOL   | ------> |  USDC   |
  +---------+         +---------+
       ^                   |
       |                   v
       |              +---------+
       +--------------+   ETH   |
                      +---------+`}
          </pre>
          <div className="text-ghost/25 text-[10px] tracking-[0.2em] mt-4">
            INITIATE SCAN TO DETECT ARBITRAGE
          </div>
        </div>
      )}

      {/* opportunity timeline */}
      <div className="border border-border bg-surface/60">
        <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
          <span className="w-2 h-2 bg-amber/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">OPPORTUNITY.TIMELINE</span>
          <span className="text-[10px] text-ghost/40 ml-2">profit_bps vs time</span>
        </div>
        <div className="p-4">
          {routes.length === 0 ? (
            <div className="flex items-center justify-center h-32">
              <span className="text-ghost/30 text-[10px] tracking-[0.3em]">RUN AN ARB SCAN TO SEE TIMELINE</span>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <ScatterChart margin={{ top: 4, right: 8, left: 8, bottom: 4 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="rgba(255,255,255,0.04)" />
                <XAxis
                  dataKey="hops"
                  name="hops"
                  tick={{ fill: 'var(--color-ghost)', fontSize: 9 }}
                  axisLine={{ stroke: 'var(--color-border)' }}
                  tickLine={false}
                  label={{ value: 'Hops', position: 'insideBottom', offset: -2, fill: 'var(--color-ghost)', fontSize: 9 }}
                />
                <YAxis
                  dataKey="profit_bps"
                  name="profit_bps"
                  tick={{ fill: 'var(--color-ghost)', fontSize: 9 }}
                  axisLine={false}
                  tickLine={false}
                  width={48}
                  label={{ value: 'bps', angle: -90, position: 'insideLeft', fill: 'var(--color-ghost)', fontSize: 9 }}
                />
                <Tooltip
                  content={({ active, payload }: any) => {
                    if (!active || !payload?.length) return null
                    const d = payload[0].payload as ArbRoute
                    return (
                      <div className="bg-panel border border-border px-3 py-2 text-[11px] font-mono space-y-0.5">
                        <div className="text-gain">+{d.profit_bps.toFixed(2)} bps</div>
                        <div className="text-ghost/70">{d.hops} hops · {d.tokens.join(' → ')}</div>
                      </div>
                    )
                  }}
                />
                <Scatter data={routes} fill="var(--color-gain)" fillOpacity={0.8} r={5} />
              </ScatterChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  )
}
