#!/usr/bin/env bash
# Start/stop/status the Open Notebook dev stack as FOREGROUND processes in
# Herdr panes — inside the CURRENT workspace, one tab ("dev"), 2x2 panes:
#
#   +-----------+-----------+
#   | surrealdb |    api    |
#   +-----------+-----------+
#   |   worker  | frontend  |
#   +-----------+-----------+
#
# Never uses nohup/backgrounding. Requires running inside Herdr.
# Usage: scripts/dev-herdr.sh up|down|status
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAB_LABEL="dev"
cd "$REPO_ROOT"

if [ "${HERDR_ENV:-}" != "1" ]; then
  echo "dev-herdr: must run from a pane inside Herdr (HERDR_ENV is not 1)." >&2
  exit 1
fi
command -v herdr >/dev/null 2>&1 || { echo "dev-herdr: herdr CLI not found in PATH" >&2; exit 1; }

# Migrations are SurrealDB v2 syntax — prefer the v2 binary if present
# (see KB note "Local SurrealDB must be v2").
if [ -x "$HOME/.local/bin/surreal-2.6.5" ]; then
  SURREAL_BIN="${SURREAL_BIN:-$HOME/.local/bin/surreal-2.6.5}"
else
  SURREAL_BIN="${SURREAL_BIN:-surreal}"
fi

jqr() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

# --- current workspace / tab helpers -----------------------------------------

find_tab() { # prints tab_id of the "dev" tab in the current workspace, or nothing
  herdr tab list --workspace "$HERDR_WORKSPACE_ID" | python3 -c "
import json, sys
for t in json.load(sys.stdin)['result']['tabs']:
    if t['label'] == '$TAB_LABEL':
        print(t['tab_id']); break
"
}

tab_panes() { # <tab_id> -> pane ids (one per line)
  herdr pane list --workspace "$HERDR_WORKSPACE_ID" | python3 -c "
import json, sys
for p in json.load(sys.stdin)['result']['panes']:
    if p['tab_id'] == '$1':
        print(p['pane_id'])
"
}

wait_http() { # <url> <timeout_s> <name>
  local url="$1" timeout="${2:-60}" name="$3"
  local deadline=$((SECONDS + timeout))
  until curl -sf -m 2 "$url" >/dev/null 2>&1; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "dev-herdr: $name did not become healthy at $url within ${timeout}s" >&2
      exit 1
    fi
    sleep 2
  done
  echo "dev-herdr: $name healthy ($url)"
}

