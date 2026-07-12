#!/usr/bin/env bash
# Production smoke test for the Terraform deployment.
#
# Uses only AWS APIs/SSM and the canonical app URL. The EC2 instance has
# no public IP and raw ALB DNS requests are intentionally denied by the
# listener host-header rules.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

resolve_output() {
    (
        cd "$SCRIPT_DIR" &&
        ~/.local/bin/terraform output -raw "$1" 2>/dev/null ||
        terraform output -raw "$1" 2>/dev/null ||
        true
    )
}

INSTANCE_ID="${1:-$(resolve_output instance_id)}"
APP_URL="${APP_URL:-$(resolve_output app_url)}"
ALB_DNS="$(resolve_output alb_dns_name)"
AWS_REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || echo us-east-1)}"

[[ -z "$INSTANCE_ID" ]] && { echo "no instance_id (pass arg or run after terraform apply)" >&2; exit 2; }
[[ -z "$APP_URL" ]]     && { echo "no app_url (set APP_URL or run after terraform apply)" >&2; exit 2; }

pass=0
fail=0
ok()   { printf "  \033[32mOK\033[0m %s\n" "$1"; pass=$((pass + 1)); }
bad()  { printf "  \033[31mFAIL\033[0m %s - %s\n" "$1" "$2"; fail=$((fail + 1)); }
section() { printf "\n\033[1m%s\033[0m\n" "$1"; }

ssm_run() {
    local label="$1"; shift
    local cmd="$*"
    local cmd_id status stdout stderr waited=0
    cmd_id=$(aws ssm send-command \
        --region "$AWS_REGION" \
        --document-name "AWS-RunShellScript" \
        --instance-ids "$INSTANCE_ID" \
        --comment "smoke-$label" \
        --parameters "commands=[\"$(printf '%s' "$cmd" | sed 's/"/\\"/g')\"]" \
        --timeout-seconds 120 \
        --query 'Command.CommandId' \
        --output text 2>/dev/null) || return 1

    status=Pending
    while [[ "$status" == "Pending" || "$status" == "InProgress" || "$status" == "Delayed" ]]; do
        sleep 2
        waited=$((waited + 2))
        if [[ $waited -ge 120 ]]; then
            echo "timeout waiting for $label"
            return 1
        fi
        status=$(aws ssm get-command-invocation \
            --region "$AWS_REGION" \
            --command-id "$cmd_id" \
            --instance-id "$INSTANCE_ID" \
            --query 'Status' --output text 2>/dev/null || echo "Pending")
    done

    stdout=$(aws ssm get-command-invocation \
        --region "$AWS_REGION" \
        --command-id "$cmd_id" \
        --instance-id "$INSTANCE_ID" \
        --query 'StandardOutputContent' --output text 2>/dev/null || true)
    stderr=$(aws ssm get-command-invocation \
        --region "$AWS_REGION" \
        --command-id "$cmd_id" \
        --instance-id "$INSTANCE_ID" \
        --query 'StandardErrorContent' --output text 2>/dev/null || true)

    [[ -n "$stderr" && "$stderr" != "None" ]] && printf '%s\n' "$stderr" >&2
    [[ "$status" == "Success" ]] || return 1
    printf '%s\n' "$stdout"
}

http_code() {
    curl -sS --max-time 10 -o /tmp/outreach-smoke.out -w '%{http_code}' "$1"
}

section "Layer 1 — instance and bootstrap"

ssm_info=$(aws ssm describe-instance-information \
    --region "$AWS_REGION" \
    --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --query 'InstanceInformationList[0].PingStatus' \
    --output text 2>/dev/null || true)
if [[ "$ssm_info" == "Online" ]]; then
    ok "SSM agent online"
else
    bad "SSM agent online" "$ssm_info"
fi

cloud_init=$(ssm_run cloud-init "cloud-init status" 2>/dev/null || true)
if echo "$cloud_init" | grep -qE 'status:\s*(done|error)'; then
    ok "cloud-init complete ($cloud_init)"
else
    bad "cloud-init complete" "${cloud_init:-no output}"
fi

docker_version=$(ssm_run docker-version "docker --version && docker compose version" 2>/dev/null || true)
if echo "$docker_version" | grep -q "Docker version" && echo "$docker_version" | grep -q "Docker Compose"; then
    ok "docker and compose installed"
else
    bad "docker and compose installed" "${docker_version:-missing}"
fi

section "Layer 2 — containers"

ps_out=$(ssm_run compose-ps "cd /opt/outreach && docker compose ps --format '{{.Name}}|{{.Status}}'" 2>/dev/null || true)
if [[ -z "$ps_out" ]]; then
    bad "compose ps" "empty output; did remote_deploy.sh run?"
else
    for svc in server frontend chroma; do
        line=$(echo "$ps_out" | grep "outreach-$svc" || true)
        if [[ -z "$line" ]]; then
            bad "$svc running" "not found"
        elif echo "$line" | grep -qi "healthy"; then
            ok "$svc healthy"
        elif echo "$line" | grep -qi "up"; then
            ok "$svc running"
        else
            bad "$svc running" "$line"
        fi
    done
fi

section "Layer 3 — public routing"

code=$(http_code "$APP_URL/")
[[ "$code" == "200" ]] && ok "canonical frontend / -> 200" || bad "canonical frontend /" "$code"

code=$(http_code "$APP_URL/auth/me")
[[ "$code" == "401" ]] && ok "canonical /auth/me -> 401 unauthenticated" || bad "canonical /auth/me" "$code"

code=$(http_code "$APP_URL/health")
[[ "$code" == "404" ]] && ok "public /health not routed -> 404" || bad "public /health" "$code"

if [[ -n "$ALB_DNS" ]]; then
    raw_code=$(curl -k -sS --max-time 10 -o /tmp/outreach-smoke-raw.out -w '%{http_code}' "https://$ALB_DNS/health")
    [[ "$raw_code" == "404" ]] && ok "raw ALB DNS denied -> 404" || bad "raw ALB DNS /health" "$raw_code"
fi

section "Layer 4 — internal service health"

server_health=$(ssm_run server-health "curl -sS http://localhost:8080/health" 2>/dev/null || true)
if echo "$server_health" | grep -q '"status":"ok"'; then
    ok "server /health internal"
else
    bad "server /health internal" "${server_health:0:120}"
fi

chroma_probe=$(ssm_run chroma-heartbeat "docker exec outreach-server-1 bash -c 'exec 3<>/dev/tcp/chroma/8000; printf \"GET /api/v2/heartbeat HTTP/1.0\r\nHost: chroma\r\n\r\n\" >&3; head -n 1 <&3'" 2>/dev/null || true)
if echo "$chroma_probe" | grep -q " 200 "; then
    ok "chroma heartbeat internal"
else
    bad "chroma heartbeat internal" "${chroma_probe:0:120}"
fi

section "Layer 5 — unauthenticated route guard"
if BASE_URL="$APP_URL" "$REPO_ROOT/scripts/prod_auth_smoke.sh"; then
    ok "unauthenticated route smoke"
else
    bad "unauthenticated route smoke" "failed"
fi

printf "\n\033[1mResult:\033[0m %d passed, %d failed\n" "$pass" "$fail"
if [[ $fail -gt 0 ]]; then
    echo "Inspect logs: aws ssm start-session --region $AWS_REGION --target $INSTANCE_ID"
    exit 1
fi
echo "Frontend: $APP_URL"
