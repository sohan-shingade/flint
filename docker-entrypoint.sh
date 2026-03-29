#!/bin/sh
set -e

# Create directories if missing
mkdir -p /app/data /app/strategies/user

# Generate default config if missing
if [ ! -f /app/flint.yaml ]; then
    python3 -c "
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
    echo "Generated default flint.yaml"
fi

exec uvicorn flint.api.main:app --host 0.0.0.0 --port 8000
