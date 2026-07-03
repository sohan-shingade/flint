// The only channel any screen uses to reach the backend (§4, §12). Screens call
// apiGet/apiPost — never fetch() directly — so bearer-token injection and the
// uniform error schema {error:{code,message,detail,hint}} are handled in one place.
// The UI never imports Python or reaches services directly; this file is the boundary.

import { API_BASE, getToken } from './config'

// A faulted response (§19.1): the server answered with an {error} envelope, or the
// transport failed. Screens catch this and render a first-class error state — the
// code/message/detail/hint are shown, never a blank screen or a raw console trace.
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail?: string
  readonly hint?: string

  constructor(status: number, code: string, message: string, detail?: string, hint?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
    this.hint = hint
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken()
  const headers = new Headers(init?.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)

  let res: Response
  try {
    res = await fetch(API_BASE + path, { ...init, headers })
  } catch (e) {
    throw new ApiError(0, 'network', 'cannot reach the Flint server', String(e),
      'is `flint serve` running on this host?')
  }

  const text = await res.text()
  let body: unknown = undefined
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      if (!res.ok) throw new ApiError(res.status, 'error', text.slice(0, 200))
    }
  }

  if (!res.ok) {
    const err = (body as { error?: Record<string, string> })?.error ?? {}
    throw new ApiError(
      res.status,
      err.code ?? 'error',
      err.message ?? res.statusText ?? 'request failed',
      err.detail,
      err.hint,
    )
  }
  return body as T
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path)
}

export function apiPost<T>(path: string, payload: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

// The WebSocket handshake cannot carry an Authorization header, so the token
// rides in the query string (the server accepts ?token= on WS, and guards
// origin/token BEFORE accept — a bad token closes with 1008, never a live socket).
export function wsUrl(path: string): string {
  const token = getToken()
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const q = token ? `?token=${encodeURIComponent(token)}` : ''
  return `${proto}//${window.location.host}${API_BASE}${path}${q}`
}

export function encodeQuery(params: Record<string, string | undefined>): string {
  const parts: string[] = []
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
  }
  return parts.length ? `?${parts.join('&')}` : ''
}

// Query string with repeated keys (e.g. ?market=A&market=B&venue=hyperliquid) —
// FastAPI reads these as list[str]. Used by the funding lab + run-compare routes.
export function encodeMulti(params: Record<string, string[]>): string {
  const parts: string[] = []
  for (const [k, values] of Object.entries(params)) {
    for (const v of values) if (v) parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
  }
  return parts.length ? `?${parts.join('&')}` : ''
}
