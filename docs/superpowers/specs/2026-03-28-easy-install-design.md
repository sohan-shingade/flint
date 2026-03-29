# Easy Install Design

**Date:** 2026-03-28
**Goal:** Make Flint installable via a single command for casual users, while keeping a clean dev path for power users.

## Overview

Four deliverables that work together:

1. **Browser setup wizard** — first-run experience that replaces `flint init`
2. **Docker polish** — one-liner `docker run` that drops users into the wizard
3. **Shell install script** — `curl | sh` that installs everything and opens the browser
4. **Makefile** — dev convenience aliases for clone-and-hack users

The wizard is the keystone: both Docker and the shell script just need to get the server running, then the wizard handles market selection and data download in the browser.

---

## 1. Browser Setup Wizard

### Detection

New API endpoint:

```
GET /api/v1/system/status
Response: { "initialized": bool, "version": "0.2.0" }
```

`initialized` is `true` when the `candles` table in DuckDB has >0 rows. The React app checks this on mount (in the root layout or App component) and redirects to `/setup` if `false`.

### Wizard Flow

| Step | Screen | Details |
|------|--------|---------|
| 1 | Welcome | "Welcome to Flint" + tagline, single "Get Started" button |
| 2 | Market selection | Checkboxes for markets from `GET /api/v1/data/available-markets`. Pre-checked: SOL-PERP, BTC-PERP, ETH-PERP. Backfill period selector: 30 / 60 / 90 / 180 days |
| 3 | API keys (optional) | Expandable/collapsible section for Birdeye + Helius keys. Prominent "Skip" button. Saves keys to server config |
| 4 | Download + progress | Calls `POST /api/v1/data/download` for selected markets. Per-market progress bars. Funding data downloads in parallel |
| 5 | Done | "Setup complete" message. CTA to run a sample backtest. Redirects to dashboard |

### UI Implementation

- New route `/setup` in React Router
- New page component `ui/src/pages/Setup.tsx` with step state machine
- Reuses existing API hooks where possible (`useDataDownload`, etc.)
- Minimal new API surface: only `GET /api/v1/system/status` is new
- Optional API keys step: `POST /api/v1/system/config` to save keys server-side (writes to `flint.yaml` or `.env`)

### API Changes

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/system/status` | GET | Returns `{initialized, version}` |
| `/api/v1/system/config` | POST | Saves optional config (API keys). Body: `{birdeye_api_key?, helius_api_key?}` |

The config endpoint reads the existing `.env` file (or creates it), updates only the specified keys, and writes back. Existing keys not in the request body are preserved unchanged.

---

## 2. Docker Setup

### Entrypoint Script

New file `docker-entrypoint.sh`:

```bash
#!/bin/sh
set -e

# Create directories if missing
mkdir -p /app/data /app/strategies/user

# Generate default config if missing
if [ ! -f /app/flint.yaml ]; then
    python -c "
from flint.config import FlintConfig
import yaml
cfg = FlintConfig()
data = {
    'db': {'path': '/app/data/flint.duckdb'},
    'trading': {
        'default_markets': list(cfg.default_markets),
        'default_fee_rate': cfg.default_fee_rate,
        'default_capital': cfg.default_capital,
    },
    'collector': {'enabled': True},
}
print(yaml.dump(data, default_flow_style=False))
" > /app/flint.yaml
fi

exec uvicorn flint.api.main:app --host 0.0.0.0 --port 8000
```

### Dockerfile Changes

- Replace `CMD` with `ENTRYPOINT ["./docker-entrypoint.sh"]`
- Add `HEALTHCHECK CMD curl -f http://localhost:8000/api/v1/health || exit 1`
- No other structural changes needed (multi-stage build is already correct)

### docker-compose.yml Changes

```yaml
services:
  flint:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - flint-data:/app/data
      - ./strategies:/app/strategies
    env_file:
      - .env
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped

volumes:
  flint-data:
```

