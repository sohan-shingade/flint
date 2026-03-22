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

# Install backend
COPY pyproject.toml .
COPY flint/ flint/
COPY scripts/ scripts/
COPY flint.yaml .
RUN pip install --no-cache-dir -e .

# Copy built frontend
COPY --from=frontend /app/ui/dist /app/ui/dist

# Create data directory
RUN mkdir -p data strategies/user

EXPOSE 8000
ENV FLINT_DB_PATH=/app/data/flint.duckdb
ENV FLINT_COLLECTOR_ENABLED=true

CMD ["uvicorn", "flint.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
