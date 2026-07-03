# Flint web UI

Five screens over the Flint REST/WS API (§12). React 19 + Vite + TypeScript.

The UI is a **pure API client** — it never imports Python and never reaches
`services/` directly. Everything goes through `src/api/` (the only place `fetch`
is called), which injects the per-session bearer token and normalizes the uniform
error schema `{error:{code,message,detail,hint}}`. Data scarcity and faults render
as first-class states (a funding-gap rejection shows the missing ranges and the
fix), never blank screens or console errors (§19.1).

## Screens (`src/screens/`)

| Route | Screen | Reads |
|---|---|---|
| `/results` | Results / tearsheet | `GET /backtests/{id}` |
| `/funding` | Funding coverage heatmap (markets × venues) | `GET /data/coverage` |
| `/data` | Data explorer (coverage matrix) | `GET /data/coverage` |
| `/live` | Live monitor | `WS /paper/{id}/stream` — *7.4b* |
| `/runs` | Run library + two-run diff | `GET /runs`, `GET /runs/compare` — *7.4b* |

## Auth

Every route needs the per-session bearer token that `flint serve` generates on
start. Serve injects it into the served page; the client reads it (in order) from
`window.__FLINT_TOKEN__`, `<meta name="flint-token">`, or `VITE_FLINT_TOKEN` for
`vite dev`. Never hardcode a token — it only ever comes from the running server.

## Develop

```bash
npm install
npm run dev      # vite dev server on :5173, proxies /api + /ws to localhost:8000
npm test         # vitest — fully mocked (MSW), no live API needed
```

## Build (ships pre-built in the wheel)

```bash
npm run build    # tsc -b && vite build → dist/  (gitignored; packaged into the wheel)
```

`dist/` and `node_modules/` are never committed — the wheel packaging step runs
`npm run build` and bundles the `dist/` output.
