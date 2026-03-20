import { Routes, Route, NavLink } from 'react-router-dom'
import { useEffect, useState } from 'react'
import AsciiBackground from './components/AsciiBackground'
import Dashboard from './pages/Dashboard'
import BacktestLab from './pages/BacktestLab'
import DataExplorer from './pages/DataExplorer'
import Docs from './pages/Docs'
import MevDashboard from './pages/MevDashboard'

const navItems = [
  { to: '/', label: 'HOME', key: '1' },
  { to: '/backtest', label: 'LAB', key: '2' },
  { to: '/data', label: 'DATA', key: '3' },
  { to: '/docs', label: 'DOCS', key: '4' },
  { to: '/mev', label: 'MEV', key: '5' },
]

export default function App() {
  const [clock, setClock] = useState('')

  useEffect(() => {
    const tick = () => {
      const now = new Date()
      setClock(now.toLocaleTimeString('en-US', { hour12: false }) + ' UTC')
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="min-h-screen relative">
      <AsciiBackground />

      {/* top border accent line */}
      <div className="fixed top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-amber to-transparent opacity-40 z-50" />

      <nav className="sticky top-0 z-40 border-b border-border bg-void/90 backdrop-blur-sm">
        <div className="max-w-[1400px] mx-auto px-6 flex items-center h-12">
          {/* logo */}
          <div className="flex items-center gap-3 mr-8">
            <span className="text-amber font-bold text-sm tracking-[0.2em]">FLINT</span>
            <span className="text-ghost text-[10px] tracking-wider hidden md:inline">
              //SOLANA TRADING ENGINE
            </span>
          </div>

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
                [{n.key}] {n.label}
              </NavLink>
            ))}
          </div>

          {/* right side status */}
          <div className="ml-auto flex items-center gap-4 text-[10px] text-ghost tracking-wider">
            <span className="hidden sm:inline">{clock}</span>
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-phosphor animate-pulse" />
              LIVE
            </span>
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
        </Routes>
      </main>

      {/* footer */}
      <footer className="relative z-10 border-t border-border py-4 px-6">
        <div className="max-w-[1400px] mx-auto flex items-center justify-between text-[10px] text-ghost tracking-wider">
          <span>FLINT v0.1.0</span>
          <span className="font-[var(--font-display)] italic text-[11px] text-ghost/60">
            Strike alpha on Solana
          </span>
          <span>DRIFT / JUPITER / MEV</span>
        </div>
      </footer>
    </div>
  )
}
