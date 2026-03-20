import { useEffect, useState } from 'react'

interface StatusEntry {
  market: string
  data_type: string
  state: string
  last_updated: number | null
  row_count: number
  error_message: string | null
  progress_pct: number | null
}

export default function CollectorStatus() {
  const [entries, setEntries] = useState<StatusEntry[]>([])
  const [running, setRunning] = useState(false)

  useEffect(() => {
    const poll = () => {
      fetch('/api/v1/collector/status')
        .then(r => r.json())
        .then(d => {
          setEntries(d.status || [])
          setRunning(d.running || false)
        })
        .catch(() => {})
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [])

  const fmtTime = (ts: number | null) => {
    if (!ts) return '—'
    const ago = Math.floor((Date.now() / 1000) - ts)
    if (ago < 60) return `${ago}s ago`
    if (ago < 3600) return `${Math.floor(ago / 60)}m ago`
    return `${Math.floor(ago / 3600)}h ago`
  }

  const stateColor = (state: string) => {
    if (state === 'idle') return 'text-phosphor'
    if (state === 'collecting' || state === 'backfilling') return 'text-amber'
    if (state === 'error') return 'text-loss'
    return 'text-ghost'
  }

  if (entries.length === 0) {
    return (
      <div className="border border-border bg-surface/60 backdrop-blur p-4">
        <div className="flex items-center gap-2 mb-3">
          <span className="w-2 h-2 bg-amber/60" />
          <span className="text-[10px] text-ghost tracking-[0.2em]">DATA.COLLECTOR</span>
          <span className={`ml-auto text-[10px] ${running ? 'text-phosphor' : 'text-ghost/40'}`}>
            {running ? 'ACTIVE' : 'INACTIVE'}
          </span>
        </div>
        <div className="text-[11px] text-ghost/40">No collection data yet. Start the API server to begin automatic data collection.</div>
      </div>
    )
  }

  return (
    <div className="border border-border bg-surface/60 backdrop-blur">
      <div className="px-4 py-3 border-b border-border flex items-center gap-2">
        <span className="w-2 h-2 bg-amber/60" />
        <span className="text-[10px] text-ghost tracking-[0.2em]">DATA.COLLECTOR</span>
        <span className={`ml-auto text-[10px] flex items-center gap-1.5 ${running ? 'text-phosphor' : 'text-ghost/40'}`}>
          {running && <span className="w-1.5 h-1.5 rounded-full bg-phosphor animate-pulse" />}
          {running ? 'ACTIVE' : 'INACTIVE'}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-ghost/40 border-b border-border text-[10px] tracking-[0.15em]">
              <th className="py-2 px-4">MARKET</th>
              <th className="py-2 px-4">TYPE</th>
              <th className="py-2 px-4">STATUS</th>
              <th className="py-2 px-4 text-right">RECORDS</th>
              <th className="py-2 px-4">UPDATED</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e, i) => (
              <tr key={i} className="border-b border-border/30 hover:bg-amber-glow transition-colors">
                <td className="py-2 px-4 text-amber font-medium">{e.market}</td>
                <td className="py-2 px-4 text-ghost">{e.data_type}</td>
                <td className={`py-2 px-4 ${stateColor(e.state)}`}>
                  {e.state.toUpperCase()}
                  {e.progress_pct != null && e.state === 'backfilling' && (
                    <span className="ml-2 text-ghost/40">{e.progress_pct.toFixed(0)}%</span>
                  )}
                </td>
                <td className="py-2 px-4 text-right text-white/70 tabular-nums">{e.row_count.toLocaleString()}</td>
                <td className="py-2 px-4 text-ghost/40">{fmtTime(e.last_updated)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
