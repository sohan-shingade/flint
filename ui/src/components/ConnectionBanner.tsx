import { useEffect, useState } from 'react'

/**
 * Phase 4 T4.2 — surfaces API connectivity loss.
 *
 * Polls /api/v1/health every 10s. Shows a red banner after N consecutive
 * failures. Full polling-backoff + per-hook error routing lives in
 * sibling PR D-4.2-backoff.
 */

type Status = 'ok' | 'degraded' | 'offline' | 'unknown'

const POLL_INTERVAL_MS = 10_000
const DEGRADED_AFTER = 1   // first failure
const OFFLINE_AFTER = 3    // three consecutive failures

export default function ConnectionBanner() {
  const [status, setStatus] = useState<Status>('unknown')
  const [failures, setFailures] = useState(0)

  useEffect(() => {
    let cancelled = false

    async function check() {
      try {
        const r = await fetch('/api/v1/health', { cache: 'no-store' })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        if (cancelled) return
        setStatus('ok')
        setFailures(0)
      } catch {
        if (cancelled) return
        setFailures((f) => f + 1)
      }
    }

    check() // probe immediately
    const id = setInterval(check, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  useEffect(() => {
    if (failures >= OFFLINE_AFTER) setStatus('offline')
    else if (failures >= DEGRADED_AFTER) setStatus('degraded')
  }, [failures])

  if (status === 'ok' || status === 'unknown') return null

  const bg = status === 'offline' ? '#7f1d1d' : '#78350f'
  const msg =
    status === 'offline'
      ? `Cannot reach Flint API (${failures} failed probes). Is flint serve running?`
      : `Flint API probe failed — retrying...`

  return (
    <div
      role="alert"
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 100,
        padding: '8px 16px',
        background: bg,
        color: '#fef2f2',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12,
        textAlign: 'center',
        borderBottom: '1px solid #ef4444',
      }}
    >
      {msg}
      {' '}
      <button
        onClick={() => {
          setFailures(0)
          setStatus('unknown')
        }}
        style={{
          marginLeft: 12,
          padding: '2px 8px',
          background: 'transparent',
          color: '#fef2f2',
          border: '1px solid #fef2f2',
          cursor: 'pointer',
          fontSize: 11,
          textTransform: 'uppercase',
        }}
      >
        Retry now
      </button>
    </div>
  )
}
