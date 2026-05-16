#!/bin/bash
set -euo pipefail

LOGFILE=/var/log/talentpulse/daily-$(date +%F).log
mkdir -p "$(dirname "$LOGFILE")"

{
  echo "=== START $(date -Iseconds) ==="

  echo "[1/5] Crawling listings..."
  python -m src.crawlers.vietnamworks.listing

  echo "[2/5] Crawling details..."
  python -m src.crawlers.vietnamworks.detail.main

  echo "[3/5] Loading to Postgres..."
  python -m src.loaders.job_detail_loader

  echo "[4/5] Running dbt..."
  cd dbt_transform
  dbt build
  cd ..

  echo "[5/5] Dispatching alerts..."
  bash scripts/dispatch_alerts.sh

  echo "=== END $(date -Iseconds) ==="
} >> "$LOGFILE" 2>&1
