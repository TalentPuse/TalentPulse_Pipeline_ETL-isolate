#!/bin/bash
# Dispatch job alerts via the dashboard API.
# Usage: ./dispatch_alerts.sh
# Called by cron or daily_run.sh after dbt completes.
set -euo pipefail

API_URL="${DASHBOARD_API_URL:-http://tp-backend:8001}"
SECRET="${TELEGRAM_WEBHOOK_SECRET:-${ALERT_DISPATCH_SECRET:-dev-webhook-secret}}"

echo "$(date -Iseconds) Dispatching alerts via $API_URL ..."

HTTP_CODE=$(curl -s -o /tmp/tp-alert-response.json -w "%{http_code}" \
    -X POST \
    -H "X-Webhook-Secret: $SECRET" \
    -H "Content-Type: application/json" \
    "${API_URL}/api/admin/alerts/dispatch-internal" \
    --max-time 120)

if [ "$HTTP_CODE" -eq 200 ]; then
    DISPATCHED=$(python3 -c "import json; print(json.load(open('/tmp/tp-alert-response.json')).get('dispatched', '?'))" 2>/dev/null || echo "?")
    echo "$(date -Iseconds) OK — dispatched=$DISPATCHED"
else
    BODY=$(cat /tmp/tp-alert-response.json 2>/dev/null || echo "no body")
    echo "$(date -Iseconds) FAIL — HTTP $HTTP_CODE: $BODY" >&2
    exit 1
fi
