---
name: talentpulse-health
description: Daily health check for the TalentPulse data platform — detects silent pipeline failures, stalled crawl queues, a stale web copy, data-quality drift, and box resource pressure. Use for the scheduled daily report, or whenever asked whether the platform is healthy.
license: MIT
metadata:
  hermes:
    tags: [TalentPulse, monitoring, health check, Postgres, data quality]
---

# TalentPulse health check

Read `talentpulse-platform` first for the database layout and the safety rules.

**Everything here is read-only. Run SELECT only.** If a check fires, report it —
do not fix it yourself unless explicitly asked to.

## Why these checks and not others

Every threshold below comes from a failure that actually happened and that
nobody noticed for days. This platform does not fail loudly. It fails quietly
and keeps serving stale data while every workflow reports green. The job is to
make the quiet failures visible.

## The checks

Run each, then produce ONE short report. Lead with whatever fired; if nothing
fired, say so in one line and give the headline numbers.

### 1. Source freshness — did a crawler die?

```sql
SELECT source, max(parsed_at) AS newest, (now() - max(parsed_at)) AS age
FROM raw.job_detail GROUP BY 1 ORDER BY 2;
```

**FIRE if any source has age > 48h.** TopCV went dead for 7 days — Cloudflare
blocks datacenter IPs — and the workflow kept reporting success because the
crawl "completed" with zero results. Green CI is not evidence of a working
crawler.

### 2. Crawl queue stalls

```sql
SELECT source, status, count(*) FROM raw.crawl_log GROUP BY 1,2 ORDER BY 1,3 DESC;

SELECT source, count(*) FROM raw.crawl_log
WHERE status='in_progress' AND last_attempt_at < now() - interval '120 minutes'
GROUP BY 1;
```

**FIRE if stuck `in_progress` > 500 for any source.** LinkedIn hit 3,684 and grew
to 3,882 the next day: Prefect restarted a long crawl after a heartbeat lapse
while the first attempt was still alive, and the zombie drained the queue,
claiming rows and abandoning them. Also **FIRE if `failed` > `success`** for a
source — that is a blocked crawler, not bad luck.

### 3. Is the web copy stale?

```sql
-- against WEB_DSN
SELECT count(*), max(snapshot_date) FROM dbt_dev_gold.fct_jobs_daily;
```

**FIRE if `max(snapshot_date) < current_date - 1`.** The sync runs at 16:00 VN;
when its password drifted it failed every single day in silence and the website
kept serving whatever it already had. Compare row counts against the warehouse
too — a large gap means a partial sync, which is worse than none because it
looks fine.

### 4. Data-quality drift

```sql
SELECT round(100.0*count(*) FILTER (WHERE job_category='Other')/count(*),1) AS pct_other,
       round(100.0*count(*) FILTER (WHERE city_canonical IS NULL)/count(*),1) AS pct_no_city,
       count(*) AS total
FROM dbt_dev_silver.silver_job_detail;
```

**FIRE if `pct_other` > 35 or `pct_no_city` > 15.** Both are drift detectors:
new job titles or new location formats that the seed maps do not cover yet.
`pct_other` sat at 51.6% before the seeds were extended — half the board was
unclassified and the dashboard simply showed no category for it.

### 5. Table growth

```sql
SELECT count(*) AS rows, count(DISTINCT run_id) AS runs
FROM normalization.job_normalization;
```

**FIRE if rows > 20,000.** The table is append-only and is pruned to the newest
3 runs. It once reached 100,186 rows describing 4,383 real jobs — the
second-largest table in the warehouse, and every stale generation made the
silver view slower.

### 6. LLM skill-extraction coverage

```sql
SELECT (SELECT count(*) FROM raw.skill_extraction_log) AS done,
       (SELECT count(*) FROM raw.job_detail) AS total;
```

**FIRE if done/total < 0.5.** It is incremental and should catch up on its own;
sitting at 21% means it is stalling or erroring out mid-batch.

### 7. Postings that never expire

```sql
SELECT source, count(*) FROM raw.job_detail
WHERE expired_at IS NULL AND posted_at < now() - interval '45 days'
GROUP BY 1;
```

**FIRE if > 300.** LinkedIn publishes no expiry date at all, so without a
fallback every posting stays "currently hiring" forever — 68 of them were more
than 180 days old, on a live job board.

### 8. Box resources

Use exactly this — it prints the one number that matters and nothing else:

```bash
free -m | awk 'NR==2{print "available_mb="$7}'
df -h / | awk 'NR==2{print "disk_used="$5}'
docker stats --no-stream --format "{{.Name}} {{.MemUsage}}"
```

**FIRE if `available_mb` < 400 or disk > 85%.**

Read the **`available`** column (field 7), never `free` (field 4). They are not
the same thing and the difference is not small: this box has shown
`free=325MB` while `available=2053MB` at the same instant, because Linux counts
reclaimable page cache as used. Reporting `free` produces a panicked alert about
a box that has 2GB spare — that false positive has already happened once here.

Why it matters at all: 3.8GB is shared between Postgres, a Metabase JVM, Prefect
and this agent. Swap exists now, but if the kernel starts OOM-killing, the victim
it picks is usually Postgres — the entire warehouse — not whatever caused the
pressure.

## Report format

Keep it under 20 lines.

```
TalentPulse health — <date>
[ALERT] <what fired, the number, and what it means>      ← omit when nothing fired
OK: jobs <n> (<per-source>) | web copy <date> | Other <n>% | RAM <n>MB free
```

Numbers, not adjectives. When something fires, add one line on what a human
should do about it — and stop there. Do not fix it yourself.