up() {
  TAB=$(find_tab || true)
  if [ -z "${TAB}" ]; then
    out=$(herdr tab create --workspace "$HERDR_WORKSPACE_ID" --label "$TAB_LABEL" --cwd "$REPO_ROOT" --no-focus 2>/dev/null || herdr tab create --workspace "$HERDR_WORKSPACE_ID" --label "$TAB_LABEL" --cwd "$REPO_ROOT")
    TAB=$(echo "$out" | jqr "d['result']['tab']['tab_id']")
    ROOT_PANE=$(echo "$out" | jqr "d['result']['root_pane']['pane_id']")
    # 2x2 grid: split root right; split each column down
    P_API=$(herdr pane split --pane "$ROOT_PANE" --direction right --cwd "$REPO_ROOT" --no-focus | jqr "d['result']['pane']['pane_id']")
    P_WORKER=$(herdr pane split --pane "$ROOT_PANE" --direction down --cwd "$REPO_ROOT" --no-focus | jqr "d['result']['pane']['pane_id']")
    P_FRONTEND=$(herdr pane split --pane "$P_API" --direction down --cwd "$REPO_ROOT" --no-focus | jqr "d['result']['pane']['pane_id']")
    P_DB="$ROOT_PANE"
    echo "dev-herdr: created tab '$TAB_LABEL' ($TAB) with 2x2 panes"
  else
    mapfile -t PANES < <(tab_panes "$TAB")
    if [ "${#PANES[@]}" -ne 4 ]; then
      echo "dev-herdr: tab '$TAB_LABEL' exists but has ${#PANES[@]} panes (expected 4); recreate it manually." >&2
      exit 1
    fi
    P_DB="${PANES[0]}"; P_API="${PANES[1]}"; P_WORKER="${PANES[2]}"; P_FRONTEND="${PANES[3]}"
    echo "dev-herdr: reusing tab '$TAB_LABEL' ($TAB)"
  fi

  # 1. SurrealDB (everything waits on it)
  if curl -sf -m 2 http://localhost:8000/health >/dev/null 2>&1; then
    echo "dev-herdr: surrealdb already healthy, skipping"
  else
    herdr pane run "$P_DB" "SURREAL_BIN=$SURREAL_BIN make database-local-fg"
    wait_http http://localhost:8000/health 60 surrealdb
  fi

  # 2. API (runs migrations on startup — must finish before the worker)
  if curl -sf -m 2 http://localhost:5055/health >/dev/null 2>&1; then
    echo "dev-herdr: api already healthy, skipping"
  else
    herdr pane run "$P_API" "make api"
    wait_http http://localhost:5055/health 180 api
  fi

  # 3. Worker (crashes if it starts before migrations exist)
  if pgrep -f surreal-commands-worker >/dev/null 2>&1; then
    echo "dev-herdr: worker already running, skipping"
  else
    herdr pane run "$P_WORKER" "make worker-start"
    if ! herdr pane wait-output "$P_WORKER" --match "LIVE query listener" --timeout 90000 >/dev/null; then
      echo "dev-herdr: worker did not report healthy (no 'LIVE query listener')" >&2
      exit 1
    fi
    echo "dev-herdr: worker healthy (LIVE query listener up)"
  fi

  # 4. Frontend
  if curl -sf -m 2 -o /dev/null http://localhost:3000; then
    echo "dev-herdr: frontend already healthy, skipping"
  else
    herdr pane run "$P_FRONTEND" "make frontend"
    wait_http http://localhost:3000 120 frontend
  fi

  echo
  echo "dev-herdr: stack is up in tab '$TAB_LABEL' of this workspace (2x2 panes: db/api/worker/frontend)."
  echo "dev-herdr: UI at http://localhost:3000 — stop with: $0 down (or Ctrl-C per pane)"
}

down() {
  TAB=$(find_tab || true)
  if [ -n "${TAB}" ]; then
    for pane in $(tab_panes "$TAB"); do
      herdr pane send-keys "$pane" ctrl+c >/dev/null 2>&1 || true
    done
  fi
  sleep 2
  make database-local-stop >/dev/null 2>&1 || true
  for port in 5055 3000; do
    lsof -ti :$port 2>/dev/null | xargs kill 2>/dev/null || true
  done
  echo "dev-herdr: stack stopped (tab '$TAB_LABEL' left in place for logs)"
}

status() {
  local ok=1
  check() { # <url-or-pgrep> <name>
    if curl -sf -m 2 "$1" >/dev/null 2>&1; then
      echo "  ✅ $2 ($1)"
    else
      echo "  ❌ $2 ($1)"; ok=0
    fi
  }
  check http://localhost:8000/health surrealdb
  check http://localhost:5055/health api
  if pgrep -f surreal-commands-worker >/dev/null 2>&1; then echo "  ✅ worker (surreal-commands-worker running)"; else echo "  ❌ worker"; ok=0; fi
  check http://localhost:3000 frontend
  TAB=$(find_tab || true)
  [ -n "${TAB}" ] && echo "  tab: '$TAB_LABEL' ($TAB) in workspace $HERDR_WORKSPACE_ID"
  exit $((1 - ok))
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  status) status ;;
  *) echo "Usage: $0 up|down|status" >&2; exit 2 ;;
esac
