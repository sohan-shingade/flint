// The granularity-unavailable rejection (§B7, §19.1 row 2): an *explicit* tier
// (ticks/book) whose data has a gap. Like the funding gap it is DATA, not an
// error — so it renders as an options card, not a toast: the per-leg per-kind
// coverage, then the machine-readable ways out as action affordances (run at
// bars / clip to the covered window / backfill via Tardis / record forward).
// Run-at-bars and clip resubmit through optional callbacks the launcher (Lab)
// wires; backfill fires the pull_data job; record-forward is a display-only CLI
// hint. In a view-only surface (a loaded run in RESULTS) the callbacks are
// absent and the actionable ones read as disabled affordances.

import type { Range, RejectedPayload, RejectionOption } from '../api/types'
import { fmtRange } from '../lib/format'

export interface GranularityActions {
  onRunAtBars?: (granularity: string) => void
  onClip?: (range: Range) => void
  onBackfill?: (window: Range | null) => void
}

const btn =
  'rounded border border-amber/50 bg-amber/10 px-3 py-1.5 font-mono text-xs uppercase tracking-widest text-amber transition-colors hover:bg-amber/20 disabled:cursor-not-allowed disabled:opacity-40'

function RunBars({ opt, actions }: { opt: RejectionOption; actions?: GranularityActions }) {
  const g = opt.granularity ?? 'candles'
  return (
    <button
      type="button"
      className={btn}
      disabled={!actions?.onRunAtBars}
      onClick={() => actions?.onRunAtBars?.(g)}
    >
      run at bars ({g})
    </button>
  )
}

function Clip({ opt, actions }: { opt: RejectionOption; actions?: GranularityActions }) {
  const r = opt.effective_range
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        className={btn}
        disabled={!actions?.onClip || !r}
        onClick={() => r && actions?.onClip?.(r)}
      >
        clip to coverage
      </button>
      {r && <span className="font-mono text-xs text-ghost">{fmtRange(r)}</span>}
    </div>
  )
}

// The flattened vendor availability windows, if the advert carried any.
function backfillWindows(opt: RejectionOption): Range[] {
  const out: Range[] = []
  const avail = opt.available
  if (!avail) return out
  for (const perKind of Object.values(avail)) {
    for (const win of Object.values(perKind)) if (win) out.push(win)
  }
  return out
}

function Backfill({ opt, actions }: { opt: RejectionOption; actions?: GranularityActions }) {
  const windows = backfillWindows(opt)
  const win = windows[0] ?? null
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <button
          type="button"
          className={btn}
          disabled={!actions?.onBackfill}
          onClick={() => actions?.onBackfill?.(win)}
        >
          backfill via {opt.vendor ?? 'tardis'}
        </button>
        {opt.requires_secret && (
          <span className="font-mono text-xs text-ghost">needs {opt.requires_secret}</span>
        )}
      </div>
      {win ? (
        <span className="font-mono text-xs text-ghost">available {fmtRange(win)}</span>
      ) : (
        <span className="font-mono text-xs text-ghost/60">
          set {opt.requires_secret ?? 'the vendor key'} to see the available window
        </span>
      )}
    </div>
  )
}

function RecordForward({ opt }: { opt: RejectionOption }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-xs uppercase tracking-widest text-ghost">record forward</span>
      {opt.hint && (
        <code className="block overflow-x-auto rounded border border-border bg-void px-2 py-1 font-mono text-xs text-terminal">
          {opt.hint}
        </code>
      )}
    </div>
  )
}

function Option({ opt, actions }: { opt: RejectionOption; actions?: GranularityActions }) {
  switch (opt.action) {
    case 'run_bars':
      return <RunBars opt={opt} actions={actions} />
    case 'clip_to_coverage':
      return <Clip opt={opt} actions={actions} />
    case 'vendor_backfill':
      return <Backfill opt={opt} actions={actions} />
    case 'record_forward':
      return <RecordForward opt={opt} />
    default:
      return null
  }
}

export function GranularityOptionsCard({
  rejected,
  actions,
}: {
  rejected: RejectedPayload
  actions?: GranularityActions
}) {
  const options = rejected.options ?? []
  const coverage = rejected.coverage ?? {}

  return (
    <div className="m-4 rounded border border-amber/50 bg-amber-glow p-4 font-mono text-sm" role="alert">
      <div className="mb-2 flex items-center gap-2">
        <span className="rounded bg-amber/20 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-amber">
          rejected · {rejected.code}
        </span>
        {rejected.granularity && (
          <span className="text-xs text-ghost">requested tier: {rejected.granularity}</span>
        )}
      </div>
      <div className="mb-3 text-terminal">{rejected.message}</div>

      {Object.keys(coverage).length > 0 && (
        <div className="mb-4">
          <div className="mb-1 text-xs uppercase tracking-wide text-ghost">coverage</div>
          <table className="w-full text-left">
            <tbody>
              {Object.entries(coverage).map(([leg, perKind]) =>
                Object.entries(perKind).map(([kind, pieces]) => (
                  <tr key={`${leg}-${kind}`}>
                    <td className="pr-4 text-ghost">{leg}</td>
                    <td className="pr-4 text-ghost">{kind}</td>
                    <td className="text-terminal">
                      {pieces.length > 0 ? pieces.map((p) => fmtRange(p)).join(', ') : 'none'}
                    </td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      )}

      <div className="mb-1 text-xs uppercase tracking-wide text-ghost">ways out</div>
      <div className="flex flex-col gap-3">
        {options.map((opt, i) => (
          <Option key={`${opt.action}-${i}`} opt={opt} actions={actions} />
        ))}
      </div>
    </div>
  )
}
