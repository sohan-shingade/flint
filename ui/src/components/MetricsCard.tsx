interface Props {
  metrics: Record<string, any>
}

const fmt = (v: number, decimals = 2) => v?.toFixed(decimals) ?? '-'
const pct = (v: number) => `${fmt(v)}%`

export default function MetricsCard({ metrics }: Props) {
  const items = [
    { label: 'TOTAL.PNL', value: `$${fmt(metrics.total_pnl)}`, accent: metrics.total_pnl >= 0 },
    { label: 'RETURN', value: pct(metrics.total_return_pct), accent: metrics.total_return_pct >= 0 },
    { label: 'ANN.RETURN', value: pct(metrics.annualized_return_pct), accent: metrics.annualized_return_pct >= 0 },
    { label: 'SHARPE', value: fmt(metrics.sharpe_ratio) },
    { label: 'SORTINO', value: fmt(metrics.sortino_ratio) },
    { label: 'MAX.DD', value: pct(metrics.max_drawdown * 100) },
    { label: 'WIN.RATE', value: pct(metrics.win_rate * 100) },
    { label: 'PROFIT.F', value: fmt(metrics.profit_factor) },
    { label: 'TRADES', value: String(metrics.total_trades) },
    { label: 'AVG.WIN', value: `$${fmt(metrics.avg_win)}` },
    { label: 'AVG.LOSS', value: `$${fmt(metrics.avg_loss)}` },
    { label: 'BEST', value: `$${fmt(metrics.best_trade)}` },
    { label: 'WORST', value: `$${fmt(metrics.worst_trade)}` },
  ]

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-5 gap-px bg-border">
      {items.map((item, i) => (
        <div
          key={item.label}
          className="bg-surface p-3 hover:bg-panel transition-colors"
          style={{ animation: `fadeUp 0.3s ease ${i * 0.03}s both` }}
        >
          <div className="text-[9px] text-ghost/60 tracking-[0.2em] mb-1">{item.label}</div>
          <div className={`text-sm font-medium tabular-nums ${
            item.accent !== undefined
              ? item.accent ? 'text-gain' : 'text-loss'
              : 'text-white/80'
          }`}>
            {item.value}
          </div>
        </div>
      ))}
    </div>
  )
}
