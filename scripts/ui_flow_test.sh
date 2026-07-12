#!/usr/bin/env bash
# Full-pipeline UI walkthrough against local staging. Opt-in —
# not in pre-commit because it runs real LLM calls (~3-5 min).
#
# Usage:
#   ./scripts/ui_flow_test.sh
#   ./scripts/ui_flow_test.sh --skip-seed   # reuse previous staging state
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

STAGING_JWT_SECRET="staging-only-insecure-key-change-me"
STAGING_USER_ID="00000000-0000-4000-8000-000000000001"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
bold()  { printf '\n\033[1m── %s ──\033[0m\n' "$*"; }

if ! curl -sSfo /dev/null http://localhost:8181/health 2>/dev/null; then
    red "Staging not running. Start it:  docker compose -f docker-compose.staging.yml up -d"
    exit 1
fi

if [[ "${1:-}" != "--skip-seed" ]]; then
    bold "Reset + seed staging fixtures"
    python scripts/seed_staging.py
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

bold "Run full-pipeline UI flow"
cd tests/ui && JWT="$JWT" node flow_test.mjs
green "✓ Full-flow test complete"
