# GitHub Actions Pipeline Migration — Operator Runbook

Companion to `docs/superpowers/specs/2026-07-06-pipeline-github-actions-migration-design.md`.
The repo-side artifacts (workflows, composite action, slimmed `docker-compose.yml`,
R2-tolerant `minio_client.py`) are already committed. This runbook covers the
steps that only a human with cloud/VPS access can do: create the R2 bucket,
install Tailscale, and load GitHub Secrets/Variables. Nothing here is
automatable by the agent that authored the workflows.

---

## (a) Cloudflare R2 — bucket + S3 API token

1. Cloudflare dashboard → **R2 Object Storage** → **Create bucket**.
   - Name: `talentpulse-raw` (matches the `S3_BUCKET_NAME` default baked into
     `src/utils/config.py` and the workflow `vars.S3_BUCKET_NAME` fallback).
   - Location: Automatic (or the region closest to your VPS/GHA runners).
2. **Manage R2 API Tokens** → **Create API token**.
   - Permissions: **Object Read & Write**, scoped to the `talentpulse-raw`
     bucket only (least privilege — do not grant account-wide R2 admin).
   - Save the **Access Key ID** and **Secret Access Key** — shown once.
3. Note your **Account ID** (R2 dashboard → right sidebar). The S3 endpoint is:
   ```
   https://<ACCOUNT_ID>.r2.cloudflarestorage.com
   ```
4. R2 does not support bucket creation via the S3 `CreateBucket` API call in
   all cases — that's why `src/storage/minio_client.py` was made tolerant of
   a failed `create_bucket` as long as `head_bucket` confirms the bucket
   already exists. **Because of this, step 1 (create the bucket in the
   dashboard) is mandatory** — don't rely on the app to create it on first
   run against R2.
5. Optional history migration: if you want to keep old MinIO raw objects,
   run once from a machine that can reach both:
   ```
   rclone copy minio:talentpulse-raw r2:talentpulse-raw
   ```
   If raw HTML is just a transient cache (re-crawled routinely anyway),
   skip this — the spec treats it as optional (§6).

---

## (b) Tailscale — VPS + CI OAuth client + ACL

