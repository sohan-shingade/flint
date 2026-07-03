// Surfaces API connectivity loss for the shell (§12, §19.1). Polls
// GET /api/v1/system/health through the api client so the same bearer-token +
// error envelope path every screen uses decides the banner. Three states:
//   · unreachable  — the transport failed (ApiError code "network"): server down.
//   · unauthorized — HTTP 401: the per-session token is missing or stale.
//   · ok           — hidden. A healthy server shows no banner.

import { useEffect, useState } from 'react'
import { apiGet, ApiError } from '../api/client'
import type { SystemHealth } from '../api/types'

type Status =
  | { kind: 'ok' }
  | { kind: 'unknown' }
  | { kind: 'unreachable'; failures: number }
  | { kind: 'unauthorized' }

const POLL_INTERVAL_MS = 10_000

export default function ConnectionBanner() {
  const [status, setStatus] = useState<Status>({ kind: 'unknown' })

  useEffect(() => {
    let cancelled = false
    let failures = 0

    async function check() {
      try {
        await apiGet<SystemHealth>('/system/health')
        if (cancelled) return
        failures = 0
        setStatus({ kind: 'ok' })
      } catch (e) {
        if (cancelled) return
        const api = e as ApiError
        if (api.status === 401) {
          setStatus({ kind: 'unauthorized' })
        } else {
          failures += 1
          setStatus({ kind: 'unreachable', failures })
        }
      }
    }

    check()
    const id = setInterval(check, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  if (status.kind === 'ok' || status.kind === 'unknown') return null

  const msg =
    status.kind === 'unauthorized'
      ? 'session token missing or stale — restart `flint serve`'
      : `cannot reach the Flint API (${status.failures} failed ${status.failures === 1 ? 'probe' : 'probes'}) — is \`flint serve\` running?`

  return (
    <div
      role="alert"
      className="fixed left-0 right-0 top-0 z-[100] border-b border-loss/60 bg-loss/90 px-4 py-2 text-center font-mono text-xs text-void"
    >
      {msg}
    </div>
  )
}
