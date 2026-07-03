// WebSocket lifecycle for the live monitor (screen 4, §6.7, §12). Connects to
// WS /paper/{id}/stream (token in the query string, per the client's wsUrl), parses
// each frame, and distinguishes a snapshot from a structured error payload — the
// server sends {error:{…}} then closes 1008 on a bad token/tenant, and a normal
// snapshot otherwise (it closes after the v1 single-snapshot push). The hook never
// throws; a bad frame or socket error becomes a first-class error state (§19.1).

import { useEffect, useState } from 'react'
import { wsUrl } from '../api/client'
import type { PaperSnapshot } from '../api/types'

export type WsStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error'

export interface LiveStreamState {
  snapshot: PaperSnapshot | null
  status: WsStatus
  error: string | null
}

export function useLiveStream(runId: string): LiveStreamState {
  const [snapshot, setSnapshot] = useState<PaperSnapshot | null>(null)
  const [status, setStatus] = useState<WsStatus>('idle')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!runId) {
      setStatus('idle')
      return
    }
    setStatus('connecting')
    setError(null)
    setSnapshot(null)

    const ws = new WebSocket(wsUrl(`/paper/${encodeURIComponent(runId)}/stream`))
    ws.onopen = () => setStatus('open')
    ws.onmessage = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data as string)
        if (data && data.error) {
          setError(data.error.message ?? 'stream error')
          setStatus('error')
        } else {
          setSnapshot(data as PaperSnapshot)
        }
      } catch {
        setError('malformed stream message')
        setStatus('error')
      }
    }
    ws.onerror = () => setStatus('error')
    ws.onclose = () => setStatus((s) => (s === 'error' ? s : 'closed'))

    return () => ws.close()
  }, [runId])

  return { snapshot, status, error }
}