### VPS side
1. Install Tailscale on the VPS:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up --advertise-tags=tag:vps
   ```
   (Requires `tag:vps` to already exist in your tailnet ACL — see below —
   or run `sudo tailscale up` first and assign the tag from the admin console.)
2. Get the VPS's tailnet IP:
   ```bash
   tailscale ip -4
   ```
   Record this **100.x.y.z** address — it is the `VPS_TAILNET_IP` GitHub
   Variable used by every `pipeline-*.yml` workflow.
3. **A `127.0.0.1:5432` docker publish is NOT reachable over the tailnet.**
   An earlier draft of this runbook claimed otherwise ("tailnet reaches it
   via the host's tailscaled routing") — that is false. Docker's port
   publish only installs a DNAT rule for packets whose *destination* is
   `127.0.0.1` (loopback); tailnet traffic arrives addressed to the VPS's
   `100.x.y.z` tailscale IP, which never matches that rule, so the
   connection is refused/times out regardless of ACLs. The same is true for
   any other service published as `127.0.0.1:<port>:<port>`.

   Instead, expose each service the CI job needs **explicitly over
   Tailscale**, keeping it off the public internet:
   ```bash
   tailscale serve --bg --tcp=5432 tcp://127.0.0.1:5432   # Postgres
   tailscale serve --bg --tcp=4200 tcp://127.0.0.1:4200   # Prefect API/UI
   tailscale serve --bg --tcp=8001 tcp://127.0.0.1:8001   # tp-backend (dashboard, for alert dispatch)
   ```
   `tailscale serve` listens on the tailnet interface (the VPS's `100.x.y.z`
   address) and forwards to the given loopback target — so it keeps the
   docker publish itself bound to loopback-only (nothing changes there) and
   simply bridges tailnet → loopback for these three ports. This is what
   makes the already-configured `DB_HOST`/`PREFECT_API_URL`/
   `DASHBOARD_API_URL` values (all `${{ vars.VPS_TAILNET_IP }}:<port>`) in
   the `pipeline-*.yml` workflows actually reach the VPS.

   Alternative (not recommended over the above): rebind the docker publish
   itself to the `tailscale0` interface IP (`<tailscale-ip>:5432:5432`
   instead of `127.0.0.1:5432:5432`) so the service listens directly on the
   tailnet. `tailscale serve` is preferred because it keeps the container
   bound to loopback (nothing to accidentally expose if the tailscale IP
   ever changes or the interface flaps) and is a single command to add/undo
   per service.

4. **Security action item — remove public exposure after cutover.** Today,
   Prefect (`4200`) and effectively Postgres (`5432`, via the loopback bind
   the app itself connects to) are reachable beyond the tailnet:
   `docker-compose.yml` publishes `4200:4200` (all interfaces, not just
   loopback) and `scripts/server-setup.sh` runs `sudo ufw allow 4200/tcp`,
   so Prefect's UI/API is currently open to the public internet. Once the
   tailnet path from item 3 is confirmed working end-to-end (§(d) step 2
   smoke test), **close this off**:
   - `sudo ufw delete allow 4200/tcp` on the VPS (removes the public
     firewall hole; CI and any human access to Prefect now go over Tailscale
     only, e.g. `tailscale serve` from item 3 plus `tailscale funnel`/`serve`
     locally, or just `ssh` + `localhost:4200` tunneling for humans).
   - Change `docker-compose.yml`'s Prefect port mapping from `"4200:4200"`
     to `"127.0.0.1:4200:4200"` (matching how Postgres is already bound) so
     a firewall misconfiguration alone can't re-expose it.
   - Do **not** publish `5432` or `4200` to `0.0.0.0` again for any reason
     tied to this migration — the GHA workflows only need the tailnet path.
   - If you deliberately *want* the Prefect UI reachable from the public
     internet (e.g. to check runs from a phone without Tailscale installed),
     that is a valid choice — but make it an explicit, documented decision
     (e.g. put it behind its own auth/reverse proxy), not the silent default
     it is today.

### GitHub Actions (CI) side
1. Tailscale admin console → **Settings → OAuth clients** → **Generate OAuth client**.
   - Scopes: `Devices: Write` (needed so ephemeral CI nodes can join/leave).
   - Tags: `tag:ci` (the client can only apply tags it's authorized for —
     add `tag:ci` under **Access controls → Tag owners** first if prompted).
2. Save the **Client ID** and **Client Secret** — these become the GitHub
   secrets `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_SECRET` (see §(c) below).
3. Edit the tailnet ACL (**Access controls**) to restrict `tag:ci` to only
   the ports these workflows actually need on the VPS (`tag:vps`):
   ```jsonc
   {
     "tagOwners": {
       "tag:ci":  ["autogroup:admin"],
       "tag:vps": ["autogroup:admin"]
     },
     "acls": [
       {
         "action": "accept",
         "src": ["tag:ci"],
         "dst": ["tag:vps:5432", "tag:vps:4200", "tag:vps:8001"]
       }
     ]
   }
   ```
   Port `8001` is `tp-backend` (dashboard API) — the `vnw_pipeline`,
   `itviec_pipeline`, and `linkedin_pipeline` flows all call
   `dispatch_dashboard_alerts()` as their last task (see
   `orchestration/flows/_shared.py`), and `alert_dispatch.py` calls it as its
   only task — so **all four** of those workflows need `:8001` reachability,
   not just `pipeline-alerts.yml`.
4. Each `tailscale/github-action@v3` step in the workflows joins the tailnet
   as an ephemeral CI node for the job's duration and disconnects
   automatically afterward — no persistent CI device to clean up.

---

## (c) GitHub Secrets & Variables to add

Repo → **Settings → Secrets and variables → Actions**. Prefer plain
repo-level Secrets/Variables (an `production` **Environment** works too if
you want a review gate on deploys — this repo's `deploy` job doesn't
currently target an environment, so it's optional here).

### Secrets (sensitive → `${{ secrets.* }}`)

| Name | Used by | Notes |
|---|---|---|
| `TS_OAUTH_CLIENT_ID` | all pipeline-*.yml, pipeline-migrate.yml | from §(b) |
| `TS_OAUTH_SECRET` | all pipeline-*.yml, pipeline-migrate.yml | from §(b) |
| `DB_USER` | all pipeline-*.yml, pipeline-migrate.yml | Postgres user (matches `POSTGRES_USER` on the VPS) |
| `DB_PASSWORD` | all pipeline-*.yml, pipeline-migrate.yml | Postgres password |
| `S3_ENDPOINT_URL` | pipeline-vnw/itviec/linkedin.yml | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` |
| `S3_ACCESS_KEY` | pipeline-vnw/itviec/linkedin.yml | R2 API token access key |
| `S3_SECRET_KEY` | pipeline-vnw/itviec/linkedin.yml | R2 API token secret |
| `ALERT_DISPATCH_SECRET` | pipeline-vnw/itviec/linkedin/alerts.yml | shared secret the dashboard backend's `/api/admin/alerts/dispatch-internal` checks |
| `TELEGRAM_WEBHOOK_SECRET` | pipeline-vnw/itviec/linkedin/alerts.yml | fallback if `ALERT_DISPATCH_SECRET` unset (see `_shared.py`) |
| `TELEGRAM_BOT_TOKEN` | pipeline-alerts.yml | carried over from the old deploy.yml `.env`; not read by any pipeline_data Python module today, kept for parity/future use |
| `OPENAI_API_KEY` | pipeline-skill-extraction.yml | LLM key — `configs/skill_extraction.yaml` `llm.api_key_env` names this var (the `base_url` in that file points at OpenRouter; despite the name, this key is an OpenRouter key, not an OpenAI one) |
| `GITHUB_TOKEN` | all workflows | automatic, no setup needed |
| *(existing, unchanged)* `POSTGRES_USER`, `POSTGRES_PASSWORD`, `PREFECT_UI_API_URL` | deploy.yml | already in use for the VPS deploy job |