Key changes:
- Named volume `flint-data` instead of bind mount `./data` (survives `docker compose down`)
- `env_file` for API keys
- Healthcheck
- Removed `flint.yaml` bind mount (entrypoint generates it)

### One-Liner

```bash
docker compose up
```

Or without compose:

```bash
docker run -p 8000:8000 -v flint-data:/app/data ghcr.io/sohan/flint
```

Pre-built GHCR images are out of scope for now (build locally).

---

## 3. Shell Install Script

### File

`install.sh` at project root, also fetchable via:

```bash
curl -fsSL https://raw.githubusercontent.com/<owner>/flint/main/install.sh | sh
```

### Behavior

```
Step 1/7  Detecting OS...           macOS arm64
Step 2/7  Checking Python 3.10+...  Found 3.12.1
Step 3/7  Checking Node 18+...      Installing via Homebrew...
Step 4/7  Cloning Flint...          ~/.flint
Step 5/7  Creating virtualenv...    ~/.flint/.venv
Step 6/7  Building UI...            npm install + npm run build
Step 7/7  Starting Flint...         http://localhost:8000
```

### Detailed Steps

1. **Detect OS/arch**: macOS (arm64/x86_64), Linux (Ubuntu/Debian via apt, Fedora via dnf, Arch via pacman). Exit with message on unsupported OS.

2. **Python 3.10+**: Check `python3 --version`. If missing or <3.10:
   - macOS: `brew install python@3.12` (install Homebrew first if missing)
   - Debian/Ubuntu: `sudo apt install python3 python3-venv python3-pip`
   - Fedora: `sudo dnf install python3`
   - Arch: `sudo pacman -S python`

3. **Node 18+**: Check `node --version`. If missing or <18:
   - macOS: `brew install node`
   - Linux: NodeSource setup script + package manager

4. **Clone repo**: `git clone <repo> ~/.flint` (or `$FLINT_HOME`). If dir exists, `git pull` instead.

5. **Create venv + install**:
   ```bash
   python3 -m venv ~/.flint/.venv
   source ~/.flint/.venv/bin/activate
   pip install -e ~/.flint
   ```

6. **Build UI**:
   ```bash
   cd ~/.flint/ui && npm install && npm run build
   ```

7. **Start server**:
   ```bash
   ~/.flint/.venv/bin/flint serve &
   open http://localhost:8000  # or xdg-open on Linux
   ```

### Flags

- `--no-start`: Install only, don't launch server
- `--dir <path>`: Install to custom directory instead of `~/.flint`

### Idempotency

Every step checks if work is already done before acting. Safe to re-run (e.g., after a failed network request).

### Uninstall

```bash
rm -rf ~/.flint
```

### Not in Scope

- Windows native (WSL2 uses the Linux path)
- Auto-updating (user runs `cd ~/.flint && git pull && pip install -e .`)

---

## 4. Makefile

New file `Makefile` at project root:

```makefile
.PHONY: install dev serve test build clean

install:
	pip install -e ".[dev]"
	cd ui && npm install

dev:
	flint serve --dev &
	cd ui && npm run dev

serve:
	cd ui && npm run build
	flint serve

test:
	pytest tests/ -v

build:
	docker build -t flint .

clean:
	rm -rf data/ ui/dist/ ui/node_modules/ .venv/ *.egg-info
```

---

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `ui/src/pages/Setup.tsx` | Create | Setup wizard page component |
| `ui/src/App.tsx` (or router) | Modify | Add `/setup` route, add init check + redirect |
| `flint/api/routes/system.py` | Create | `/api/v1/system/status` and `/api/v1/system/config` endpoints |
| `flint/api/main.py` | Modify | Register system router |
| `docker-entrypoint.sh` | Create | Smart entrypoint for container |
| `Dockerfile` | Modify | Use entrypoint script, add healthcheck |
| `docker-compose.yml` | Modify | Named volumes, env_file, healthcheck |
| `install.sh` | Create | Shell install script |
| `Makefile` | Create | Dev convenience targets |
