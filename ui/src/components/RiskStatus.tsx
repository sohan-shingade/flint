interface RiskStatusProps {
  riskStatus: {
    current_drawdown: number
    daily_loss: number
    max_position_used: number
    margin_ratio: number
    liquidation_distance_pct: number
    any_breached: boolean
  }
  riskConfig: {
    max_drawdown_pct: number
    daily_loss_limit: number
    max_position_pct: number
    liquidation_enabled: boolean
  }
}

export default function RiskStatus({ riskStatus, riskConfig }: RiskStatusProps) {
  const bars = [
    {
      label: 'Drawdown',
      value: riskStatus.current_drawdown,
      limit: riskConfig.max_drawdown_pct,
      format: (v: number) => `${(v * 100).toFixed(1)}%`,
    },
    {
      label: 'Daily Loss',
      value: Math.max(0, riskStatus.daily_loss),
      limit: riskConfig.daily_loss_limit,
      format: (v: number) => `$${v.toFixed(0)}`,
    },
    {
      label: 'Position',
      value: riskStatus.max_position_used,
      limit: riskConfig.max_position_pct,
      format: (v: number) => `${(v * 100).toFixed(0)}%`,
    },
    {
      label: 'Liq Dist',
      value: 1 - riskStatus.liquidation_distance_pct,
      limit: 1,
      format: (_: number) => `${(riskStatus.liquidation_distance_pct * 100).toFixed(0)}%`,
    },
  ]

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">Risk Status</h3>
      {riskStatus.any_breached && (
        <div className="text-xs text-red-400 font-medium">LIMIT BREACHED</div>
      )}
      {bars.map(bar => {
        const pct = bar.limit > 0 ? Math.min(bar.value / bar.limit, 1) : 0
        const color = pct < 0.5 ? 'bg-green-500' : pct < 0.8 ? 'bg-yellow-500' : 'bg-red-500'
        return (
          <div key={bar.label} className="flex items-center gap-2 text-xs">
            <span className="w-16 text-zinc-400 shrink-0">{bar.label}</span>
            <div className="flex-1 h-2 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className={`h-full ${color} rounded-full transition-all duration-300`}
                style={{ width: `${pct * 100}%` }}
              />
            </div>
            <span className="w-12 text-right text-zinc-300 shrink-0">{bar.format(bar.value)}</span>
          </div>
        )
      })}
    </div>
  )
}
