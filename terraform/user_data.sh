#!/bin/bash
# Outreach EC2 bootstrap. Runs once on first boot as root via cloud-init.
# Installs docker + compose, the CloudWatch agent, and the AWS CLI so
# the node can pull secrets from SSM + images from ECR on every deploy.
#
# Logs stream to /var/log/cloud-init-output.log — tail that if something
# goes sideways.

set -euxo pipefail

# --- System ---
dnf update -y
dnf install -y git docker rsync amazon-cloudwatch-agent jq

# --- Docker ---
systemctl enable --now docker
usermod -aG docker ec2-user

# --- Docker Compose (v2 plugin) ---
# Version + SHA256 are pinned. If you bump the version, grab the new
# hash from the GitHub release page (checksums are posted next to the
# artifact) and update BOTH constants before applying.
COMPOSE_VERSION="v2.29.7"
COMPOSE_SHA256="383ce6698cd5d5bbf958d2c8489ed75094e34a77d340404d9f32c4ae9e12baf0"
DOCKER_CONFIG="/home/ec2-user/.docker"
mkdir -p "$DOCKER_CONFIG/cli-plugins"
curl -fsSL \
    "https://github.com/docker/compose/releases/download/$${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
    -o "$DOCKER_CONFIG/cli-plugins/docker-compose"
echo "$${COMPOSE_SHA256}  $DOCKER_CONFIG/cli-plugins/docker-compose" | sha256sum -c -
chmod +x "$DOCKER_CONFIG/cli-plugins/docker-compose"
chown -R ec2-user:ec2-user "$DOCKER_CONFIG"

# Also install docker-compose plugin for root so `sudo docker compose`
# works on the bootstrap path below.
mkdir -p /root/.docker/cli-plugins
cp "$DOCKER_CONFIG/cli-plugins/docker-compose" /root/.docker/cli-plugins/docker-compose

# --- Project dir ---
install -d -o ec2-user -g ec2-user -m 0755 /opt/outreach

# --- Chroma persistent volume ----------------------------------------------
# Terraform attaches a dedicated EBS volume as /dev/sdf. Nitro instances expose
# EBS volumes as /dev/nvme*n1, and the attachment can complete after cloud-init
# has started. Wait for the volume by ID, create a filesystem only if it is new,
# then mount it at the host path used by docker-compose.yml.

CHROMA_VOLUME_ID="${chroma_volume_id}"
CHROMA_MOUNT="/data/outreach/chroma"
install -d -m 0755 "$CHROMA_MOUNT"

find_chroma_device() {
    local serial="$${CHROMA_VOLUME_ID//-/}"
    for dev in "/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_$serial" \
               "/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_$CHROMA_VOLUME_ID" \
               /dev/xvdf /dev/sdf; do
        if [ -e "$dev" ]; then
            readlink -f "$dev"
            return 0
        fi
    done
    return 1
}

CHROMA_DEVICE=""
for attempt in $(seq 1 60); do
    if CHROMA_DEVICE="$(find_chroma_device)"; then
        echo "found Chroma EBS device $CHROMA_DEVICE on attempt $attempt"
        break
    fi
    echo "waiting for Chroma EBS volume $CHROMA_VOLUME_ID (attempt $attempt)"
    sleep 5
done

if [ -z "$CHROMA_DEVICE" ]; then
    echo "ERROR: Chroma EBS volume $CHROMA_VOLUME_ID did not appear" >&2
    exit 1
fi

if ! blkid "$CHROMA_DEVICE" >/dev/null 2>&1; then
    mkfs.ext4 -L outreach-chroma "$CHROMA_DEVICE"
fi

CHROMA_UUID="$(blkid -s UUID -o value "$CHROMA_DEVICE")"
if ! grep -q "UUID=$CHROMA_UUID" /etc/fstab; then
    echo "UUID=$CHROMA_UUID $CHROMA_MOUNT ext4 defaults,nofail 0 2" >> /etc/fstab
fi

mountpoint -q "$CHROMA_MOUNT" || mount "$CHROMA_MOUNT"
chown -R ec2-user:ec2-user "$CHROMA_MOUNT"

# --- CloudWatch agent -----------------------------------------------------
# Fetch the agent config from SSM (the terraform put it there) and start
# the service. Agent pushes memory / disk / swap metrics + docker logs.

AWS_REGION="${aws_region}"
CWAGENT_PARAM="${cwagent_config_ssm}"

# aws CLI is pre-installed on AL2023; confirm:
aws --version || dnf install -y awscli

# Pull the config JSON into the agent's expected location.
aws ssm get-parameter \
    --region "$AWS_REGION" \
    --name "$CWAGENT_PARAM" \
    --query 'Parameter.Value' \
    --output text \
    > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

# Start (and enable across reboots).
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config -m ec2 \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json \
    -s
systemctl enable amazon-cloudwatch-agent

# --- Secrets from SSM -----------------------------------------------------
# /opt/outreach/refresh-env.sh pulls every /outreach/$env/* SecureString
# into .env and appends the baked-in compose knobs. Installed here so
# remote_deploy.sh can re-run it on every deploy (rotated SSM values
# take effect without a full instance replacement).

