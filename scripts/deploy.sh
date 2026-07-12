#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# TalentPulse manual deploy script (fallback if CI/CD is down)
# Usage: bash scripts/deploy.sh
# ============================================================

APP_DIR="${APP_DIR:-/opt/talentpulse}"
cd "$APP_DIR"

echo "=== Pulling latest code ==="
git pull origin develop

echo "=== Pulling latest images ==="
docker compose pull

echo "=== Restarting services ==="
# --remove-orphans: an older VPS still has minio + 4 worker containers from
# the pre-GHA architecture. Left running they eat ~4.5GB on a 4GB box.
docker compose up -d --remove-orphans

echo "=== Cleaning up old images ==="
docker image prune -f

echo "=== Current status ==="
docker compose ps

echo ""
echo "Deploy complete!"
