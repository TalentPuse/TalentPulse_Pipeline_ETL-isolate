# Prefect Orchestration Setup

Wires TalentPulse pipeline (listing → seed → crawl → parse → load) into 1 Prefect flow.

## Stack

```
┌────────────────────────────────────────────────────────────┐
│  Browser  →  http://localhost:4200 (Prefect UI)            │
├────────────────────────────────────────────────────────────┤
│  prefect-server (port 4200) ──┐                            │
│  prefect-worker (background)  ─┤ share network             │
│  postgres (prefect db)         │                           │
│  postgres (warehouse db) ←─────┘ workers read/write here   │
│  minio        ←─────────────────── workers upload here     │
└────────────────────────────────────────────────────────────┘
```

## First-time setup

```bash
# 1. Create the prefect database (one-off)
docker exec talentpulse-postgres psql -U admin -d warehouse -c "CREATE DATABASE prefect"

# 2. Build and start the orchestration stack
docker compose up -d --build prefect-server prefect-worker

# 3. Wait ~5s then verify
curl http://localhost:4200/api/health   # expect HTTP 200
docker logs talentpulse-prefect-worker | tail -5
# expect: Worker 'ProcessWorker ...' started!
#         Work pool 'default-agent-pool' created.

# 4. Register the deployment (one-off; re-run if flow signature changes)
docker exec talentpulse-prefect-worker bash -c \
  "cd /app && prefect deployment build \
    orchestration/flows/vnw_pipeline.py:vnw_pipeline \
    -n vnw-manual -p default-agent-pool --apply"
```

Open **http://localhost:4200** → **Deployments** → see `vnw-pipeline/vnw-manual`.

## Trigger a run

### Via UI (recommended)
1. http://localhost:4200 → **Deployments**
2. Click `vnw-pipeline/vnw-manual` → **Run** → **Custom run**
3. Fill parameters (all optional):
   - `keywords`: default `["Data Engineer", "AI Engineer"]`
   - `listing_pages`: default `1`
   - `detail_max_jobs`: cap detail batch (None = all pending)
   - `force_reparse`: reparse even if JSON exists
4. **Submit** → watch live in **Flow Runs**

### Via CLI
```bash
docker exec talentpulse-prefect-worker \
  prefect deployment run "vnw-pipeline/vnw-manual" \
  --param detail_max_jobs=10
```

### Direct (bypass Prefect — for debugging)
```bash
docker exec talentpulse-prefect-worker python orchestration/flows/vnw_pipeline.py
```

## Flow structure

5 tasks, each with independent retry policy:

| Task | Retries | Timeout | Purpose |
|---|---|---|---|
| `listing_crawl` | 2 (60s delay) | — | POST to VNW search API, upload to MinIO |
| `seed_queue` | 1 | — | Scan MinIO listings, enqueue into `raw.crawl_log` |
| `detail_crawl` | 2 (120s delay) | 1h | Claim pending, fetch HTML.gz, rate-limited |
| `detail_parse` | 1 | — | Decode RSC payload → parsed JSON in MinIO |
| `load_warehouse` | 2 | — | UPSERT into `raw.job_detail` |

All tasks reuse existing modules (`src.crawlers.*`, `src.parsers.*`, etc.) — **no business logic duplication**.

## Add a schedule (optional, later)

In UI: **Deployments → vnw-manual → Schedules → Add**. Pick cron/interval. E.g. `0 2 * * *` = 02:00 daily VN time.

Zero code changes required.

## Troubleshooting

**Worker logs show `ConnectError: All connection attempts failed`**
- Server not ready yet. `docker logs talentpulse-prefect-server | tail` and wait until you see `Uvicorn running on http://0.0.0.0:4200`.

**Deployment missing after compose restart**
- Prefect stores deployments in DB, so it persists. If `prefect deployment ls` empty, re-run the `prefect deployment build ... --apply` command.

**Flow runs stuck in `Scheduled` state**
- Worker not running or not attached to `default-agent-pool`. Check `docker ps | grep prefect-worker`.

**Import errors in worker**
- Worker doesn't see fresh code. Volumes `./src` and `./orchestration` are mounted read-only. Restart worker: `docker compose restart prefect-worker`.

**Need to reinstall deps inside worker**
- Edit `orchestration/worker-requirements.txt` → `docker compose build prefect-worker --no-cache && docker compose up -d prefect-worker`.

## What lives where

| File | Role |
|---|---|
| `docker-compose.yml` | `prefect-server`, `prefect-worker` service defs |
| `orchestration/Dockerfile.worker` | Worker image (Prefect base + deps + app code) |
| `orchestration/worker-requirements.txt` | Trimmed deps (no playwright/dbt/pandera — not needed at runtime) |
| `orchestration/flows/vnw_pipeline.py` | The flow + 5 tasks |
| `/app/vnw_pipeline-deployment.yaml` (inside worker) | Generated deployment spec |

## Before production

- [ ] Rotate default Postgres password (used by Prefect DB connection)
- [ ] Add Prefect Automation: email/Discord on `Failed`/`Crashed` state
- [ ] Move secrets (`DB_PASSWORD`, `S3_SECRET_KEY`) to Docker secrets or Prefect Blocks
- [ ] Put Caddy/Traefik in front if exposing UI publicly (currently bind to `0.0.0.0:4200`)
- [ ] Tune `retries`/`timeout_seconds` after observing real-world failure modes
