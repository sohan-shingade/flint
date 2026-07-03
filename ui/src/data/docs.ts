// v2 documentation content (§12). Terse, terminal-style reference rendered by the
// Docs screen through the recovered sidebar. Flint is Hyperliquid-native with a
// venue-agnostic core; nothing here references dropped venues or narratives. Keep
// entries factual and in sync with flint/strategy (Signal, ctx) and the services.

export type DocBlock =
  | { t: 'p'; text: string }
  | { t: 'h'; text: string }
  | { t: 'ul'; items: string[] }
  | { t: 'code'; lang: string; code: string }

export interface DocTopic {
  id: string
  title: string
  blocks: DocBlock[]
}

export interface DocSection {
  id: string
  title: string
  topics: DocTopic[]
}

export const docs: DocSection[] = [
  {
    id: 'getting-started',
    title: 'GETTING STARTED',
    topics: [
      {
        id: 'quickstart',
        title: 'Quickstart',
        blocks: [
          { t: 'p', text: 'Flint is a local-first backtesting, paper, and live lab for perp/DEX strategies. Everything runs on your machine against real recorded market data — no keys, no cloud.' },
          { t: 'h', text: 'Serve the lab' },
          { t: 'code', lang: 'bash', code: 'python3.12 -m venv .venv && source .venv/bin/activate\npip install -e ".[dev]"\nflint serve            # API + this UI, bound to 127.0.0.1' },
          { t: 'p', text: '`flint serve` mints a per-session bearer token and injects it into the served page, so the browser is authenticated without a prompt. If the banner says the token is stale, restart `flint serve`.' },
          { t: 'h', text: 'Your first backtest' },
          { t: 'ul', items: [
            'Open LAB (press 2).',
            'TEMPLATE mode: pick a template, adjust its params, set a universe (e.g. BTC-PERP,ETH-PERP,SOL-PERP) and a date range that your data covers.',
            'RUN — the result loads into a tearsheet: raw + deflated Sharpe, cost attribution, equity and drawdown curves.',
            'The run is saved to the Run Library (press 4); open it again from RESULTS (press 3) by run id.',
          ] },
          { t: 'p', text: 'If the window has no recorded funding, the run is rejected with the ranges that ARE covered — not silently filled. See "The funding gate".' },
        ],
      },
      {
        id: 'the-shell',
        title: 'Navigating',
        blocks: [
          { t: 'p', text: 'Press 1–8 to switch pages (skipped while typing in a field or the editor).' },
          { t: 'ul', items: [
            '1 HOME — status, recent runs.',
            '2 LAB — run a template or your own source.',
            '3 RESULTS — load any run by id (or ?run=<id>).',
            '4 RUNS — the Run Library; select two to compare.',
            '5 DATA — candle / funding / OI coverage per market × venue.',
            '6 FUNDING — annualized carry + cross-venue dislocation.',
            '7 LIVE — stream a paper/live session monitor.',
            '8 DOCS — this reference.',
          ] },
        ],
      },
    ],
  },
  {
    id: 'strategy-api',
    title: 'STRATEGY API',
    topics: [
      {
        id: 'strategy-class',
        title: 'Strategy.on_candle',
        blocks: [
          { t: 'p', text: 'A strategy subclasses Strategy and implements on_candle(self, candle, history, ctx). It returns a list[Signal] (empty or None = do nothing; a bare Signal is wrapped).' },
          { t: 'ul', items: [
            'candle — the just-closed bar (market, venue, open/high/low/close, volume, ts, resolution_s).',
            'history — that market’s closed bars, oldest→newest, with candle last.',
            'ctx — the read-only §8.2 window (see "ctx accessors").',
          ] },
          { t: 'code', lang: 'python', code: 'from flint.strategy import Strategy\nfrom flint.core.models import Signal\n\n\nclass MyStrategy(Strategy):\n    params = {"fast": 10, "slow": 30, "size_usd": 1000.0}\n\n    def on_candle(self, candle, history, ctx):\n        closes = [c.close for c in history]\n        if len(closes) < self.params["slow"]:\n            return []\n        fast = sum(closes[-self.params["fast"]:]) / self.params["fast"]\n        slow = sum(closes[-self.params["slow"]:]) / self.params["slow"]\n        pos = ctx.position(candle.market, candle.venue)\n        if fast > slow and pos is None:\n            return [Signal.long(candle.market, candle.venue, size_usd=self.params["size_usd"])]\n        if fast < slow and pos is not None:\n            return [Signal.close(candle.market, candle.venue)]\n        return []' },
          { t: 'p', text: 'params is a plain dict of name → default. It is the knob surface the LAB param editor and the optimizer read; overrides passed at run time replace individual values.' },
        ],
      },
      {
        id: 'signal',
        title: 'Signal',
        blocks: [
          { t: 'p', text: 'A Signal is a declared intent — the engine converts it to an order under §8.1 rules (sized at the execution bar’s open, tick/lot rounded, reduce-only on close).' },
          { t: 'ul', items: [
            'Signal.long(market, venue, size_usd=…) or size=… (base units) — exactly one.',
            'Signal.short(market, venue, size_usd=…) — same shape.',
            'Signal.close(market, venue) — reduce-only, closes the full current position; no size needed.',
            'Optional: limit_price (0 = market), stop_loss, take_profit, margin_mode, tif.',
          ] },
          { t: 'p', text: 'A signal with an empty venue routes to the run’s default venue. Signals are frozen once emitted.' },
        ],
      },
      {
        id: 'ctx',
        title: 'ctx accessors',
        blocks: [
          { t: 'p', text: 'ctx is the read-only window over everything knowable at bar start — never the future. Each accessor returns the last value before "now", or None when nothing is knowable yet (gaps are never forward-filled or synthesized).' },
          { t: 'ul', items: [
            'ctx.candles(market, lookback, venue=None) — the last N closed bars (current bar excluded).',
            'ctx.position(market, venue=None) — the open Position, or None.',
            'ctx.account(venue=None) — equity + cross-margin snapshot + sizing helpers.',
            'ctx.funding_rate(market, venue=None) — last PUBLISHED PREDICTED rate before now (never the settled rate).',
            'ctx.basis_bps(market, venue=None) — perp premium vs index from the last mark, or None.',
            'ctx.open_interest(market, venue=None) — last OI reading, or None.',
          ] },
          { t: 'p', text: 'funding_rate deliberately returns the predicted (not final) rate: leaking the settled rate inflates funding-arb backtests, and the predicted/final split blocks that leak where a look-ahead linter cannot see it.' },
        ],
      },
      {
        id: 'templates',
        title: 'Template catalog',
        blocks: [
          { t: 'p', text: 'The built-in registry the LAB can launch by name (GET /templates returns them live with their params). v1 ships ten:' },
          { t: 'ul', items: [
            'funding_harvest (funding) — hold the side that receives funding once the hourly rate clears a deadband.',
            'funding_dislocation (funding) — trade the HL leg when its funding dislocates from a benchmark rate.',
            'funding_svd (funding) — rank-1 SVD funding factor: fade each market’s residual funding dislocation.',
            'basis_trade (basis) — fade the perp premium: short a rich basis, long a cheap one.',
            'oi_momentum (flow) — trade with price when rising open interest confirms the move.',
            'ma_cross (technical) — moving-average cross trend follower (SMA or EMA).',
            'rsi_reversion (technical) — RSI mean reversion: long oversold, short overbought.',
            'bollinger (technical) — Bollinger-band mean reversion around a moving average.',
            'breakout (technical) — Donchian channel breakout of the prior high/low.',
            'lgbm_trend (ml) — LightGBM trend classifier on bounded momentum features.',
          ] },
        ],
      },
    ],
  },
  {
    id: 'running',
    title: 'RUNNING & VALIDATION',
    topics: [
      {
        id: 'user-source',
        title: 'User source & the sandbox',
        blocks: [
          { t: 'p', text: 'EDITOR mode submits your source to POST /backtests/source. It is validated, then walked by the real engine — all inside an OS-isolated subprocess. The AST import-allowlist is lint-grade UX; the OS sandbox is the security boundary.' },
          { t: 'p', text: 'A source run that fails comes back as a COMPLETED run with verdict="invalid" (never a stack trace, nothing persisted). The LAB renders the report first-class:' },
          { t: 'ul', items: [
            'screen violations — line-precise import/AST rejections from the static screen.',
            'sandbox error — a contained runtime failure (type + message).',
            'look-ahead lint — findings plus the honest list of what was and wasn’t checked (blind spots).',
          ] },
          { t: 'p', text: 'Fix the reported lines and re-run — the report is data you revise against, not an exception.' },
        ],
      },
      {
        id: 'funding-gate',
        title: 'The funding gate',
        blocks: [
          { t: 'p', text: 'Funding is a hard gate. A backtest over a window without real recorded funding is REJECTED — never silently filled, interpolated, or zero-defaulted.' },
          { t: 'p', text: 'A rejection is a completed run with verdict="rejected". It lists the legs whose funding is missing, the ranges that ARE available, and the fix (re-run over a covered range, or pull the data first from DATA).' },
          { t: 'p', text: 'This is why an honest funding-arb backtest cannot accidentally report carry that never existed.' },
        ],
      },
      {
        id: 'run-library',
        title: 'Run library & reproducibility',
        blocks: [
          { t: 'p', text: 'Every completed run is saved with its manifest: strategy, seed, engine version, effective range, and metrics. RUNS lists them; RESULTS loads any one by id.' },
          { t: 'ul', items: [
            'Compare — select two runs to diff their metrics side by side.',
            'Honesty warnings — the compare view flags when two runs were measured over DIFFERENT effective ranges; two Sharpes over different windows are not the same measurement.',
            'A user-source run’s manifest carries the actual source, so the run reproduces.',
          ] },
        ],
      },
    ],
  },
]
