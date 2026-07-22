#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# TalentPulse VPS setup — run once on a fresh Ubuntu 22.04 server.
#
#   bash scripts/server-setup.sh warehouse    # postgres + prefect + metabase
#   bash scripts/server-setup.sh web          # tp-backend + frontend + mcp + latex
#
# The two roles live on SEPARATE boxes and reach each other over Tailscale. That
# separation is the point: a warehouse problem — a runaway dbt query, an OOM, a
# Metabase restart — must not take the public website down with it.
#
# Shared bits (swap, docker, tailscale) run for both roles. The firewall and the
# `tailscale serve` bridges differ, because each box should expose only the ports
# the other side actually calls.
# ============================================================

ROLE="${1:-}"
if [[ "$ROLE" != "warehouse" && "$ROLE" != "web" ]]; then
  echo "usage: $0 <warehouse|web>" >&2
  exit 1
fi
echo "=== Setting up this box as: ${ROLE} ==="

echo "=== [1/6] Update system ==="
sudo apt-get update -y && sudo apt-get upgrade -y

echo "=== [2/6] Configure swap ==="
# Both boxes are 4GB and run close to the ceiling. Without swap the kernel
# OOM-killer picks the largest RSS — postgres on the warehouse box, the backend on
# the web box — and kills it mid-request.
if ! sudo swapon --show | grep -q '/swapfile'; then
  sudo fallocate -l 4G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-talentpulse.conf
  sudo sysctl -p /etc/sysctl.d/99-talentpulse.conf
  echo "4GB swapfile enabled."
else
  echo "Swap already configured."
fi

echo "=== [3/6] Install Docker ==="
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  echo "Docker installed. Re-login for group changes to apply."
else
  echo "Docker already installed."
fi
if ! docker compose version &>/dev/null; then
  sudo apt-get install -y docker-compose-plugin
fi

echo "=== [4/6] Install Tailscale ==="
# This is the ONLY path between the two boxes, and the only path the GHA runners
# use. Without it the web box cannot reach the database and every pipeline times
# out.
if ! command -v tailscale &>/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
else
  echo "Tailscale already installed."
fi

echo "=== [5/6] Configure firewall (UFW) ==="
sudo ufw --force enable
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh

if [[ "$ROLE" == "warehouse" ]]; then
  sudo ufw allow 3000/tcp   # Metabase UI (browser access)

  # 5432 and 4200 are deliberately NOT opened to the internet. Prefect 2 OSS ships
  # with NO authentication at all, so a public 4200 lets anyone read your flow
  # history and create or delete deployments. Both are bound to 127.0.0.1 in
  # docker-compose.yml and bridged over the tailnet below.
  sudo ufw delete allow 4200/tcp 2>/dev/null || true
else
  echo "web role: no extra ports opened. Add 80/443 only if TLS terminates here"
  echo "rather than at Cloudflare / a reverse proxy."
fi
sudo ufw reload

echo "=== [6/6] App dir + GitHub Actions runner ==="
APP_DIR="${APP_DIR:-/opt/talentpulse}"
sudo mkdir -p "$APP_DIR"
sudo chown "$USER":"$USER" "$APP_DIR"

if [[ "$ROLE" == "warehouse" ]]; then
  RUNNER_DIR="${APP_DIR}/actions-runner"
  mkdir -p "$RUNNER_DIR"
  cd "$RUNNER_DIR"
  RUNNER_VERSION="2.321.0"
  RUNNER_TAR="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
  if [ ! -f "./run.sh" ]; then
    curl -o "$RUNNER_TAR" -L "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_TAR}"
    tar xzf "$RUNNER_TAR"
    rm -f "$RUNNER_TAR"
  fi
  echo "Runner extracted to $RUNNER_DIR"
else
  echo "web role: pipeline_data's deploy.yml does not target this box — the"
  echo "dashboard repo deploys here with its own workflow."
fi

echo ""
echo "============================================================"
echo " ${ROLE} box: automated part done. Remaining MANUAL steps:"
echo ""
echo " 1. Join the tailnet. The device MUST end up tagged tag:${ROLE} — the ACL keys"
echo "    off it, so an untagged box is unreachable by CI even once it's up."
if [[ "$ROLE" == "warehouse" ]]; then
  echo "      # non-interactive: create the auth key WITH tag:warehouse (admin -> Auth keys"
  echo "      # -> Tags); it then tags the box automatically, no --advertise-tags needed:"
  echo "      sudo tailscale up --auth-key=tskey-auth-XXXX"
  echo "      # or interactive (approve the tag in a browser):"
  echo "      sudo tailscale up --advertise-tags=tag:warehouse"
else
  echo "      # non-interactive: create the auth key WITH tag:web (admin -> Auth keys"
  echo "      # -> Tags); it then tags the box automatically, no --advertise-tags needed:"
  echo "      sudo tailscale up --auth-key=tskey-auth-XXXX"
  echo "      # or interactive (approve the tag in a browser):"
  echo "      sudo tailscale up --advertise-tags=tag:web"
fi
echo "      tailscale ip -4     # record this 100.x.y.z"
echo ""
echo " 2. Bridge tailnet -> loopback. A '127.0.0.1:PORT' docker publish is NOT"
echo "    reachable over the tailnet on its own — without these, the other box"
echo "    and the GHA runners all time out."
if [[ "$ROLE" == "warehouse" ]]; then
  echo "      tailscale serve --bg --tcp=5432 tcp://127.0.0.1:5432   # Postgres"
  echo "      tailscale serve --bg --tcp=4200 tcp://127.0.0.1:4200   # Prefect"
  echo ""
  echo "    This box's tailnet IP is the WAREHOUSE_TAILNET_IP GitHub Variable,"
  echo "    and it is the DB_HOST the web box must use to reach Postgres."
else
  echo "      tailscale serve --bg --tcp=8001 tcp://127.0.0.1:8001   # tp-backend"
  echo ""
  echo "    This box's tailnet IP is the WEB_TAILNET_IP GitHub Variable — the"
  echo "    pipelines POST alerts to it."
fi
echo ""
if [[ "$ROLE" == "warehouse" ]]; then
  echo " 3. Register the GitHub Actions runner. The label MUST be talentpulse_pa —"
  echo "    deploy.yml targets 'runs-on: [self-hosted, Linux, X64, talentpulse_pa]'"
  echo "    and will queue forever under any other label."
  echo "      cd ${APP_DIR}/actions-runner"
  echo "      ./config.sh --url https://github.com/TalentPuse/TalentPulse_Pipeline_ETL \\"
  echo "        --token <RUNNER_TOKEN> --labels talentpulse_pa \\"
  echo "        --name talentpulse-warehouse --work $APP_DIR"
  echo "      sudo ./svc.sh install && sudo ./svc.sh start"
  echo ""
  echo " 4. Create the Cloudflare R2 buckets by hand (the app does NOT create them):"
  echo "      talentpulse-raw      — crawled HTML"
  echo "      talentpulse-backup   — pg_dump + parquet"
fi
echo ""
echo " Tailnet ACL — each side reaches only what it actually calls:"
echo '   {"action":"accept","src":["tag:web"],"dst":["tag:warehouse:5432"]},'
echo '   {"action":"accept","src":["tag:ci"], "dst":["tag:warehouse:5432","tag:warehouse:4200","tag:web:8001"]}'
echo "============================================================"
