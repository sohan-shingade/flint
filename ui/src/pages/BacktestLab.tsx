import { useState, useEffect, useCallback, useRef } from 'react'
import { useBacktest } from '../hooks/useBacktest'
import { useStrategies } from '../hooks/useStrategies'
import CodeEditor from '../components/CodeEditor'
import EquityCurve from '../components/EquityCurve'
import DrawdownChart from '../components/DrawdownChart'
import MetricsCard from '../components/MetricsCard'
import TradeTable from '../components/TradeTable'

/* ── strategy templates ────────────────────────────────── */

const BLANK_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List


class MyStrategy(Strategy):
    @property
    def name(self) -> str:
        return "my_strategy"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        # Your logic here
        return Signal.HOLD

    def reset(self) -> None:
        pass
`

const MA_CROSSOVER_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class MACrossover(Strategy):
    """Moving Average Crossover — trend following.

    Buys when fast SMA crosses above slow SMA (golden cross).
    Sells when fast crosses below slow (death cross).
    """
    def __init__(self, fast_period=10, slow_period=30):
        self.fast = fast_period
        self.slow = slow_period
        self._prev_fast = None
        self._prev_slow = None

    @property
    def name(self) -> str:
        return f"MA-Crossover({self.fast}/{self.slow})"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        if len(history) < self.slow:
            return Signal.HOLD
        closes = [c.close for c in history[-self.slow:]]
        fast_ma = float(np.mean(closes[-self.fast:]))
        slow_ma = float(np.mean(closes))
        signal = Signal.HOLD
        if self._prev_fast is not None:
            if self._prev_fast <= self._prev_slow and fast_ma > slow_ma:
                signal = Signal.BUY
            elif self._prev_fast >= self._prev_slow and fast_ma < slow_ma:
                signal = Signal.SELL
        self._prev_fast = fast_ma
        self._prev_slow = slow_ma
        return signal

    def reset(self) -> None:
        self._prev_fast = None
        self._prev_slow = None
`

const RSI_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List
import numpy as np


class RSIMeanReversion(Strategy):
    """RSI Mean Reversion — buy oversold, sell overbought."""
    def __init__(self, period=14, oversold=30, overbought=70):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    @property
    def name(self) -> str:
        return f"RSI({self.period})"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        if len(history) < self.period + 1:
            return Signal.HOLD
        closes = [c.close for c in history[-(self.period + 1):]]
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = float(np.mean(gains)) if len(gains) > 0 else 0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0
        if avg_loss == 0:
            rsi = 100.0
        else:
            rsi = 100 - (100 / (1 + avg_gain / avg_loss))
        if rsi < self.oversold:
            return Signal.BUY
        elif rsi > self.overbought:
            return Signal.SELL
        return Signal.HOLD

    def reset(self) -> None:
        pass
`

const FUNDING_HARVEST_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List


class FundingHarvest(Strategy):
    """Funding Rate Harvest — Solana-native strategy.

    Collects funding payments by trading against the crowd.
    Goes long when funding is deeply negative (longs get paid).
    Uses price deviation from mean as a proxy for funding direction.
    """
    def __init__(self, lookback=24, threshold_pct=2.0):
        self.lookback = lookback
        self.threshold = threshold_pct / 100

    @property
    def name(self) -> str:
        return f"FundingHarvest({self.lookback})"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        if len(history) < self.lookback:
            return Signal.HOLD
        closes = [c.close for c in history[-self.lookback:]]
        mean_price = sum(closes) / len(closes)
        deviation = (candle.close - mean_price) / mean_price
        if deviation < -self.threshold:
            return Signal.BUY
        elif deviation > self.threshold:
            return Signal.SELL
        return Signal.HOLD

    def reset(self) -> None:
        pass
`

const AMM_ARB_TEMPLATE = `from flint.strategy.base import Strategy
from flint.models import Candle, Signal
from typing import List


class AMMArbitrage(Strategy):
    """AMM Arbitrage — detect cross-venue mispricing.

    Monitors price divergence from moving average as a proxy
    for cross-venue arbitrage opportunities.
    """
    def __init__(self, window=12, divergence_bps=50):
        self.window = window
        self.divergence_bps = divergence_bps
        self._prices: list = []

    @property
    def name(self) -> str:
        return f"AMMArb({self.window}/{self.divergence_bps}bps)"

    def on_candle(self, candle: Candle, history: List[Candle]) -> Signal:
        self._prices.append(candle.close)
        if len(self._prices) < self.window:
            return Signal.HOLD
        recent = self._prices[-self.window:]
        mean = sum(recent) / len(recent)
        deviation_bps = abs(candle.close - mean) / mean * 10000
        if deviation_bps > self.divergence_bps:
            if candle.close < mean:
                return Signal.BUY
            else:
                return Signal.SELL
        return Signal.HOLD

    def reset(self) -> None:
        self._prices = []
`

