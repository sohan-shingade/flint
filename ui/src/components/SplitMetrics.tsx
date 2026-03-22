interface Trade {
  side: string
  pnl: number
  entry_price: number
  exit_price: number
  holding_s: number
}

interface Props {
  trades: Trade[]
}

function computeStats(trades: Trade[]) {
  if (!trades.length) return null
  const winners = trades.filter(t => t.pnl > 0)
  const losers = trades.filter(t => t.pnl <= 0)
  const totalPnl = trades.reduce((s, t) => s + t.pnl, 0)
  const avgWin = winners.length ? winners.reduce((s, t) => s + t.pnl, 0) / winners.length : 0
  const avgLoss = losers.length ? losers.reduce((s, t) => s + t.pnl, 0) / losers.length : 0
  const grossWins = winners.reduce((s, t) => s + t.pnl, 0)
  const grossLosses = Math.abs(losers.reduce((s, t) => s + t.pnl, 0))
  const profitFactor = grossLosses > 0 ? grossWins / grossLosses : grossWins > 0 ? Infinity : 0

  return {
    trades: trades.length,
    winRate: ((winners.length / trades.length) * 100).toFixed(1),
    totalPnl: totalPnl.toFixed(2),
    avgWin: avgWin.toFixed(2),
    avgLoss: avgLoss.toFixed(2),
    profitFactor: profitFactor === Infinity ? '∞' : profitFactor.toFixed(2),
    best: trades.length ? Math.max(...trades.map(t => t.pnl)).toFixed(2) : '0',
    worst: trades.length ? Math.min(...trades.map(t => t.pnl)).toFixed(2) : '0',
  }
}

export default function SplitMetrics({ trades }: Props) {
  const longs = trades.filter(t => t.side === 'long')
  const shorts = trades.filter(t => t.side === 'short')

  const allStats = computeStats(trades)
  const longStats = computeStats(longs)
  const shortStats = computeStats(shorts)

  if (!allStats) return null

  const columns = [
    { label: 'ALL', stats: allStats, color: 'text-terminal' },
    { label: 'LONG', stats: longStats, color: 'text-gain' },
    { label: 'SHORT', stats: shortStats, color: 'text-loss' },
  ]

  const rows = [
    { key: 'trades', label: 'TRADES' },
    { key: 'winRate', label: 'WIN.RATE', suffix: '%' },
    { key: 'totalPnl', label: 'TOTAL.PNL', prefix: '$' },
    { key: 'avgWin', label: 'AVG.WIN', prefix: '$' },
    { key: 'avgLoss', label: 'AVG.LOSS', prefix: '$' },
    { key: 'profitFactor', label: 'PROFIT.F' },
    { key: 'best', label: 'BEST', prefix: '$' },
    { key: 'worst', label: 'WORST', prefix: '$' },
  ]

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px] font-mono">
        <thead>
          <tr>
            <th className="text-left text-[9px] text-ghost/50 tracking-wider px-2 py-1.5 border-b border-border"></th>
            {columns.map(c => (
              <th key={c.label} className={`text-right text-[9px] tracking-wider px-2 py-1.5 border-b border-border ${c.color}`}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.key} className="border-b border-border/30">
              <td className="text-[9px] text-ghost/60 tracking-wider px-2 py-1">{row.label}</td>
              {columns.map(c => {
                const val = c.stats ? (c.stats as any)[row.key] : '—'
                const isNeg = typeof val === 'string' && val.startsWith('-')
                return (
                  <td key={c.label} className={`text-right px-2 py-1 ${
                    row.key === 'totalPnl' || row.key === 'best' || row.key === 'worst'
                      ? isNeg ? 'text-loss' : 'text-gain'
                      : 'text-terminal'
                  }`}>
                    {val !== '—' ? `${row.prefix || ''}${val}${row.suffix || ''}` : '—'}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
