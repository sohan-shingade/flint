// The Flint web UI shell (§12). Eight screens, each reading ONLY the 7.1 REST/WS
// API through src/api — the UI never imports Python or reaches services directly.
// The shell owns the chrome: sticky numbered nav (press 1–8 to switch), a UTC clock,
// the ASCII backdrop, the connection banner, and a footer whose version comes from
// GET /system/health.

import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import AsciiBackground from './components/AsciiBackground'
import ConnectionBanner from './components/ConnectionBanner'
import { apiGet } from './api/client'
import type { SystemHealth } from './api/types'
import Dashboard from './screens/Dashboard'
import Lab from './screens/Lab'
import Tearsheet from './screens/Tearsheet'
import RunLibrary from './screens/RunLibrary'
import DataExplorer from './screens/DataExplorer'
import FundingHeatmap from './screens/FundingHeatmap'
import LiveMonitor from './screens/LiveMonitor'
import Docs from './screens/Docs'

const NAV = [
  { to: '/', label: 'HOME', key: '1' },
  { to: '/lab', label: 'LAB', key: '2' },
  { to: '/results', label: 'RESULTS', key: '3' },
  { to: '/runs', label: 'RUNS', key: '4' },
  { to: '/data', label: 'DATA', key: '5' },
  { to: '/funding', label: 'FUNDING', key: '6' },
  { to: '/live', label: 'LIVE', key: '7' },
  { to: '/docs', label: 'DOCS', key: '8' },
]

export default function App() {
  const [clock, setClock] = useState('')
  const [version, setVersion] = useState('?.?.?')
  const navigate = useNavigate()

  // Footer version — cosmetic; the ConnectionBanner owns connectivity signalling.
  useEffect(() => {
    apiGet<SystemHealth>('/system/health')
      .then((h) => setVersion(h.version))
      .catch(() => {})
  }, [])

  useEffect(() => {
    const tick = () =>
      setClock(new Date().toLocaleTimeString('en-US', { hour12: false, timeZone: 'UTC' }) + ' UTC')
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  // Numbered hotkeys 1–8 — skipped while typing in a field or the Monaco editor.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement)
        return
      if (t?.closest?.('.monaco-editor')) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const item = NAV.find((n) => n.key === e.key)
      if (item) navigate(item.to)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [navigate])

  return (
    <div className="relative min-h-screen">
      <ConnectionBanner />
      <AsciiBackground />

      {/* top border accent line */}
      <div className="fixed left-0 right-0 top-0 z-50 h-px bg-gradient-to-r from-transparent via-amber to-transparent opacity-40" />

      <nav className="sticky top-0 z-40 border-b border-border bg-void/90 backdrop-blur-sm">
        <div className="mx-auto flex h-11 max-w-[1400px] items-center px-6">
          <NavLink to="/" className="mr-8 flex items-center gap-3">
            <span className="text-sm font-bold tracking-[0.2em] text-amber">FLINT</span>
          </NavLink>

          <div className="flex items-center gap-0.5">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === '/'}
                className={({ isActive }) =>
                  `border border-transparent px-3 py-1 text-[11px] tracking-[0.15em] transition-all duration-200 ${
                    isActive ? 'border-border-bright bg-amber-glow text-amber' : 'text-ghost hover:text-terminal'
                  }`
                }
              >
                <span className="mr-1 text-ghost/50">{n.key}</span>
                {n.label}
              </NavLink>
            ))}
          </div>

          <div className="ml-auto flex items-center gap-4 text-[10px] tracking-wider text-ghost">
            <span className="hidden font-mono tabular-nums sm:inline">{clock}</span>
          </div>
        </div>
      </nav>

      <main className="relative z-10 mx-auto max-w-[1400px] px-6 py-8">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/lab" element={<Lab />} />
          <Route path="/results" element={<Tearsheet />} />
          <Route path="/runs" element={<RunLibrary />} />
          <Route path="/data" element={<DataExplorer />} />
          <Route path="/funding" element={<FundingHeatmap />} />
          <Route path="/live" element={<LiveMonitor />} />
          <Route path="/docs" element={<Docs />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      <footer className="relative z-10 border-t border-border px-6 py-3">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between text-[10px] tracking-wider text-ghost/70">
          <span>FLINT v{version}</span>
          <span className="font-display text-[11px] italic text-ghost/60">Strike alpha on perps</span>
          <span>HYPERLIQUID · DUCKDB</span>
        </div>
      </footer>
    </div>
  )
}
