#!/usr/bin/env bash
# Thread-switch regression — drives the app through 12 sidebar clicks
# across 4 threads × 3 rounds, asserts each switch renders that thread's
# messages (not the "How can I help you today?" empty state).
#
# Guards the useThreadHydration fix for the connectAgent/setMessages
# race (commit history has the diagnosis; the bug left the chat area
# blank on ~40% of switches).
#
# Usage:
#   ./scripts/ui_thread_switch.sh                # local staging (default)
#   BASE_URL=https://<alb> JWT=<token> ./scripts/ui_thread_switch.sh
#
# Requires: docker compose -f docker-compose.staging.yml up -d  (or an
# explicit BASE_URL+JWT pointing at any deployed instance with threads).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

STAGING_JWT_SECRET="staging-only-insecure-key-change-me"
STAGING_USER_ID="00000000-0000-4000-8000-000000000001"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
bold()  { printf '\n\033[1m── %s ──\033[0m\n' "$*"; }

# Mint a JWT only if the caller didn't supply one. For deployed-target
# runs, pass BASE_URL + JWT yourself.
if [[ -z "${JWT:-}" ]]; then
    if ! curl -sSfo /dev/null http://localhost:8181/health 2>/dev/null; then
        red "Staging not running. Start it:  docker compose -f docker-compose.staging.yml up -d"
        exit 1
    fi
    bold "Mint JWT for test user"
    JWT=$(python -c "
import jwt, secrets
from datetime import datetime, timezone, timedelta
print(jwt.encode(
    {'sub': '$STAGING_USER_ID', 'email': 'test@example.com',
     'role': 'user', 'jti': secrets.token_urlsafe(16),
     'exp': datetime.now(timezone.utc) + timedelta(hours=1)},
    '$STAGING_JWT_SECRET', algorithm='HS256',
))")
    export JWT
fi

BASE_URL="${BASE_URL:-http://localhost:3131}"
# Split-port local staging: frontend on 3131 doesn't proxy /threads —
# the test must hit the backend (8181) for API calls, and the UI
# (3131) for page loads. For deployed same-origin ALB runs, the
# caller sets API_URL themselves (or omits it and the test defaults
# it to BASE_URL).
API_URL="${API_URL:-http://localhost:8181}"
bold "Run thread-switch regression vs $BASE_URL (api=$API_URL)"
cd tests/ui && BASE_URL="$BASE_URL" API_URL="$API_URL" JWT="$JWT" node thread_switch.mjs
green "✓ Thread-switch test complete"
