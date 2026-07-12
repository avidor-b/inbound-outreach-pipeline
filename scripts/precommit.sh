#!/usr/bin/env bash
# Pre-commit regression suite. The UI phases are budget-guarded so a
# warm local run stays under roughly 4 minutes.
#
# Runs against a LOCAL STAGING stack (docker-compose.staging.yml) —
# never against production. The staging stack is started on demand
# and left running for subsequent commits (fast warm path).
#
# Stages:
#   1. Python regressions      session + critical workflow regressions
#   2. Frontend type check     `npx tsc --noEmit`
#   3. UI regression           tests/ui/ui_regression.mjs vs localhost
#
# Env toggles:
#   SKIP_UI=1               Skip the playwright step (emergency).
#   SKIP_SEED=1             Assume staging is already seeded.
#   REQUIRE_DATA=0          Run UI in core mode (no hint-sum check).
#   PYTHON=...              Override the Python interpreter; defaults
#                            to ./venv/bin/python when available.
set -euo pipefail

START=$SECONDS
DEADLINE=$((START + 240))

cd "$(git rev-parse --show-toplevel)"

if [[ -z "${PYTHON:-}" ]]; then
    if [[ -x "./venv/bin/python" ]]; then
        PYTHON="./venv/bin/python"
    else
        PYTHON="python"
    fi
fi

STAGING_SERVER="http://localhost:${STAGING_SERVER_PORT:-8181}"
STAGING_FRONTEND="http://localhost:${STAGING_FRONTEND_PORT:-3131}"
# Must match docker-compose.staging.yml JWT_SECRET.
STAGING_JWT_SECRET="staging-only-insecure-key-change-me"
STAGING_USER_ID="00000000-0000-4000-8000-000000000001"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
bold()  { printf '\n\033[1m── %s ──\033[0m\n' "$*"; }

remaining() { echo $((DEADLINE - SECONDS)); }
guard() {
    local r=$(remaining)
    if (( r < 10 )); then
        red "✗ Over 4-minute budget; aborting"
        exit 1
    fi
    echo "$r"
}

# --- 1. Python regressions (hit the test DB, not staging) ---
bold "Python regressions"
LEFT=$(guard)
if ! "$PYTHON" -m pytest \
        tests/test_session_regressions.py \
        tests/test_langgraph_features.py::TestGetDraftsTool::test_get_approved_drafts_by_company_name_checks_all_matching_contacts \
        tests/test_policies_submission.py::TestClassifyIntent::test_partnerships_topic_does_not_block_clear_buyer_language \
        tests/test_services_intake.py::TestProcessSubmission::test_partnerships_topic_buyer_language_routes_to_sales \
        tests/test_suggester_clean_orphans.py \
        -q --tb=short -x; then
    red "✗ Python regressions FAILED"
    exit 1
fi
green "✓ Python regressions passed"

# --- 2. Frontend type check ---
bold "Frontend type check"
LEFT=$(guard)
if ! (cd frontend && timeout "${LEFT}s" npx tsc --noEmit); then
    red "✗ TypeScript type check FAILED"
    exit 1
fi
green "✓ Type check passed"

# --- 3. UI regression against staging ---
if [[ "${SKIP_UI:-0}" == "1" ]]; then
    bold "UI regression — SKIP_UI=1"
    ELAPSED=$((SECONDS - START))
    green "✓ Pre-commit (partial) passed in ${ELAPSED}s"
    exit 0
fi

bold "UI regression (local staging)"

# Ensure staging stack is up. Cold start builds the images (~5 min
# on a fresh checkout); warm start is <10s.
if ! timeout 3s curl -sSfo /dev/null "$STAGING_SERVER/health" 2>/dev/null; then
    red "  ! Staging not reachable at $STAGING_SERVER"
    red "  ! Start it with:   docker compose -f docker-compose.staging.yml up -d"
    red "  ! Or bypass with:  SKIP_UI=1 git commit ..."
    exit 1
fi

# Seed fixtures (idempotent). Skip with SKIP_SEED=1 on tight loops.
if [[ "${SKIP_SEED:-0}" != "1" ]]; then
    LEFT=$(guard)
    if ! timeout "${LEFT}s" "$PYTHON" scripts/seed_staging.py >/dev/null 2>&1; then
        red "  ! Seed failed (non-fatal) — continuing with current staging state"
    fi
fi

# Mint a JWT against the staging secret.
JWT=$("$PYTHON" -c "
import jwt, secrets
from datetime import datetime, timezone, timedelta
print(jwt.encode(
    {'sub': '$STAGING_USER_ID', 'email': 'test@example.com',
     'role': 'user', 'jti': secrets.token_urlsafe(16),
     'exp': datetime.now(timezone.utc) + timedelta(hours=1)},
    '$STAGING_JWT_SECRET', algorithm='HS256',
))")

LEFT=$(guard)
REQUIRE_DATA="${REQUIRE_DATA:-1}"
if ! (cd tests/ui && timeout "${LEFT}s" env \
        BASE_URL="$STAGING_FRONTEND" JWT="$JWT" REQUIRE_DATA="$REQUIRE_DATA" \
        node ui_regression.mjs); then
    red "✗ UI regression FAILED"
    exit 1
fi
green "✓ UI regression passed"

# Thread-switch regression — steady-state (3 rounds) plus a rapid-fire
# burst that exercises the in-flight hydration race. Skips itself
# cleanly when the staging DB has <2 threads with content (new env /
# wiped seed), so it never blocks a fresh-clone pre-commit.
bold "Thread-switch (steady + rapid) vs staging"
LEFT=$(guard)
if ! (cd tests/ui && timeout "${LEFT}s" env \
        BASE_URL="$STAGING_FRONTEND" API_URL="$STAGING_SERVER" JWT="$JWT" \
        node thread_switch.mjs); then
    red "✗ Thread-switch FAILED"
    exit 1
fi
green "✓ Thread-switch passed"

ELAPSED=$((SECONDS - START))
green ""
green "✓ All pre-commit checks passed (${ELAPSED}s of 240s budget)"