const TEMPLATES: Record<string, { label: string; code: string }> = {
  blank: { label: 'Blank Strategy', code: BLANK_TEMPLATE },
  ma_crossover: { label: 'MA Crossover', code: MA_CROSSOVER_TEMPLATE },
  rsi: { label: 'RSI Mean Reversion', code: RSI_TEMPLATE },
  funding: { label: 'Funding Rate Harvest', code: FUNDING_HARVEST_TEMPLATE },
  amm_arb: { label: 'AMM Arbitrage', code: AMM_ARB_TEMPLATE },
}

/* ── component ─────────────────────────────────────────── */

export default function BacktestLab() {
  const { run, status, results, error } = useBacktest()
  const { strategies: savedStrategies, save, load, remove, validate, loading: savingStrategy } = useStrategies()

  // Editor state
  const [code, setCode] = useState(BLANK_TEMPLATE)
  const [currentName, setCurrentName] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)

  // Config state
  const [market, setMarket] = useState('SOL-PERP')
  const [startDate, setStartDate] = useState('2024-03-01')
  const [endDate, setEndDate] = useState('2024-03-31')
  const [capital, setCapital] = useState(10000)
  const [feeRate] = useState(0.0005)

  const codeRef = useRef(code)
  codeRef.current = code

  /* ── handlers ──────────────────────────────────────── */

  const handleCodeChange = useCallback((value: string) => {
    setCode(value)
    setDirty(true)
    setValidationError(null)
  }, [])

  const handleTemplateSelect = useCallback((key: string) => {
    const tpl = TEMPLATES[key]
    if (tpl) {
      setCode(tpl.code)
      setCurrentName(null)
      setDirty(false)
      setValidationError(null)
    }
  }, [])

  const handleLoadStrategy = useCallback(async (name: string) => {
    const loaded = await load(name)
    setCode(loaded)
    setCurrentName(name)
    setDirty(false)
    setValidationError(null)
  }, [load])

  const handleDeleteStrategy = useCallback(async (name: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (window.confirm(`Delete strategy "${name}"?`)) {
      await remove(name)
      if (currentName === name) {
        setCurrentName(null)
        setCode(BLANK_TEMPLATE)
        setDirty(false)
      }
    }
  }, [remove, currentName])

  const handleSave = useCallback(async () => {
    let name = currentName
    if (!name) {
      const input = window.prompt('Strategy name:')
      if (!input) return
      name = input.trim().replace(/\s+/g, '_').toLowerCase()
      if (!name) return
    }
    await save(name, codeRef.current)
    setCurrentName(name)
    setDirty(false)
  }, [currentName, save])

  const handleValidate = useCallback(async () => {
    const result = await validate(codeRef.current)
    if (result.valid) {
      setValidationError(null)
    } else {
      setValidationError(result.error || 'Validation failed')
    }
    return result.valid
  }, [validate])

  const handleRun = useCallback(async () => {
    setValidationError(null)
    const startTs = Math.floor(new Date(startDate + 'T00:00:00Z').getTime() / 1000)
    const endTs = Math.floor(new Date(endDate + 'T23:59:59Z').getTime() / 1000)
    run({
      strategy: 'custom',
      code: codeRef.current,
      market,
      resolution_s: 3600,
      start_ts: startTs,
      end_ts: endTs,
      initial_capital: capital,
      fee_rate: feeRate,
      params: {},
    })
  }, [run, market, startDate, endDate, capital, feeRate])

  /* ── keyboard shortcuts ────────────────────────────── */

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        handleSave()
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault()
        handleRun()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handleSave, handleRun])

  /* ── style helpers ─────────────────────────────────── */

  const inputClass = 'w-full bg-void border border-border text-terminal text-xs px-2.5 py-2 focus:border-amber/50 focus:outline-none transition-colors'
  const labelClass = 'block text-[10px] text-ghost tracking-[0.15em] mb-1.5'

  return (
    <div className="space-y-4">
      {/* ── header ─────────────────────────────────────── */}
      <div className="flex items-baseline gap-4 flex-wrap">
        <h1 className="font-[var(--font-display)] text-2xl text-white/90 italic">Strategy Lab</h1>
        <span className="text-[10px] text-ghost tracking-[0.2em]">
          // {currentName ? currentName.toUpperCase() : 'UNSAVED'}
          {dirty && <span className="text-amber ml-1">*</span>}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <select
            onChange={(e) => handleTemplateSelect(e.target.value)}
            value=""
            className="bg-void border border-border text-ghost text-[11px] px-2 py-1.5 focus:border-amber/50 focus:outline-none"
          >
            <option value="" disabled>Start from template...</option>
            {Object.entries(TEMPLATES).map(([key, tpl]) => (
              <option key={key} value={key}>{tpl.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* ── strategy tabs ──────────────────────────────── */}
      {savedStrategies.length > 0 && (
        <div className="flex items-center gap-0.5 overflow-x-auto pb-1">
          <button
            onClick={() => { setCurrentName(null); setCode(BLANK_TEMPLATE); setDirty(false) }}
            className={`px-3 py-1.5 text-[10px] tracking-[0.12em] border transition-all whitespace-nowrap ${
              currentName === null
                ? 'border-amber/50 bg-amber-glow text-amber'
                : 'border-border text-ghost hover:text-terminal hover:border-border-bright'
            }`}
          >
            + NEW
          </button>
          {savedStrategies.map((s) => (
            <button
              key={s.name}
              onClick={() => handleLoadStrategy(s.name)}
              className={`group px-3 py-1.5 text-[10px] tracking-[0.12em] border transition-all whitespace-nowrap flex items-center gap-2 ${
                currentName === s.name
                  ? 'border-amber/50 bg-amber-glow text-amber'
                  : 'border-border text-ghost hover:text-terminal hover:border-border-bright'
              }`}
            >
              {s.name}
              <span
                onClick={(e) => handleDeleteStrategy(s.name, e)}
                className="opacity-0 group-hover:opacity-60 hover:!opacity-100 text-loss cursor-pointer"
              >
                x
              </span>
            </button>
          ))}
        </div>
      )}

      {/* ── main split layout ──────────────────────────── */}
      <div className="flex gap-4" style={{ minHeight: '560px' }}>
        {/* LEFT — editor */}
        <div className="flex flex-col border border-border bg-surface/60 backdrop-blur" style={{ flex: '0 0 55%', minWidth: 0 }}>
          {/* editor toolbar */}
          <div className="px-3 py-2 border-b border-border flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 bg-amber/60" />
              <span className="text-[10px] text-ghost tracking-[0.2em]">EDITOR</span>
              <span className="text-[10px] text-ghost/40 ml-2">python</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleValidate}
                className="px-2.5 py-1 text-[10px] tracking-[0.1em] border border-border text-ghost hover:text-terminal hover:border-border-bright transition-all"
              >
                VALIDATE
              </button>
              <button
                onClick={handleSave}
                disabled={savingStrategy}
                className="px-2.5 py-1 text-[10px] tracking-[0.1em] border border-border text-ghost hover:text-amber hover:border-amber/30 transition-all disabled:opacity-40"
                title="Ctrl+S"
              >
                {savingStrategy ? 'SAVING...' : 'SAVE'}
              </button>
            </div>
          </div>

          {/* Monaco editor */}
          <div className="flex-1 min-h-0">
            <CodeEditor value={code} onChange={handleCodeChange} />
          </div>

          {/* validation errors */}
          {validationError && (
            <div className="px-3 py-2 border-t border-loss/30 bg-loss/5 text-loss text-[11px] font-mono">
              <span className="text-loss/60 mr-2">[ERR]</span>{validationError}
            </div>
          )}
        </div>

        {/* RIGHT — config + results */}
        <div className="flex-1 min-w-0 flex flex-col gap-4 overflow-y-auto" style={{ maxHeight: 'calc(100vh - 200px)' }}>
          {/* config panel */}
          <div className="border border-border bg-surface/60 backdrop-blur">
            <div className="px-3 py-2 border-b border-border flex items-center gap-2">
              <span className="w-2 h-2 bg-amber/60" />
              <span className="text-[10px] text-ghost tracking-[0.2em]">CONFIG.PARAMS</span>
            </div>
            <div className="p-3 grid grid-cols-2 gap-3">
              <div>
                <label className={labelClass}>MARKET</label>
                <select value={market} onChange={(e) => setMarket(e.target.value)} className={inputClass}>
                  <option>SOL-PERP</option>
                  <option>BTC-PERP</option>
                  <option>ETH-PERP</option>
                </select>
              </div>
              <div>
                <label className={labelClass}>CAPITAL</label>
                <input type="number" value={capital} onChange={(e) => setCapital(+e.target.value)} className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>START</label>
                <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>END</label>
                <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>FEE.RATE</label>
                <input type="text" value={feeRate} readOnly className={`${inputClass} text-ghost/60`} />
              </div>
            </div>
            <div className="px-3 pb-3">
              <button
                onClick={handleRun}
                disabled={status === 'running'}
                className="w-full px-4 py-2.5 bg-amber text-void text-xs font-semibold tracking-[0.15em] hover:bg-amber-dim disabled:bg-border disabled:text-ghost transition-all duration-200"
                style={{ animation: status === 'running' ? 'pulse-glow 2s ease infinite' : 'none' }}
                title="Ctrl+Enter"
              >
                {status === 'running' ? '> EXECUTING...' : '> RUN_BACKTEST'}
              </button>
              <div className="text-[9px] text-ghost/40 mt-1.5 text-center tracking-wider">
                Ctrl+Enter to run &middot; Ctrl+S to save
              </div>
            </div>
          </div>

          {/* error display */}
          {error && (
            <div className="border border-loss/30 bg-loss/5 px-3 py-2.5 text-loss text-xs">
              <span className="text-loss/60 mr-2">[ERR]</span>{error}
            </div>
          )}

          {/* results */}
          {results && (
            <div className="space-y-4" style={{ animation: 'fadeUp 0.5s ease' }}>
              <div className="flex items-baseline gap-3 border-b border-border pb-2">
                <span className="font-[var(--font-display)] text-base text-white/90 italic">Results</span>
                <span className="text-[10px] text-amber tracking-wider">{results.strategy_name}</span>
                <span className="text-[10px] text-ghost">{results.market}</span>
              </div>

              <MetricsCard metrics={results.metrics} />

              <div className="border border-border bg-surface/60 backdrop-blur p-3">
                <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">EQUITY.CURVE</div>
                <EquityCurve equity={results.equity_curve} buyHold={results.buy_hold_equity} height={200} />
              </div>

              <div className="border border-border bg-surface/60 backdrop-blur p-3">
                <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">DRAWDOWN</div>
                <DrawdownChart drawdown={results.drawdown_curve} height={140} />
              </div>

              {results.monthly_returns && Object.keys(results.monthly_returns).length > 0 && (
                <div className="border border-border bg-surface/60 backdrop-blur p-3">
                  <div className="text-[10px] text-ghost tracking-[0.2em] mb-2">MONTHLY.RETURNS</div>
                  <div className="grid grid-cols-13 gap-px text-[9px] text-center">
                    <div className="text-ghost py-1">YR</div>
                    {['J','F','M','A','M','J','J','A','S','O','N','D'].map(m =>
                      <div key={m} className="text-ghost/50 py-1">{m}</div>
                    )}
                    {Object.entries(results.monthly_returns as Record<string, Record<number, number>>).map(([year, months]) => (
                      <div key={year} className="contents">
                        <div className="text-ghost py-1">{year.slice(2)}</div>
                        {Array.from({ length: 12 }, (_, i) => {
                          const ret = months[i + 1]
                          return (
                            <div key={i} className={`py-1 ${
                              ret === undefined ? 'bg-border/30' :
                              ret >= 0 ? 'bg-gain/10 text-gain' : 'bg-loss/10 text-loss'
                            }`}>
                              {ret !== undefined ? `${ret > 0 ? '+' : ''}${ret}` : ''}
                            </div>
                          )
                        })}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="border border-border bg-surface/60 backdrop-blur">
                <div className="px-3 py-2 border-b border-border flex items-center justify-between">
                  <span className="text-[10px] text-ghost tracking-[0.2em]">TRADE.LOG</span>
                  <span className="text-[10px] text-amber/60">{results.trades.length} executions</span>
                </div>
                <div className="p-3">
                  <TradeTable trades={results.trades} />
                </div>
              </div>

              <div className="text-[10px] text-ghost/40 text-center tracking-wider pb-2">
                BUY_HOLD_BENCHMARK: {results.buy_hold_return_pct?.toFixed(2)}%
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
