/**
 * RegimeResults — displays multi-regime backtest results with drill-down.
 */
import { useState } from 'react'
import { REGIME_MAP, REGIME_TYPES } from '../constants/regimes'

import EquityCurve from './EquityCurve'
import DrawdownChart from './DrawdownChart'
import MetricsCard from './MetricsCard'
import TradeTable from './TradeTable'

interface Props {
  results: {
    mode: string
    per_regime: Record<string, any>
    aggregate: {
      avg_sharpe: number
      worst_drawdown: number
      best_regime: string
      worst_regime: string
      regime_summary: any[]
    }
  }
}

export function RegimeResults({ results }: Props) {
  const [selectedRegimeId, setSelectedRegimeId] = useState<string | null>(null)
  const { aggregate, per_regime } = results

  // Drill-down view
  if (selectedRegimeId && per_regime[selectedRegimeId]) {
    const regime = REGIME_MAP[selectedRegimeId]
    const raw = per_regime[selectedRegimeId]
    // Metrics may be nested under raw.metrics or flat
    const m = raw.metrics || raw
    return (
      <div className="space-y-4">
        <button
          onClick={() => setSelectedRegimeId(null)}
          className="text-xs text-amber hover:text-amber-dim flex items-center gap-1 mb-2"
        >
          &larr; BACK TO REGIME SUMMARY
        </button>

        <div className="flex items-center gap-2 mb-3">
          {regime && <span className="inline-block w-2.5 h-2.5" style={{ backgroundColor: regime.color }} />}
          <span className="text-sm text-terminal font-medium">{regime?.label || selectedRegimeId}</span>
          {regime && <span className="text-[10px] text-ghost">{regime.startDate} - {regime.endDate}</span>}
        </div>

        {raw.error ? (
          <div className="border border-border bg-surface/40 px-4 py-3 text-xs text-ghost">{raw.error}</div>
        ) : (
          <>
            <div className="grid grid-cols-5 gap-2">
              {[
                { label: 'PnL', value: `$${(m.total_pnl ?? 0).toFixed(0)}`, color: (m.total_pnl ?? 0) >= 0 ? '#57c84d' : '#e84d4d' },
                { label: 'Sharpe', value: (m.sharpe_ratio ?? 0).toFixed(2) },
                { label: 'Max DD', value: `${((m.max_drawdown ?? 0) * 100).toFixed(1)}%` },
                { label: 'Trades', value: String(m.total_trades ?? 0) },
                { label: 'Win Rate', value: `${((m.win_rate ?? 0) * 100).toFixed(0)}%` },
              ].map(item => (
                <div key={item.label} className="border border-border bg-surface/40 px-3 py-2 text-center">
                  <div className="text-[9px] text-ghost tracking-wider">{item.label}</div>
                  <div className="text-sm text-terminal font-mono" style={item.color ? { color: item.color } : {}}>{item.value}</div>
                </div>
              ))}
            </div>

            {raw.equity_curve && raw.equity_curve.length > 0 && (
              <div className="grid grid-cols-2 gap-3">
                <EquityCurve equity={raw.equity_curve} />
                <DrawdownChart drawdown={raw.drawdown_curve || raw.equity_curve} />
              </div>
            )}

            {m.sharpe_ratio !== undefined && <MetricsCard metrics={m} />}

            {(raw.trades || m.trades) && (raw.trades || m.trades).length > 0 && (
              <TradeTable trades={raw.trades || m.trades} />
            )}
          </>
        )}
      </div>
    )
  }

  // Summary view
  const summary = aggregate.regime_summary || []
  const regimeCount = summary.length

  return (
    <div className="space-y-4">
      {/* Aggregate banner */}
      <div className="border border-border bg-surface/60 px-4 py-3">
        <div className="flex items-center gap-2 mb-2">
          <span className="w-2 h-2 bg-amber/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">REGIME.SUMMARY</span>
        </div>
        <div className="grid grid-cols-5 gap-4 text-center">
          <div>
            <div className="text-[9px] text-ghost">REGIMES TESTED</div>
            <div className="text-lg text-terminal font-mono">{regimeCount}</div>
          </div>
          <div>
            <div className="text-[9px] text-ghost">AVG SHARPE</div>
            <div className="text-lg font-mono" style={{ color: (aggregate.avg_sharpe ?? 0) >= 0 ? '#57c84d' : '#e84d4d' }}>
              {(aggregate.avg_sharpe ?? 0).toFixed(2)}
            </div>
          </div>
          <div>
            <div className="text-[9px] text-ghost">WORST DD</div>
            <div className="text-lg text-terminal font-mono">
              {((aggregate.worst_drawdown ?? 0) * 100).toFixed(1)}%
            </div>
          </div>
          <div>
            <div className="text-[9px] text-ghost">BEST REGIME</div>
            <div className="text-xs text-terminal truncate">
              {REGIME_MAP[aggregate.best_regime]?.label || aggregate.best_regime || '-'}
            </div>
          </div>
          <div>
            <div className="text-[9px] text-ghost">WORST REGIME</div>
            <div className="text-xs text-terminal truncate">
              {REGIME_MAP[aggregate.worst_regime]?.label || aggregate.worst_regime || '-'}
            </div>
          </div>
        </div>
      </div>

      {/* Per-regime table */}
      <div className="border border-border">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-surface/40 text-ghost text-[10px] tracking-wider">
              <th className="text-left px-3 py-2">REGIME</th>
              <th className="text-left px-2 py-2">TYPE</th>
              <th className="text-left px-2 py-2">PERIOD</th>
              <th className="text-right px-2 py-2">PnL</th>
              <th className="text-right px-2 py-2">SHARPE</th>
              <th className="text-right px-2 py-2">MAX DD</th>
              <th className="text-right px-2 py-2">TRADES</th>
              <th className="text-right px-3 py-2">WIN RATE</th>
            </tr>
          </thead>
          <tbody>
            {summary.map((entry: any, i: number) => {
              const regime = REGIME_MAP[entry.id]
              const rtype = entry.regime_type || entry.type || ''
              const typeMeta = REGIME_TYPES[rtype]
              const hasError = entry.has_error || per_regime[entry.id]?.error
              const pnl = entry.total_pnl ?? entry.pnl ?? 0
              const sharpe = entry.sharpe ?? entry.sharpe_ratio ?? 0
              const maxDD = entry.max_drawdown ?? entry.max_dd ?? 0
              const trades = entry.total_trades ?? entry.trades ?? 0
              const winRate = entry.win_rate ?? 0
              return (
                <tr
                  key={entry.id}
                  onClick={() => !hasError && setSelectedRegimeId(entry.id)}
                  className={`border-t border-border/50 transition-colors ${
                    hasError ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:bg-surface/60'
                  } ${i % 2 === 0 ? '' : 'bg-surface/20'}`}
                >
                  <td className="px-3 py-2 flex items-center gap-2">
                    <span className="w-2 h-2 flex-shrink-0" style={{ backgroundColor: regime?.color || '#78788a' }} />
                    <span className="text-terminal">{entry.label}</span>
                  </td>
                  <td className="px-2 py-2">
                    <span
                      className="text-[9px] px-1.5 py-0.5"
                      style={{ color: typeMeta?.color || '#78788a', border: `1px solid ${typeMeta?.color || '#78788a'}40` }}
                    >
                      {typeMeta?.label || rtype}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-ghost tabular-nums">
                    {regime?.startDate || '?'} - {regime?.endDate || '?'}
                  </td>
                  <td className="px-2 py-2 text-right font-mono" style={{ color: pnl >= 0 ? '#57c84d' : '#e84d4d' }}>
                    {hasError ? '-' : `$${pnl.toFixed(0)}`}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-terminal">
                    {hasError ? '-' : sharpe.toFixed(2)}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-terminal">
                    {hasError ? '-' : `${(maxDD * 100).toFixed(1)}%`}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-terminal">
                    {hasError ? '-' : trades}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-terminal">
                    {hasError ? '-' : `${(winRate * 100).toFixed(0)}%`}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="text-[10px] text-ghost/40 text-center">
        Click a regime row to view full backtest details
      </div>
    </div>
  )
}
