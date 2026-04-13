"""Deploy The Midnight Gardener to paper trading.

Usage:
  1. Start the server: flint serve
  2. Run this script: python scripts/deploy_midnight_gardener.py

Or use the API directly:
  curl -X POST http://localhost:8000/api/v1/paper/start \
    -H 'Content-Type: application/json' \
    -d @- << 'EOF'
    {
      "strategy": "user:midnight_gardener",
      "market": "SOL-PERP",
      "resolution_s": 3600,
      "initial_capital": 10000.0,
      "venue": "hyperliquid"
    }
    EOF
"""
import httpx
import sys

API_BASE = "http://localhost:8000/api/v1"

# Read strategy code
strategy_path = "strategies/user/midnight_gardener.py"
with open(strategy_path) as f:
    code = f.read()

# Deploy via API
resp = httpx.post(f"{API_BASE}/paper/start", json={
    "strategy": "user:midnight_gardener",
    "code": code,
    "market": "SOL-PERP",
    "resolution_s": 3600,
    "initial_capital": 10_000.0,
    "venue": "hyperliquid",
})

if resp.status_code == 200:
    data = resp.json()
    print(f"Deployed successfully!")
    print(f"  Session ID: {data.get('session_id')}")
    print(f"  Status:     {data.get('status')}")
    print(f"\nMonitor at: {API_BASE}/paper/status/{data.get('session_id')}")
else:
    print(f"Deployment failed: {resp.status_code}")
    print(resp.text)
    sys.exit(1)
