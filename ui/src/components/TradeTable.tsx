interface Trade {
  entry_ts: number
  exit_ts: number
  side: string
  entry_price: number
  exit_price: number
  size: number
  pnl: number
  holding_s: number
}

interface Props {
  trades: Trade[]
}

const fmtDate = (ts: number) => new Date(ts * 1000).toLocaleString(undefined, {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
})

export default function TradeTable({ trades }: Props) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-ghost/50 border-b border-border text-[10px] tracking-[0.12em]">
            <th className="py-2 px-2 w-8">#</th>
            <th className="py-2 px-2">SIDE</th>
            <th className="py-2 px-2">ENTRY</th>
            <th className="py-2 px-2">EXIT</th>
            <th className="py-2 px-2 text-right">ENTRY$</th>
            <th className="py-2 px-2 text-right">EXIT$</th>
            <th className="py-2 px-2 text-right">SIZE</th>
            <th className="py-2 px-2 text-right">PNL</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr
              key={i}
              className="border-b border-border/30 hover:bg-amber-glow transition-colors"
              style={{ animation: `slideIn 0.2s ease ${i * 0.03}s both` }}
            >
              <td className="py-1.5 px-2 text-ghost/30 tabular-nums">{String(i + 1).padStart(2, '0')}</td>
              <td className="py-1.5 px-2">
                <span className={`text-[10px] tracking-wider ${
                  t.side === 'long' ? 'text-gain' : 'text-loss'
                }`}>
                  {t.side === 'long' ? 'LONG' : 'SHRT'}
                </span>
              </td>
              <td className="py-1.5 px-2 text-ghost/60">{fmtDate(t.entry_ts)}</td>
              <td className="py-1.5 px-2 text-ghost/60">{fmtDate(t.exit_ts)}</td>
              <td className="py-1.5 px-2 text-right text-terminal tabular-nums">{t.entry_price?.toFixed(2) ?? '-'}</td>
              <td className="py-1.5 px-2 text-right text-terminal tabular-nums">{t.exit_price?.toFixed(2) ?? '-'}</td>
              <td className="py-1.5 px-2 text-right text-ghost tabular-nums">{t.size?.toFixed(2) ?? '-'}</td>
              <td className={`py-1.5 px-2 text-right font-medium tabular-nums ${(t.pnl ?? 0) >= 0 ? 'text-gain' : 'text-loss'}`}>
                {(t.pnl ?? 0) >= 0 ? '+' : ''}{t.pnl?.toFixed(2) ?? '-'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