### Variables (non-sensitive → `${{ vars.* }}`)

| Name | Default if unset | Notes |
|---|---|---|
| `VPS_TAILNET_IP` | *(none — required)* | the VPS's `100.x.y.z` tailnet address from §(b) |
| `DB_NAME` | `warehouse` | |
| `S3_BUCKET_NAME` | `talentpulse-raw` | |
| `CRAWLER_CONTACT_EMAIL` | *(none — set this)* | goes into the crawler's User-Agent string |
| `VNW_KEYWORDS` | `Data Engineer,AI Engineer,Business Analyst,Business Development,Technical Sales` | |
| `ITVIEC_KEYWORDS` | `data-engineer,ai-engineer,data-analyst` | |
| `ALLOWED_FUNCTION_IDS` | `25,27,129,130` | |
| `FOCUS_KEYWORDS` | `data engineer,data analyst,ai,machine learning,data science,data scientist,big data,analytics,business development,business analyst,technical sales` | |
| `SKIP_FOCUS_SOURCES` | `itviec` | |
| `LINKEDIN_KEYWORDS` | `Data Engineer,Data Analyst,AI Engineer,Data Scientist,Business Analyst` | |
| `LINKEDIN_TITLE_KEYWORDS` | same as `LINKEDIN_KEYWORDS` | |
| `LINKEDIN_GEO_ID` | `104195383` | |
| `LINKEDIN_RATE_SECONDS` | `3.0` | |

All the defaults above are wired as `${{ vars.X || 'default' }}` in the
workflow `env:` blocks, matching the `x-worker-env` anchor's old
`${VAR:-default}` compose defaults — so you only need to set the Variables
that should differ from those defaults (`VPS_TAILNET_IP` and
`CRAWLER_CONTACT_EMAIL` are the two you must set; everything else is
optional).

---

## (d) Rollout plan

1. **Prep** — complete (a), (b), (c) above. Confirm `docker compose config`
   still parses on the VPS after pulling the slimmed `docker-compose.yml`
   (don't deploy it yet).
2. **Smoke test (manual dispatch)** — for each of the 5
   `pipeline-*.yml` workflows, go to **Actions → <workflow> → Run workflow**
   (workflow_dispatch) one at a time. Verify per run:
   - The `tailscale/github-action@v3` step connects without error.
   - The run appears in the Prefect UI (VPS, existing `prefect-server`).
   - New rows land in Postgres (`bronze`/`silver`/`gold` as appropriate).
   - Raw objects appear in the R2 bucket (R2 dashboard → bucket → browse).
   - `alert_dispatch`/other flows' calls to `tp-backend:8001` succeed (check
     dashboard backend logs, or the flow's own `dispatch_alerts` artifact in
     Prefect for a `dispatched: N` result instead of an `error` field).
   - Run `pipeline-migrate.yml` once by hand too, confirm it applies cleanly
     against the existing schema (all `migrate_*.sql` are idempotent
     `IF NOT EXISTS`/`ADD COLUMN IF NOT EXISTS` style, and
     `seed_normalization_rules.py` uses `ON CONFLICT DO NOTHING`).
3. **Parallel run (1–2 days)** — turn on the GHA cron schedules (they're
   already defined in each workflow's `on.schedule`; no extra toggle needed
   once merged to `develop`) **while the VPS's 4 worker containers +
   `minio` are still running side by side** (don't deploy the slimmed
   compose file yet — keep running the current `docker-compose.yml` from
   before this change, or check out this commit's compose changes onto a
   separate branch only for the pipeline workflows to land first). Compare
   day-over-day:
   - `dbt_dev_gold.*` row counts (jobs, dedup counts) between the two paths.
   - Alert dispatch counts (`alert_log` table / Prefect artifact
     `dispatched` values) match what the VPS workers produced historically.
