/**
 * RegimeSelector — checkbox UI for selecting market regimes.
 * Used by both BacktestLab and DataExplorer.
 */
import { REGIMES, REGIME_TYPES } from '../constants/regimes'

interface Props {
  selected: string[]
  onChange: (ids: string[]) => void
  compact?: boolean
}

export function RegimeSelector({ selected, onChange, compact }: Props) {
  const selectedSet = new Set(selected)

  const toggle = (id: string) => {
    const next = new Set(selectedSet)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    onChange(Array.from(next))
  }

  const selectAll = () => onChange(REGIMES.map(r => r.id))
  const clearAll = () => onChange([])
  const selectByType = (type: string) => {
    const ids = REGIMES.filter(r => r.regimeType === type).map(r => r.id)
    const allSelected = ids.every(id => selectedSet.has(id))
    if (allSelected) {
      onChange(selected.filter(id => !ids.includes(id)))
    } else {
      onChange([...new Set([...selected, ...ids])])
    }
  }

  // Group regimes by type for display
  const types = Object.keys(REGIME_TYPES)

  return (
    <div className={compact ? 'space-y-2' : 'space-y-3'}>
      {/* Quick actions */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={selectAll}
          className="text-[10px] px-2 py-1 border border-border text-ghost hover:text-terminal hover:border-amber/40 transition-colors"
        >
          ALL
        </button>
        <button
          onClick={clearAll}
          className="text-[10px] px-2 py-1 border border-border text-ghost hover:text-terminal hover:border-amber/40 transition-colors"
        >
          CLEAR
        </button>
        <span className="text-[9px] text-ghost/50 mx-1">|</span>
        {types.map(type => {
          const meta = REGIME_TYPES[type]
          const typeIds = REGIMES.filter(r => r.regimeType === type).map(r => r.id)
          const allOn = typeIds.every(id => selectedSet.has(id))
          return (
            <button
              key={type}
              onClick={() => selectByType(type)}
              className={`text-[10px] px-2 py-1 border transition-colors ${
                allOn
                  ? 'border-amber/50 bg-amber-glow text-amber'
                  : 'border-border text-ghost hover:text-terminal hover:border-amber/40'
              }`}
            >
              <span className="inline-block w-1.5 h-1.5 mr-1" style={{ backgroundColor: meta.color }} />
              {meta.label}
            </button>
          )
        })}
      </div>

      {/* Regime checkboxes */}
      <div className={`space-y-1 ${compact ? 'max-h-48 overflow-y-auto' : ''}`}>
        {REGIMES.map(regime => (
          <label
            key={regime.id}
            className={`flex items-center gap-2 px-2 py-1.5 cursor-pointer transition-colors ${
              selectedSet.has(regime.id)
                ? 'bg-surface/80 border border-amber/20'
                : 'border border-transparent hover:bg-surface/40'
            }`}
          >
            <input
              type="checkbox"
              checked={selectedSet.has(regime.id)}
              onChange={() => toggle(regime.id)}
              className="accent-amber"
            />
            <span
              className="inline-block w-2 h-2 flex-shrink-0"
              style={{ backgroundColor: regime.color }}
            />
            <span className="text-xs text-terminal flex-1 truncate">
              {regime.label}
            </span>
            <span className="text-[10px] text-ghost/60 tabular-nums flex-shrink-0">
              {regime.startDate} - {regime.endDate}
            </span>
            <span
              className="text-[9px] px-1.5 py-0.5 flex-shrink-0"
              style={{
                color: REGIME_TYPES[regime.regimeType]?.color || '#78788a',
                border: `1px solid ${REGIME_TYPES[regime.regimeType]?.color || '#78788a'}40`,
              }}
            >
              {REGIME_TYPES[regime.regimeType]?.label || regime.regimeType}
            </span>
          </label>
        ))}
      </div>

      {/* Selection count */}
      <div className="text-[10px] text-ghost/50">
        {selected.length} of {REGIMES.length} regimes selected
      </div>
    </div>
  )
}
