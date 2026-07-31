---
name: pipeline-cicd
description: How this repo builds, deploys, and schedules — GitHub Actions workflows, the run-flow composite action, Docker images, Tailscale, and the secrets/vars matrix. Use when editing anything under .github/, adding a scheduled pipeline, changing a cron time or job timeout, or wiring a new environment variable through to a flow.
---

# CI/CD and scheduling

## Two kinds of workflow

**`deploy.yml`** — on push/PR to `develop`. Jobs: `test` (pytest) →
`build-and-push` (4 images to GHCR via a matrix) → `deploy` (self-hosted runner
`talentpulse_pa`, writes a runtime `.env` and runs `docker compose up -d
--remove-orphans`).

`docker-compose.yml` on the VPS runs **only** `postgres`, `metabase`, and
`prefect-server`. The worker images are not deployed there — they are pulled and
run by the scheduled workflows below. `build-and-push` still builds them so those
workflows have something to pull.

**`pipeline-*.yml`** — cron + `workflow_dispatch`. One per pipeline: `vnw`,
`itviec`, `linkedin`, `topcv`, `skill-extraction`, `alerts`, `backup`, `migrate`.
Each declares its `env:` block and delegates to the composite action.

## The composite action

`.github/actions/run-flow/action.yml` holds all the Tailscale + GHCR + docker
wiring in one place. It: joins the tailnet (`tag:ci`), logs into GHCR, then
`docker run --rm <image> python -m <module>`.

Two rules:

- **Env vars are forwarded with bare `-e VAR`** (no `=value`), which copies the
  value from the runner's shell. So a new variable must be added in **two** places:
  the calling workflow's `env:` block *and* the `-e` list in the action. Missing
  either one and the container gets nothing, silently.
- **`PREFECT_DEPLOY` is deliberately never forwarded.** Setting it turns a
  run-once job into a process that registers a cron deployment and blocks forever.

Composite actions cannot read `secrets.*`, so Tailscale OAuth and the GHCR token
are passed in as explicit inputs.

## Cron is UTC; the product is UTC+7

`"0 2 * * *"` = 09:00 Asia/Ho_Chi_Minh. Current daily order: VNW 09:00 → ITviec
11:00 → LinkedIn 13:00 → alerts 14:00 → skill-extraction 15:00 → sync-to-web after
that → backup last, so it captures a full day. Changing one time means checking the
whole chain — several stages assume upstream is finished.

GitHub only (re)registers `schedule:` workflows when the file is added or modified
on the default branch; some files carry a trailing comment purely to force that.

## Timeout ordering

`timeout-minutes` (workflow) > Prefect task `timeout_seconds` > the crawler's own
internal budget. Break the order and the job dies mid-crawl with nothing
downstream running. See the `prefect-flows` skill.

## Two tailnet addresses, not one

- `vars.WAREHOUSE_TAILNET_IP` — Postgres (5432) and Prefect (4200)
- `vars.WEB_TAILNET_IP` — the dashboard API (`DASHBOARD_API_URL`, port 8001), which
  the final `dispatch_alerts` stage POSTs to

Pointing both at one IP is what coupled the two boxes before the split.

Prefect 2 OSS has no authentication, which is why compose binds it to
`127.0.0.1:4200` and CI reaches it over the tailnet. Do not publish `0.0.0.0:4200`.

## Git rules for this repo

**Canonical remote is `isolate` → `TalentPuse/TalentPulse_Pipeline_ETL-isolate`.
Push with `git push isolate develop`, explicitly.**

`develop` still *tracks* `origin/develop`, and `origin` points at the older
non-isolate repo — so a bare `git push` here goes to the **wrong repository** and
`git status` will happily tell you that you are "up to date" with it. Check
`git remote -v` before pushing; do not infer the target from the tracking branch.
This went wrong on 2026-07-31: two commits landed on `origin/develop` before being
pushed to `isolate` (left in place deliberately rather than force-rewritten).

The canonical repo has **no `main` branch** — the local `main` is an orphan left
from the old fork; do not use it. Deploys happen through CI, not by hand.

## Secrets vs vars

Secrets: `DB_USER`, `DB_PASSWORD`, `S3_*`, `ALERT_DISPATCH_SECRET`,
`TELEGRAM_WEBHOOK_SECRET`, `OPENAI_API_KEY`, `TS_OAUTH_*`, `POSTGRES_*`.
Vars: `WAREHOUSE_TAILNET_IP`, `WEB_TAILNET_IP`, `DB_NAME`, `S3_BUCKET_NAME`,
`CRAWLER_CONTACT_EMAIL`.

Note that several `vars.*_KEYWORDS` and `vars.ALLOWED_FUNCTION_IDS` are still
passed by the workflows but are **inert** — `src/utils/config.py` hardcodes
`CRAWL_KEYWORDS` and leaves `ALLOWED_FUNCTION_IDS` empty on purpose. Do not change
crawl scope by editing a GitHub variable; edit the config.

The authoritative deployment runbook is `../docs/DEPLOY.md` at the workspace root.
`docs/gha-migration-runbook.md` in this repo is single-VPS and stale for topology.