# The refresh script is written to disk via a quoted heredoc
# ('REFRESH_SH'), so $VARS inside stay as shell variables rather
# than getting interpolated by terraform templatefile.
cat > /opt/outreach/refresh-env.sh <<'REFRESH_SH'
#!/bin/bash
set -euo pipefail
AWS_REGION="AWS_REGION_PLACEHOLDER"
ENVIRONMENT="ENV_PLACEHOLDER"
ECR_REGISTRY="ECR_REGISTRY_PLACEHOLDER"
ENV_FILE="/opt/outreach/.env"
# Preserve the currently-deployed image tags across refresh. The
# deploy script writes these, and compose needs them to keep
# pulling the right tag; losing them silently reverts compose to
# the outreach-*:local fallback, so a rotation that calls
# refresh-env.sh ends up running stale images.
PRESERVED=""
if [ -f "$ENV_FILE" ]; then
    PRESERVED=$(grep -E '^(SERVER_IMAGE|FRONTEND_IMAGE|OUTREACH_VERSION)=' "$ENV_FILE" 2>/dev/null || true)
fi
: > "$ENV_FILE"
chmod 600 "$ENV_FILE"
aws ssm get-parameters-by-path \
    --region "$AWS_REGION" \
    --path "/outreach/$ENVIRONMENT" \
    --recursive \
    --with-decryption \
    --query 'Parameters[].{name:Name,value:Value}' \
    --output json \
| jq -r '.[]
    # Strip the /outreach/<env>/ prefix
    | .name |= sub("^.*/"; "")
    # Env-var names must be [A-Z_][A-Z0-9_]* — skip anything else
    # (e.g. cwagent-config JSON is fetched by the agent directly).
    | select(.name | test("^[A-Za-z_][A-Za-z0-9_]*$"))
    | "\(.name)=\(.value)"' \
>> "$ENV_FILE"
cat >> "$ENV_FILE" <<CONFIG
ENVIRONMENT=$ENVIRONMENT
CHROMA_HOST=chroma
CHROMA_PORT=8000
SERVER_HOST_PORT=8080
FRONTEND_HOST_PORT=3001
ECR_REGISTRY=$ECR_REGISTRY
COOKIE_SECURE=true
# Same-origin deploy — frontend and backend share the ALB host.
# Empty NEXT_PUBLIC_BACKEND_URL means the client bundle uses
# relative URLs, so no cross-origin requests, no CORS.
NEXT_PUBLIC_BACKEND_URL=
FRONTEND_URL=https://ALB_DNS_PLACEHOLDER
CONFIG
if [ -n "$PRESERVED" ]; then
    printf '%s\n' "$PRESERVED" >> "$ENV_FILE"
fi
chown ec2-user:ec2-user "$ENV_FILE"
echo "refreshed $ENV_FILE ($(wc -l < "$ENV_FILE") lines)"
REFRESH_SH

# Substitute the terraform vars and make executable.
sed -i "s#AWS_REGION_PLACEHOLDER#${aws_region}#g; s#ENV_PLACEHOLDER#${environment}#g; s#ECR_REGISTRY_PLACEHOLDER#${ecr_registry}#g; s#ALB_DNS_PLACEHOLDER#${alb_dns_name}#g" /opt/outreach/refresh-env.sh
chmod +x /opt/outreach/refresh-env.sh

# Run once at boot.
/opt/outreach/refresh-env.sh

# --- ECR login for docker -----------------------------------------------
# Credential helper writes short-lived tokens on demand; no stored
# passwords. Both ec2-user and root get the config so either can pull.

mkdir -p /home/ec2-user/.docker /root/.docker
cat > /home/ec2-user/.docker/config.json <<EOF
{ "credsStore": "ecr-login" }
EOF
cp /home/ec2-user/.docker/config.json /root/.docker/config.json
chown -R ec2-user:ec2-user /home/ec2-user/.docker

# Install the ECR credential helper (small Go binary from ECR repo).
# Checksum pinned — the bucket is AWS-owned, but defense-in-depth.
ECR_HELPER_VERSION="0.9.0"
ECR_HELPER_SHA256="dd6bd933e439ddb33b9f005ad5575705a243d4e1e3d286b6c82928bcb70e949a"
curl -fsSL \
    "https://amazon-ecr-credential-helper-releases.s3.us-east-2.amazonaws.com/$${ECR_HELPER_VERSION}/linux-amd64/docker-credential-ecr-login" \
    -o /usr/local/bin/docker-credential-ecr-login
echo "$${ECR_HELPER_SHA256}  /usr/local/bin/docker-credential-ecr-login" | sha256sum -c -
chmod +x /usr/local/bin/docker-credential-ecr-login

# --- MOTD ---------------------------------------------------------------
cat <<EOF > /etc/motd

    Outreach ${environment} — managed by Terraform

    Deploys:
      Use scripts/remote_deploy.sh — it builds locally, pushes to ECR,
      and runs `docker compose pull && up -d` here.

    On-box:
      cd /opt/outreach
      docker compose ps
      docker compose logs -f server
      docker compose logs -f frontend

    Secrets:       /outreach/${environment}/* in SSM Parameter Store
    Logs:          /outreach/${environment}/docker in CloudWatch
    Memory alarm:  /outreach/${environment}-high-memory

EOF

echo "outreach bootstrap: done" | tee -a /var/log/outreach-bootstrap.log
