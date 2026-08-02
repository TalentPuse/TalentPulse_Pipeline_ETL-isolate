# Hermes agent — skills and deployment

[Hermes Agent](https://github.com/NousResearch/hermes-agent) runs on the
warehouse box as a systemd service and watches this platform. Its skills live
**here, in the repo**, not on the box — so they are reviewable, versioned, and
shipped the same way as everything else.

## What is deployed where

| | Where | Note |
|---|---|---|
| Agent runtime | `/usr/local/lib/hermes-agent` on the warehouse box | installed by the official `install.sh` |
| Config + secrets | `/root/.hermes/config.yaml`, `/root/.hermes/.env` | never in git |
| **Skills** | **`hermes/skills/` in this repo** | symlinked into `/root/.hermes/skills/` |
| Agent workspace | `/opt/talentpulse/pipeline_data` | a dedicated clone of this repo |
| Service | `hermes-gateway.service` | `MemoryHigh=600M`, `MemoryMax=800M` |

The skills are consumed through symlinks:

```
/root/.hermes/skills/talentpulse-health   -> /opt/talentpulse/pipeline_data/hermes/skills/talentpulse-health
/root/.hermes/skills/talentpulse-platform -> /opt/talentpulse/pipeline_data/hermes/skills/talentpulse-platform
```

So updating a skill is: edit here → commit → push → `git -C /opt/talentpulse/pipeline_data pull`.
No copying files onto the box by hand, and no version of a skill that exists
only in one place.

## Why the workspace is not the runner checkout

The obvious place to point the agent at is the checkout the CI runner already
has, under `/home/github-runner/actions-runner/_work/…`. Do not.

`actions/checkout` **deletes the contents of that directory on every CI run**,
and the agent runs as root, so any file it leaves behind is root-owned and makes
the next checkout fail with `EACCES: permission denied`. That is not
hypothetical — it broke a deploy on 2026-08-02, on `orchestration/__pycache__/*.pyc`
left there by an older setup.

Hence a separate clone at `/opt/talentpulse/pipeline_data`, which nothing else
writes to.

## The skills

| Skill | Purpose |
|---|---|
| `talentpulse-platform` | Orientation: the two databases, the daily schedule, which tables matter, and the hard safety rules (never touch `app.*`, never `--full-refresh fct_jobs_daily`, never run dbt unprompted). |
| `talentpulse-health` | The daily watchdog: crawler freshness, stalled crawl queues, web-copy staleness, data-quality drift, table growth, box resources. |

Every threshold in `talentpulse-health` comes from a failure that actually
happened here and went unnoticed for days — TopCV dead for 7 days, LinkedIn's
queue stranding 3,684 rows, `job_category = 'Other'` at 51.6%. None of them
announced themselves; CI stayed green throughout. That is what the agent is for.

When you change a threshold, write down the incident that justifies it. A
number with no story behind it gets tuned away by the next person who sees a
false positive.

## Scheduled job

```
talentpulse-health-daily   0 14 * * * UTC   (21:00 VN, after the 20:00 backup)
```

Inspect with `hermes cron list`, run on demand with:

```bash
cd /opt/talentpulse/pipeline_data
hermes -z "Run the talentpulse-health daily check and report."
```

## Known rough edges

- The agent runs **as root**. Hermes itself refuses this by default and had to be
  overridden with `--run-as-user root`, because the install already lived under
  `/root/.hermes`. Moving it to a dedicated unprivileged user is the right fix
  and is not yet done.
- It reads shell output imperfectly. It once reported `free` (325 MB) instead of
  `available` (2,053 MB) from `free -m` and raised a low-memory alert on a box
  with 2 GB spare. The skill now pins the exact command and column. Read its
  reports with that in mind for the first few weeks.
