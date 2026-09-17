#!/bin/bash
# Development environment startup for Open Notebook
# Assumes SurrealDB is already running externally (per .env config)

set -e

echo "=== Open Notebook Dev Startup ==="

# Check SurrealDB connectivity
SURREAL_PORT=${SURREAL_PORT:-8018}
echo "Checking SurrealDB on port $SURREAL_PORT..."
if ! nc -z localhost "$SURREAL_PORT" 2>/dev/null; then
  echo "❌ SurrealDB not reachable on port $SURREAL_PORT. Please start it first."
  exit 1
fi
echo "✅ SurrealDB is running"

# Install dependencies if needed
echo "Syncing Python dependencies..."
uv sync

echo "Syncing frontend dependencies..."
cd frontend && npm install && cd ..

# Start API backend in background
echo "Starting API backend (port 5055)..."
uv run --env-file .env run_api.py &
scripts/wait-http.sh http://localhost:5055/health 180 api || {
  echo "❌ API failed to become healthy — check the logs above." >&2
  exit 1
}

# Start background worker in background
echo "Starting background worker..."
uv run --env-file .env surreal-commands-worker --import-modules commands --max-tasks "${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-5}" &
deadline=$((SECONDS + 30))
until pgrep -f "surreal-commands-worker" > /dev/null 2>&1; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "❌ Worker did not start within 30s." >&2
    exit 1
  fi
  sleep 1
done

# Start frontend (foreground)
echo "Starting Next.js frontend (port 3000)..."
echo ""
echo "✅ All services starting!"
echo "  Frontend: http://localhost:3000"
echo "  API:      http://localhost:5055"
echo "  API Docs: http://localhost:5055/docs"
echo ""
cd frontend && npm run dev