4. **Cutover** — once counts match for 1–2 consecutive days:
   - Merge/deploy the slimmed `docker-compose.yml` (this commit) so
     `deploy.yml`'s `docker compose up -d --remove-orphans` tears down
     `prefect-worker`, `alert-worker`, `itviec-worker`, `linkedin-worker`,
     and `minio` on the next push to `develop`.
   - Confirm `docker compose ps` on the VPS only shows `postgres`,
     `prefect-server`, `metabase` afterward.
5. **Watch (1 week)** — daily check: GHA Actions tab (all 5 pipeline runs
   green), Prefect UI (runs recorded, no missing days), gold table freshness,
   Telegram alerts still arriving. Also track **GHA billed minutes**
   (Settings → Billing → Actions) to feed the cost decision in §(f).
6. **Rollback** — if something's wrong post-cutover:
   ```bash
   git -C pipeline_data revert <cutover-commit-sha>   # restores 4 workers + minio in docker-compose.yml
   # on the VPS:
   docker compose up -d
   ```
   Point `S3_ENDPOINT_URL` back at `http://minio:9000` (old `.env` default)
   if R2 turns out to be the problem — the reverted compose file brings
   `minio` back with its old volume (`miniodata`) intact as long as the
   volume wasn't pruned. Disable the `pipeline-*.yml` schedules (or just
   leave them — the VPS workers and GHA cron running the same flow
   concurrently is wasteful but not unsafe, since the DB writes are
   idempotent) while you investigate.

---

## (e) Timezone — crons are UTC

Every `pipeline-*.yml` cron is documented **as UTC** with an inline comment
giving the equivalent Asia/Ho_Chi_Minh (UTC+7) time:

| Pipeline | UTC cron | VN time (UTC+7) |
|---|---|---|
| VietnamWorks | `0 2 * * *` | 09:00 |
| ITviec | `0 4 * * *` | 11:00 |
| LinkedIn | `0 6 * * *` | 13:00 |
| Skill extraction | `0 8 * * *` | 15:00 |
| Alerts | `0 7,12 * * *` | 14:00 and 19:00 |

These are the same wall-clock cron **numbers** as the old
`flow.serve(cron=...)` calls in the flow files — but the old Prefect
`.serve()` cron strings were **never explicitly timezone-qualified either**,
so if the VPS/Prefect process's local timezone was already UTC, behavior is
unchanged; if it was previously interpreted as VN local time, GHA's UTC
interpretation shifts every run **7 hours earlier in the UTC numbering**
(e.g. old "VN 09:00" would have been cron string `0 2 * * *` under a
VN-local Prefect scheduler, but under GHA's UTC-only scheduler `0 2 * * *`
now fires at **09:00 UTC = 16:00 VN**). **Verify the *old* actual fire times
in the Prefect UI run history before cutover**, and adjust the `cron:` lines
in the 5 `pipeline-*.yml` files if the numbers above don't land at your
intended VN wall-clock time.

---

## (f) Cost note

- GHA minutes: **private repo** → 2,000 free minutes/month (Linux), then
  ~$0.008/min. Rough estimate (measure for real during the parallel-run
  window in §(d) step 3): itviec (Playwright) and skill-extraction (LLM
  calls) are likely the longest jobs; if daily totals across all 5 workflows
  land around 1–2h/day, that's ~1,800–3,600 min/month — **likely over the
  free tier**.
- Mitigations, pick based on measured minutes:
  1. **Make the repo public** → unlimited free GHA minutes for public repos
     (weigh against exposing crawler source code).
  2. **Trim runtime**: lower `LISTING_MAX_PAGES`, tighten crawl scope,
     reduce LLM `max_concurrent`/`batch_size` in
     `configs/skill_extraction.yaml` only if it doesn't hurt data freshness.
  3. **Move the heaviest job (itviec) to the existing self-hosted runner**
     (`runs-on: [self-hosted, Linux, X64, talentpulse_pa]`, same label
     `deploy.yml`'s `deploy` job already uses) — 0 GHA minutes billed, still
     "on GitHub Actions" from a workflow-authoring standpoint. Swap
     `runs-on: ubuntu-latest` for that label in `pipeline-itviec.yml` if you
     go this route (note: the self-hosted runner then also needs Docker +
     Tailscale installed locally, and loses the "ephemeral, single-purpose
     hosted runner" isolation the GHA-hosted approach gives you).
  4. **Cache Playwright browsers between itviec runs** via
     `actions/cache` keyed on the Playwright version, if step 2 alone isn't
     enough — image already bakes in `playwright install chromium
     --with-deps` so image pull time dominates; this cuts container start
     latency but not job runtime once running.
- The primary savings from this whole migration is **downsizing the VPS**
  (no more 4 always-on worker containers + MinIO, ~4.5–5.5GB of RAM limits
  freed per the removed services' `deploy.resources.limits`) — weigh that
  against whatever GHA minutes cost turns out to be after real measurement.
