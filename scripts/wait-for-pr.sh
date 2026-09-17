#!/bin/sh
# Wait for all GitHub PR checks to complete, then exit with their result.
# Blocks on `gh pr checks --watch` (event-driven, no fixed sleep); the only
# sleep here is the interval between deadline checks. `--fail-fast` exits as
# soon as one check fails instead of waiting for the rest.
#
# Usage: wait-for-pr.sh <pr-number|url|branch> [timeout_seconds]
# Exit codes: 0 = all checks passed, non-zero = failed/cancelled, 124 = timeout

set -u

PR="${1:?usage: wait-for-pr.sh <pr-number|url|branch> [timeout_seconds]}"
TIMEOUT="${2:-1800}"

# Watcher output is silenced: --watch re-renders the checks table on every
# update, which is noise in CI logs (and its grandchild can outlive a
# timeout-kill). The final result is printed once below.
gh pr checks "$PR" --watch --fail-fast > /dev/null 2>&1 &
watcher=$!

DEADLINE=$(( $(date +%s) + TIMEOUT ))
while kill -0 "$watcher" 2>/dev/null; do
    if [ "$(date +%s)" -ge "$DEADLINE" ]; then
        echo "ERROR: checks for PR '$PR' did not finish within ${TIMEOUT}s" >&2
        kill "$watcher" 2>/dev/null
        wait "$watcher" 2>/dev/null
        exit 124
    fi
    sleep 5
done

wait "$watcher"
rc=$?

if [ "$rc" -eq 0 ]; then
    echo "PR '$PR': all checks passed"
else
    echo "PR '$PR': checks failed or were cancelled" >&2
    gh pr checks "$PR" || true
fi
exit "$rc"
