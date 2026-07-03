// The Flint web UI shell (§12). Five screens, each reading ONLY the 7.1 REST/WS
// API through src/api — the UI never imports Python or reaches services directly.
//
// Screens 1–3 (results, funding heatmap, data explorer) land in slice 7.4a.
// Screens 4–5 (live monitor, run library) + deletion of the legacy pre-greenfield
// pages land in 7.4b — they are placeholders here so the shell is whole and the
// nav is stable for the successor to fill in.

import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Tearsheet from './screens/Tearsheet'
import FundingHeatmap from './screens/FundingHeatmap'
import DataExplorer from './screens/DataExplorer'

const NAV = [
  { to: '/results', label: 'RESULTS' },
  { to: '/funding', label: 'FUNDING' },
  { to: '/data', label: 'DATA' },
  { to: '/live', label: 'LIVE' },
  { to: '/runs', label: 'RUNS' },
]

function ComingSoon({ name, slice }: { name: string; slice: string }) {
  return (
    <div className="p-8 font-mono text-sm text-ghost">
      <div className="mb-1 text-terminal">{name}</div>
      <div>landing in slice {slice}.</div>
    </div>
  )
}

export default function App() {
  return (
    <div className="min-h-screen bg-void text-terminal">
      <header className="flex items-center gap-6 border-b border-border px-4 py-3">
        <span className="font-display text-lg text-amber">flint</span>
        <nav className="flex gap-1">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `rounded px-3 py-1.5 font-mono text-xs tracking-wide transition-colors ${
                  isActive ? 'bg-amber/15 text-amber' : 'text-ghost hover:text-terminal'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/results" replace />} />
          <Route path="/results" element={<Tearsheet />} />
          <Route path="/funding" element={<FundingHeatmap />} />
          <Route path="/data" element={<DataExplorer />} />
          <Route path="/live" element={<ComingSoon name="Live monitor" slice="7.4b" />} />
          <Route path="/runs" element={<ComingSoon name="Run library" slice="7.4b" />} />
          <Route path="*" element={<Navigate to="/results" replace />} />
        </Routes>
      </main>
    </div>
  )
}
