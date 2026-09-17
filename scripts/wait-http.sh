#!/bin/sh
# Wait for an HTTP endpoint to respond before proceeding.
# Returns as soon as the endpoint answers; fails loudly after the deadline
# (no blind `sleep N` + hope). The `sleep 1` below is between condition
# checks, which is the only acceptable use of sleep.
#
# Usage: wait-http.sh <url> <timeout_seconds> [name]
# Example: wait-http.sh http://localhost:5055/health 180 api

set -u

URL="${1:?usage: wait-http.sh <url> <timeout_seconds> [name]}"
TIMEOUT="${2:?usage: wait-http.sh <url> <timeout_seconds> [name]}"
NAME="${3:-$URL}"
DEADLINE=$(( $(date +%s) + TIMEOUT ))

while :; do
    if curl -sf -m 2 "$URL" > /dev/null 2>&1; then
        echo "$NAME is ready ($URL)"
        exit 0
    fi
    if [ "$(date +%s)" -ge "$DEADLINE" ]; then
        echo "ERROR: $NAME did not become ready at $URL within ${TIMEOUT}s" >&2
        exit 1
    fi
    sleep 1
done
