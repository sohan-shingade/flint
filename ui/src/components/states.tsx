// First-class UI states (§19.1). Data scarcity and faults are outcomes the UI
// renders deliberately — a funding-gap rejection shows the missing ranges and the
// fix, a fault shows code/message/detail/hint — never a blank screen or a console
// error. These small components are the shared vocabulary for those states.

import type { ApiError } from '../api/client'
import type { RejectedPayload } from '../api/types'
import { fmtRange } from '../lib/format'

export function Loading({ label = 'loading…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-6 text-ghost font-mono text-sm" role="status">
      <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber" />
      {label}
    </div>
  )
}

export function Empty({ message }: { message: string }) {
  return (
    <div className="p-6 text-ghost font-mono text-sm" role="note">
      {message}
    </div>
  )
}

// A transport/server fault (§19.1 rows 1/3). Shows the uniform error envelope so
// the operator sees exactly what the server said and how to fix it.
export function ErrorState({ error }: { error: ApiError | Error }) {
  const api = error as ApiError
  return (
    <div className="m-4 rounded border border-loss/50 bg-loss/5 p-4 font-mono text-sm" role="alert">
      <div className="mb-1 font-semibold text-loss">
        {api.code ? `${api.code}` : 'error'}
        {api.status ? <span className="text-ghost"> · HTTP {api.status}</span> : null}
      </div>
      <div className="text-terminal">{error.message}</div>
      {api.detail ? <div className="mt-1 text-ghost">{api.detail}</div> : null}
      {api.hint ? <div className="mt-2 text-amber">↳ {api.hint}</div> : null}
    </div>
  )
}

// The funding hard-gate rejection (§6.4, §19.1 row 2): a *completed* run whose
// verdict is "rejected". It is data, not an error — so it renders as a proper
// panel listing the legs whose funding is missing, the ranges actually available,
// and the fix, exactly as the design promises.
export function RejectedState({ rejected }: { rejected: RejectedPayload }) {
  return (
    <div className="m-4 rounded border border-amber/50 bg-amber-glow p-4 font-mono text-sm" role="alert">
      <div className="mb-2 flex items-center gap-2">
        <span className="rounded bg-amber/20 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-amber">
          rejected · {rejected.code}
        </span>
      </div>
      <div className="mb-3 text-terminal">{rejected.message}</div>

      {rejected.missing.length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-xs uppercase tracking-wide text-ghost">missing funding</div>
          <ul className="space-y-0.5">
            {rejected.missing.map((leg) => (
              <li key={leg} className="text-loss">
                · {leg}
              </li>
            ))}
          </ul>
        </div>
      )}

      {Object.keys(rejected.available).length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-xs uppercase tracking-wide text-ghost">available ranges</div>
          <table className="w-full text-left">
            <tbody>
              {Object.entries(rejected.available).map(([leg, range]) => (
                <tr key={leg}>
                  <td className="pr-4 text-ghost">{leg}</td>
                  <td className="text-terminal">{range ? fmtRange(range) : 'none'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rejected.hint && <div className="text-amber">↳ {rejected.hint}</div>}
    </div>
  )
}
