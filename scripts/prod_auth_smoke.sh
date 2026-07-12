#!/usr/bin/env bash
# Production unauthenticated-route smoke test.
#
# Verifies sensitive routes fail closed without a session. Intended to
# run after deploy and safe to run against production: all mutating
# probes are unauthenticated and must return 401 before reaching app
# logic.
set -euo pipefail

BASE_URL="${BASE_URL:-https://app.example.com}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass() { printf '  ✓ %s\n' "$*"; }
fail() { printf '  ✗ %s\n' "$*" >&2; }

probe() {
    local method="$1"
    local path="$2"
    local expected="$3"
    local body="${4:-}"
    local out="$TMP_DIR/$(printf '%s-%s' "$method" "$path" | tr -c 'A-Za-z0-9_' '_').out"
    local code

    if [[ -n "$body" ]]; then
        code=$(curl -sS -o "$out" -w '%{http_code}' \
            -X "$method" "$BASE_URL$path" \
            -H 'content-type: application/json' \
            -d "$body")
    else
        code=$(curl -sS -o "$out" -w '%{http_code}' \
            -X "$method" "$BASE_URL$path")
    fi

    if [[ "$code" != "$expected" ]]; then
        fail "$method $path returned $code, expected $expected; body=$(head -c 300 "$out")"
        return 1
    fi
    pass "$method $path -> $code"
}

echo "▶ Unauthenticated production route smoke vs $BASE_URL"

probe GET  /auth/me 401
probe GET  /threads 401
probe POST /threads 401 '{"title":"smoke"}'
probe GET  /threads/smoke-thread 401
probe PATCH /threads/smoke-thread 401 '{"title":"smoke"}'
probe GET  /threads/smoke-thread/messages 401
probe GET  /threads/smoke-thread/history 401
probe GET  /threads/smoke-thread/artifacts 401
probe POST /threads/smoke-thread/approve 401 '{"artifact_id":1,"action":"approve"}'
probe PATCH /contacts/smoke-contact/website 401 '{"website":"https://example.com"}'
probe GET  /contacts/smoke-contact/email 401
probe GET  /contacts/smoke-contact/firstname 401
probe POST /engagements/1/mark-sent 401 '{}'
probe GET  /connections 401
probe GET  /copilotkit/health 401
probe POST /copilotkit 401 '{}'
probe POST /copilotkit-suggester 401 '{}'

echo
echo "✓ Unauthenticated production route smoke passed"
