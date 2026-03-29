# Flint — Solana Algo Trading Platform
# Multi-stage build: Python backend + Node.js frontend

FROM python:3.11-slim AS backend
WORKDIR /app
COPY pyproject.toml .
COPY flint/ flint/
RUN pip install --no-cache-dir -e .

FROM node:20-slim AS frontend
WORKDIR /app/ui
COPY ui/package.json ui/package-lock.json* ./
RUN npm ci --silent
COPY ui/ .
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install backend
COPY pyproject.toml .
COPY flint/ flint/
COPY scripts/ scripts/
RUN pip install --no-cache-dir -e .

# Copy built frontend
COPY --from=frontend /app/ui/dist /app/ui/dist

# Copy entrypoint
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Create data directory
RUN mkdir -p data strategies/user

EXPOSE 8000
ENV FLINT_DB_PATH=/app/data/flint.duckdb
ENV FLINT_COLLECTOR_ENABLED=true

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
