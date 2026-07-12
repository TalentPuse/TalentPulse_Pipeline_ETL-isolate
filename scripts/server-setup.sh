#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# TalentPulse VPS initial setup script
# Run once on a fresh Ubuntu 22.04 server:
#   bash scripts/server-setup.sh
#
# Target box: 4GB RAM / 2 vCPU. The VPS runs ONLY the always-on services
# (postgres + prefect-server + metabase, plus tp-backend from the dashboard
# repo). All crawl/dbt/LLM compute runs on GitHub Actions runners and reaches
# this box over Tailscale — see docs/gha-migration-runbook.md.
# ============================================================

echo "=== [1/7] Update system ==="
sudo apt-get update -y && sudo apt-get upgrade -y

echo "=== [2/7] Configure swap ==="
# A 4GB box running postgres(1G) + metabase(1G) + prefect(512M) + tp-backend
# sits close to the ceiling. Without swap the kernel OOM-killer picks the
# largest RSS — usually postgres — and kills the warehouse mid-write.
if ! sudo swapon --show | grep -q '/swapfile'; then
  sudo fallocate -l 4G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  # Prefer reclaiming page cache over swapping hot pages; only swap under real pressure.
  echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-talentpulse.conf
  sudo sysctl -p /etc/sysctl.d/99-talentpulse.conf
  echo "4GB swapfile created and enabled."
else
  echo "Swap already configured."
fi

echo "=== [3/7] Install Docker ==="
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  echo "Docker installed. You may need to re-login for group changes."
else
  echo "Docker already installed."
fi

if ! docker compose version &>/dev/null; then
  sudo apt-get install -y docker-compose-plugin
else
  echo "Docker Compose already installed."
fi

echo "=== [4/7] Install Tailscale ==="
# Every pipeline-*.yml GHA workflow reaches postgres/prefect/tp-backend over the
# tailnet. Without this, all 5 scheduled pipelines fail with connection timeouts.
if ! command -v tailscale &>/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
  echo "Tailscale installed — you must still run 'sudo tailscale up' (step 2 below)."
else
  echo "Tailscale already installed."
fi

echo "=== [5/7] Configure firewall (UFW) ==="
sudo ufw --force enable
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 3000/tcp   # Metabase UI

# Port 4200 (Prefect) is deliberately NOT opened. Prefect 2 OSS ships with no
# authentication whatsoever — a public 4200 lets anyone read your flow history
# and create/delete deployments. docker-compose.yml binds it to 127.0.0.1, and
# CI reaches it through `tailscale serve` (step 3 below). If an older run of
# this script opened it, close it now.
sudo ufw delete allow 4200/tcp 2>/dev/null || true

sudo ufw reload
echo "Firewall: SSH + Metabase (3000) open. Prefect (4200) and Postgres (5432) are loopback+tailnet only."

echo "=== [6/7] Create app directory ==="
APP_DIR="${APP_DIR:-/opt/talentpulse}"
sudo mkdir -p "$APP_DIR"
sudo chown "$USER":"$USER" "$APP_DIR"
echo "App directory ready at $APP_DIR"

echo "=== [7/7] Install GitHub Actions Runner ==="
RUNNER_DIR="${APP_DIR}/actions-runner"
mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

RUNNER_VERSION="2.321.0"
RUNNER_ARCH="linux-x64"
RUNNER_TAR="actions-runner-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"

if [ ! -f "./run.sh" ]; then
  curl -o "$RUNNER_TAR" -L "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_TAR}"
  tar xzf "$RUNNER_TAR"
  rm -f "$RUNNER_TAR"
  echo "Runner extracted to $RUNNER_DIR"
else
  echo "Runner already installed."
fi

echo ""
echo "============================================================"
echo " Setup complete! Manual steps that remain:"
echo ""
echo " 1. Register the GitHub Actions runner."
echo "    The label MUST be talentpulse_pa — deploy.yml targets"
echo "    'runs-on: [self-hosted, Linux, X64, talentpulse_pa]'. Any other"
echo "    label and the deploy job queues forever with no runner to pick it up."
echo ""
echo "    cd $RUNNER_DIR"
echo "    ./config.sh --url https://github.com/TalentPuse/TalentPulse_Pipeline_ETL \\"
echo "      --token <RUNNER_TOKEN> \\"
echo "      --labels talentpulse_pa \\"
echo "      --name talentpulse-vps \\"
echo "      --work $APP_DIR"
echo "    sudo ./svc.sh install && sudo ./svc.sh start"
echo ""
echo "    RUNNER_TOKEN: GitHub repo -> Settings -> Actions -> Runners -> New self-hosted runner"
echo ""
echo " 2. Join the tailnet:"
echo "      sudo tailscale up --advertise-tags=tag:vps"
echo "      tailscale ip -4      # -> record this 100.x.y.z as the VPS_TAILNET_IP variable"
echo ""
echo " 3. Bridge tailnet -> loopback for the 3 ports CI needs."
echo "    A '127.0.0.1:PORT' docker publish is NOT reachable over the tailnet on"
echo "    its own; without these the pipeline-*.yml workflows all time out."
echo "      tailscale serve --bg --tcp=5432 tcp://127.0.0.1:5432   # Postgres"
echo "      tailscale serve --bg --tcp=4200 tcp://127.0.0.1:4200   # Prefect"
echo "      tailscale serve --bg --tcp=8001 tcp://127.0.0.1:8001   # tp-backend"
echo ""
echo " 4. Add GitHub Secrets / Variables — see docs/gha-migration-runbook.md (c)"
echo "    for the full table. Required minimum:"
echo "      Secrets:   POSTGRES_USER POSTGRES_PASSWORD DB_USER DB_PASSWORD"
echo "                 TS_OAUTH_CLIENT_ID TS_OAUTH_SECRET"
echo "                 S3_ENDPOINT_URL S3_ACCESS_KEY S3_SECRET_KEY"
echo "                 ALERT_DISPATCH_SECRET TELEGRAM_WEBHOOK_SECRET"
echo "                 TELEGRAM_BOT_TOKEN OPENAI_API_KEY"
echo "      Variables: VPS_TAILNET_IP CRAWLER_CONTACT_EMAIL"
echo ""
echo " 5. Create the Cloudflare R2 bucket 'talentpulse-raw' by hand — the app"
echo "    does NOT create it on first run. See runbook (a)."
echo ""
echo " 6. Push to develop -> auto deploy."
echo "============================================================"
