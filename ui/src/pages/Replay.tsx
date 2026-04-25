import { useEffect, useMemo, useState } from 'react'

import { useReplaySummary, useReplayState, useReplayEvents } from '../hooks/useReplay'

const fmtUsd = (v: number | undefined | null) =>
  v != null
    ? `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : '-'

const fmtTs = (ts: number) =>
  new Date(ts * 1000).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    timeZone: 'UTC', hour12: false,
  }) + ' UTC'


function MetricTile({ label, value, accent }: {
  label: string
  value: string
  accent?: 'gain' | 'loss'
}) {
  const cls = accent === 'gain' ? 'text-gain'
    : accent === 'loss' ? 'text-loss'
    : 'text-white/80'
  return (
    <div className="bg-surface p-3 hover:bg-panel transition-colors">
      <div className="text-[9px] text-ghost/60 tracking-[0.2em] mb-1">{label}</div>
      <div className={`text-sm font-medium tabular-nums ${cls}`}>{value}</div>
    </div>
  )
}


export default function Replay() {
  const [sessionId, setSessionId] = useState('')
  const [activeId, setActiveId] = useState('')
  const [targetTs, setTargetTs] = useState<number | null>(null)
  const [initialCapital, setInitialCapital] = useState(10_000)

  const { data: summary, error: summaryError } = useReplaySummary(activeId)
  const { data: state, error: stateError, loading } = useReplayState(
    activeId, targetTs, initialCapital,
  )
  // Pull a window of events for the tail panel + timeline range.
  const { data: eventsPage } = useReplayEvents(activeId, null, 1000)
  const events = useMemo(() => eventsPage?.events ?? [], [eventsPage])
  const tsRange = useMemo(() => {
    if (events.length === 0) return null
    return { min: events[0].ts, max: events[events.length - 1].ts }
  }, [events])

  // When the user picks a session and the summary lands, default
  // target_ts to "now" so the page shows the latest state immediately.
  useEffect(() => {
    if (activeId && summary && targetTs == null) {
      setTargetTs(Math.floor(Date.now() / 1000))
    }
  }, [activeId, summary, targetTs])

  function handleLoad(e: React.FormEvent) {
    e.preventDefault()
    if (!sessionId.trim()) return
    setActiveId(sessionId.trim())
    setTargetTs(null)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-baseline gap-4">
        <h1 className="font-[var(--font-display)] text-2xl text-white/90 italic">Replay</h1>
        <span className="text-[10px] text-ghost tracking-[0.2em]">// TIME-TRAVEL DEBUG</span>
      </div>

      {/* session loader */}
      <form onSubmit={handleLoad} className="border border-border bg-surface/60 px-4 py-3 flex items-center gap-3">
        <span className="text-[10px] text-ghost/60 tracking-[0.2em]">SESSION.ID</span>
        <input
          type="text"
          value={sessionId}
          onChange={(e) => setSessionId(e.target.value)}
          placeholder="paste a backtest run_id or paper session_id"
          className="flex-1 bg-void/50 border border-border/50 px-3 py-1.5 text-[12px] font-mono text-terminal placeholder:text-ghost/30 focus:outline-none focus:border-terminal/60"
        />
        <button
          type="submit"
          className="px-4 py-1.5 text-[10px] tracking-[0.15em] border border-terminal/40 text-terminal hover:bg-terminal/10 transition-colors"
        >
          LOAD
        </button>
      </form>

      {summaryError && (
        <div className="border border-loss/30 bg-loss/5 px-3 py-2 text-loss text-[10px]">
          <span className="text-loss/60 mr-1">[REPLAY]</span>{summaryError}
        </div>
      )}

      {/* summary cards */}
      {summary && activeId && (
        <div className="grid grid-cols-3 gap-px bg-border">
          <MetricTile label="EVENT.COUNT" value={String(summary.event_count)} />
          <MetricTile label="LATEST.SEQ" value={summary.latest_seq != null ? String(summary.latest_seq) : '-'} />
          <MetricTile label="SNAPSHOTS" value={String(summary.snapshot_count)} />
        </div>
      )}

      {/* time scrubber + step controls + capital input */}
      {summary && summary.event_count > 0 && (
        <div className="border border-border bg-surface/60 px-4 py-3 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-ghost/60 tracking-[0.2em]">TARGET.TS</span>
            <span className="text-[12px] font-mono text-terminal">
              {targetTs != null ? `${targetTs} (${fmtTs(targetTs)})` : '-'}
            </span>
          </div>

          {/* Timeline slider gated on having an event range. */}
          {tsRange && (
            <div className="space-y-2">
              <input
                type="range"
                min={tsRange.min}
                max={tsRange.max}
                value={
                  targetTs != null
                    ? Math.min(Math.max(targetTs, tsRange.min), tsRange.max)
                    : tsRange.max
                }
                onChange={(e) => setTargetTs(parseInt(e.target.value, 10))}
                className="w-full accent-terminal"
              />
              <div className="flex items-center justify-between text-[9px] text-ghost/40 font-mono">
                <span>{fmtTs(tsRange.min)}</span>
                <span>{events.length} events in window</span>
                <span>{fmtTs(tsRange.max)}</span>
              </div>
            </div>
          )}

          {/* Step controls — jump to neighboring event timestamps. */}
          {events.length > 0 && (
            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  if (targetTs == null) return
                  const prev = [...events].reverse().find(e => e.ts < targetTs)
                  if (prev) setTargetTs(prev.ts)
                }}
                className="px-3 py-1 text-[10px] tracking-[0.15em] border border-border text-ghost/70 hover:bg-panel hover:text-white/80 transition-colors"
              >
                ← PREV EVENT
              </button>
              <button
                onClick={() => {
                  if (targetTs == null) return
                  const next = events.find(e => e.ts > targetTs)
                  if (next) setTargetTs(next.ts)
                }}
                className="px-3 py-1 text-[10px] tracking-[0.15em] border border-border text-ghost/70 hover:bg-panel hover:text-white/80 transition-colors"
              >
                NEXT EVENT →
              </button>
              <button
                onClick={() => tsRange && setTargetTs(tsRange.min)}
                className="px-3 py-1 text-[10px] tracking-[0.15em] border border-border text-ghost/70 hover:bg-panel hover:text-white/80 transition-colors"
              >
                ⏮ START
              </button>
              <button
                onClick={() => tsRange && setTargetTs(tsRange.max)}
                className="px-3 py-1 text-[10px] tracking-[0.15em] border border-border text-ghost/70 hover:bg-panel hover:text-white/80 transition-colors"
              >
                END ⏭
              </button>
              <span className="ml-auto text-[10px] text-ghost/40">capital</span>
              <input
                type="number"
                value={initialCapital}
                onChange={(e) => setInitialCapital(parseFloat(e.target.value) || 10_000)}
                className="w-28 bg-void/50 border border-border/50 px-3 py-1 text-[11px] font-mono text-terminal focus:outline-none focus:border-terminal/60"
              />
            </div>
          )}
        </div>
      )}

      {stateError && (
        <div className="border border-loss/30 bg-loss/5 px-3 py-2 text-loss text-[10px]">
          <span className="text-loss/60 mr-1">[STATE]</span>{stateError}
        </div>
      )}

      {/* replayed state cards */}
      {state && (
        <div className="space-y-3">
          <div className="grid grid-cols-3 md:grid-cols-6 gap-px bg-border">
            <MetricTile
              label="CASH"
              value={fmtUsd(state.cash)}
              accent={state.cash > state.initial_capital ? 'gain' : state.cash < state.initial_capital ? 'loss' : undefined}
            />
            <MetricTile
              label="REALIZED.PNL"
              value={(state.realized_pnl >= 0 ? '+' : '') + fmtUsd(state.realized_pnl)}
              accent={state.realized_pnl >= 0 ? 'gain' : 'loss'}
            />
            <MetricTile label="FILLS" value={String(state.fill_count)} />
            <MetricTile label="LIQUIDATIONS" value={String(state.liquidation_count)} />
            <MetricTile label="FEES" value={fmtUsd(state.total_fees)} />
            <MetricTile label="FUNDING" value={fmtUsd(state.total_funding)} />
          </div>

          {/* positions */}
          <div className="border border-border bg-surface/60">
            <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
              <span className="w-2 h-2 bg-terminal/60" />
              <span className="text-[10px] text-ghost tracking-[0.2em]">POSITIONS</span>
              <span className="ml-auto text-[10px] text-ghost/40 tabular-nums">
                {Object.keys(state.positions).length} open
              </span>
            </div>
            {Object.keys(state.positions).length === 0 ? (
              <div className="text-[10px] text-ghost/40 tracking-wider py-4 text-center">
                no open positions at target_ts
              </div>
            ) : (
              <table className="w-full text-[11px] font-mono">
                <thead>
                  <tr className="text-ghost/40 tracking-wider text-[9px]">
                    <th className="text-left px-3 py-2">VENUE / MARKET</th>
                    <th className="text-left px-3 py-2">SIDE</th>
                    <th className="text-right px-3 py-2">SIZE</th>
                    <th className="text-right px-3 py-2">ENTRY</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(state.positions).map(([key, pos]) => (
                    <tr key={key} className="border-t border-border/50">
                      <td className="px-3 py-1.5 text-terminal">{key}</td>
                      <td className={`px-3 py-1.5 ${pos.side === 'long' ? 'text-gain' : 'text-loss'}`}>
                        {pos.side.toUpperCase()}
                      </td>
                      <td className="px-3 py-1.5 text-right">{pos.size.toFixed(4)}</td>
                      <td className="px-3 py-1.5 text-right">{fmtUsd(pos.entry_price)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* Event tail — show events ≤ targetTs, newest first, capped */}
      {state && events.length > 0 && (() => {
        const folded = events
          .filter(e => targetTs == null || e.ts <= targetTs)
          .slice(-50)
          .reverse()
        return (
          <div className="border border-border bg-surface/60">
            <div className="px-4 py-2.5 border-b border-border flex items-center gap-2">
              <span className="w-2 h-2 bg-amber/60" />
              <span className="text-[10px] text-ghost tracking-[0.2em]">EVENT.TAIL</span>
              <span className="ml-auto text-[10px] text-ghost/40 tabular-nums">
                showing {folded.length} of {events.filter(e => targetTs == null || e.ts <= targetTs).length} folded
              </span>
            </div>
            {folded.length === 0 ? (
              <div className="text-[10px] text-ghost/40 tracking-wider py-4 text-center">
                no events at this target_ts
              </div>
            ) : (
              <div className="max-h-72 overflow-y-auto">
                <table className="w-full text-[11px] font-mono">
                  <thead className="bg-panel/40 sticky top-0">
                    <tr className="text-ghost/40 tracking-wider text-[9px]">
                      <th className="text-left px-3 py-2">SEQ</th>
                      <th className="text-left px-3 py-2">TS</th>
                      <th className="text-left px-3 py-2">KIND</th>
                      <th className="text-left px-3 py-2">PAYLOAD</th>
                    </tr>
                  </thead>
                  <tbody>
                    {folded.map(e => (
                      <tr key={e.seq} className="border-t border-border/50">
                        <td className="px-3 py-1 text-ghost/60">{e.seq}</td>
                        <td className="px-3 py-1 text-terminal">{fmtTs(e.ts)}</td>
                        <td className={`px-3 py-1 ${
                          e.kind === 'fill' ? 'text-gain'
                          : e.kind === 'liquidation' ? 'text-loss'
                          : e.kind.startsWith('order.') ? 'text-amber'
                          : 'text-ghost/70'
                        }`}>
                          {e.kind}
                        </td>
                        <td className="px-3 py-1 text-ghost/60 truncate max-w-[400px]">
                          {JSON.stringify(e.payload)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )
      })()}

      {loading && (
        <div className="text-[10px] text-ghost/40 tracking-widest text-center py-4 animate-pulse">
          REPLAYING...
        </div>
      )}

      {!activeId && (
        <div className="border border-border bg-surface/40 px-6 py-12 text-center space-y-2">
          <div className="text-[10px] text-ghost tracking-[0.2em]">PASTE A SESSION ID TO BEGIN</div>
          <div className="text-[10px] text-ghost/40">
            Backtest run_ids are visible on the LAB page; paper session_ids on PAPER.
          </div>
        </div>
      )}
    </div>
  )
}
