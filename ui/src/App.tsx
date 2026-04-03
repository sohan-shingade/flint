import { Routes, Route, NavLink, useNavigate, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'
import AsciiBackground from './components/AsciiBackground'
import Dashboard from './pages/Dashboard'
import BacktestLab from './pages/BacktestLab'
import DataExplorer from './pages/DataExplorer'
import Docs from './pages/Docs'
import MevDashboard from './pages/MevDashboard'
import PaperTrading from './pages/PaperTrading'
import Setup from './pages/Setup'
import LiveMonitor from './pages/LiveMonitor'
import FillAnalysis from './pages/FillAnalysis'
import FundingHeatmap from './pages/FundingHeatmap'

const navItems = [
  { to: '/', label: 'HOME', key: '1' },
  { to: '/backtest', label: 'LAB', key: '2' },
  { to: '/data', label: 'DATA', key: '3' },
  { to: '/docs', label: 'DOCS', key: '4' },
  { to: '/mev', label: 'MEV', key: '5' },
  { to: '/paper', label: 'PAPER', key: '6' },
  { to: '/live', label: 'LIVE', key: '7' },
  { to: '/fills', label: 'FILLS', key: '8' },
  { to: '/funding', label: 'FUNDING', key: '9' },
]

export default function App() {
  const [clock, setClock] = useState('')
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    const tick = () => {
      const now = new Date()
      setClock(now.toLocaleTimeString('en-US', { hour12: false, timeZone: 'UTC' }) + ' UTC')
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  // Global keyboard navigation — press 1-5 to switch pages
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return
      // Don't trigger in Monaco editor
      if ((e.target as HTMLElement)?.closest?.('.monaco-editor')) return
      const item = navItems.find(n => n.key === e.key)
      if (item) navigate(item.to)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [navigate])

  useEffect(() => {
    fetch('/api/v1/system/status')
      .then(r => r.json())
      .then(data => {
        if (!data.initialized && location.pathname !== '/setup') {
          navigate('/setup', { replace: true })
        }
      })
      .catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="min-h-screen relative">
      <AsciiBackground />

      {/* top border accent line */}
      <div className="fixed top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-amber to-transparent opacity-40 z-50" />

      <nav className="sticky top-0 z-40 border-b border-border bg-void/90 backdrop-blur-sm">
        <div className="max-w-[1400px] mx-auto px-6 flex items-center h-11">
          {/* logo */}
          <NavLink to="/" className="flex items-center gap-3 mr-8">
            <span className="text-amber font-bold text-sm tracking-[0.2em]">FLINT</span>
          </NavLink>

          {/* nav links */}
          <div className="flex items-center gap-0.5">
            {navItems.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === '/'}
                className={({ isActive }) =>
                  `px-3 py-1 text-[11px] tracking-[0.15em] transition-all duration-200 border border-transparent ${
                    isActive
                      ? 'text-amber border-border-bright bg-amber-glow'
                      : 'text-ghost hover:text-terminal'
                  }`
                }
              >
                <span className="text-ghost/50 mr-1">{n.key}</span>{n.label}
              </NavLink>
            ))}
          </div>

          {/* right side */}
          <div className="ml-auto flex items-center gap-4 text-[10px] text-ghost tracking-wider">
            <span className="hidden sm:inline font-mono tabular-nums">{clock}</span>
          </div>
        </div>
      </nav>

      <main className="relative z-10 max-w-[1400px] mx-auto px-6 py-8">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/backtest" element={<BacktestLab />} />
          <Route path="/data" element={<DataExplorer />} />
          <Route path="/docs" element={<Docs />} />
          <Route path="/mev" element={<MevDashboard />} />
          <Route path="/paper" element={<PaperTrading />} />
          <Route path="/live" element={<LiveMonitor />} />
          <Route path="/fills" element={<FillAnalysis />} />
          <Route path="/funding" element={<FundingHeatmap />} />
          <Route path="/setup" element={<Setup />} />
        </Routes>
      </main>

      {/* footer */}
      <footer className="relative z-10 border-t border-border py-3 px-6">
        <div className="max-w-[1400px] mx-auto flex items-center justify-between text-[10px] text-ghost/70 tracking-wider">
          <span>FLINT v0.3.0</span>
          <span className="font-[var(--font-display)] italic text-[11px] text-ghost/60">
            Strike alpha on Solana
          </span>
          <span>DRIFT &middot; JUPITER &middot; DUCKDB</span>
        </div>
      </footer>
    </div>
  )
}
