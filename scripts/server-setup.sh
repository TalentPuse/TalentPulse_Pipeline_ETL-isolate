#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# TalentPulse VPS initial setup script
# Run once on a fresh Ubuntu 22.04 server:
#   bash scripts/server-setup.sh
# ============================================================

echo "=== [1/6] Update system ==="
sudo apt-get update -y && sudo apt-get upgrade -y

echo "=== [2/6] Install Docker ==="
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  echo "Docker installed. You may need to re-login for group changes."
else
  echo "Docker already installed."
fi

echo "=== [3/6] Install Docker Compose plugin ==="
if ! docker compose version &>/dev/null; then
  sudo apt-get install -y docker-compose-plugin
else
  echo "Docker Compose already installed."
fi

echo "=== [4/6] Configure firewall (UFW) ==="
sudo ufw --force enable
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 3000/tcp   # Metabase
sudo ufw allow 4200/tcp   # Prefect UI
sudo ufw reload
echo "Firewall configured: SSH + Metabase (3000) + Prefect (4200) open."
echo "PostgreSQL (5432), MinIO (9000/9001) are bound to 127.0.0.1 only."

echo "=== [5/6] Create app directory ==="
APP_DIR="${APP_DIR:-/opt/talentpulse}"
sudo mkdir -p "$APP_DIR"
sudo chown "$USER":"$USER" "$APP_DIR"
echo "App directory ready at $APP_DIR"

echo "=== [6/6] Install GitHub Actions Runner ==="
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
echo " Setup complete! Next steps:"
echo ""
echo " 1. Register the GitHub Actions runner:"
echo "    cd $RUNNER_DIR"
echo "    ./config.sh --url https://github.com/<owner>/<repo> \\"
echo "      --token <RUNNER_TOKEN> \\"
echo "      --labels talentpulse \\"
echo "      --name talentpulse-vps \\"
echo "      --work $APP_DIR"
echo ""
echo "    Get your RUNNER_TOKEN from:"
echo "    GitHub repo -> Settings -> Actions -> Runners -> New self-hosted runner"
echo ""
echo " 2. Install runner as a service (auto-start on boot):"
echo "    sudo ./svc.sh install"
echo "    sudo ./svc.sh start"
echo ""
echo " 3. Add secrets in GitHub repo -> Settings -> Secrets:"
echo "    - POSTGRES_USER"
echo "    - POSTGRES_PASSWORD"
echo "    - MINIO_ROOT_USER"
echo "    - MINIO_ROOT_PASSWORD"
echo "    - TELEGRAM_BOT_TOKEN"
echo ""
echo " 4. Add variables in GitHub repo -> Settings -> Variables:"
echo "    - DB_NAME (default: warehouse)"
echo "    - S3_BUCKET_NAME (default: talentpulse-raw)"
echo "    - PREFECT_UI_API_URL (e.g. http://YOUR_IP:4200/api)"
echo "    - CRAWLER_CONTACT_EMAIL"
echo ""
echo " 5. Push to develop branch -> auto deploy!"
echo "============================================================"
