// Local-security config for the API client (§12).
//
// The API is bound to 127.0.0.1 and every route requires a per-session bearer
// token generated when `flint serve` starts. Serve ships the pre-built UI in the
// wheel and injects the token into the served page, so the browser never has to
// prompt for it. We read it, in order, from:
//   1. window.__FLINT_TOKEN__ — a global `flint serve` writes into index.html.
//   2. <meta name="flint-token" content="..."> — the same, template-substituted.
//   3. import.meta.env.VITE_FLINT_TOKEN — dev convenience for `vite dev`.
// If none is present (bare dev with an unauthenticated server) the token is empty
// and requests go out without an Authorization header — the server then answers
// 401, which the screens render as a first-class error state rather than a blank.
//
// NEVER hardcode a token here. The value only ever comes from the running server.

const PLACEHOLDER = '__FLINT_TOKEN__'

export const API_BASE = '/api/v1'

export function getToken(): string {
  const injected = (window as unknown as { __FLINT_TOKEN__?: string }).__FLINT_TOKEN__
  if (injected && injected !== PLACEHOLDER) return injected

  const meta = document.querySelector('meta[name="flint-token"]')?.getAttribute('content')
  if (meta && meta !== PLACEHOLDER) return meta

  const env = (import.meta as unknown as { env?: Record<string, string> }).env
  if (env?.VITE_FLINT_TOKEN) return env.VITE_FLINT_TOKEN

  return ''
}
