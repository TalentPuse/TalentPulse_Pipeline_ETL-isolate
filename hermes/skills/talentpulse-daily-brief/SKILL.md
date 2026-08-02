---
name: talentpulse-daily-brief
description: Morning brief on the TalentPulse job board — which pipelines actually completed overnight, how many jobs came in, and how the numbers moved versus yesterday. Use for the scheduled 08:00 report or when asked "what happened overnight".
license: MIT
metadata:
  hermes:
    tags: [TalentPulse, daily report, Prefect, Postgres, metrics]
---

# TalentPulse daily brief

Read `talentpulse-platform` first for the database layout and the safety rules.
**Read-only: SELECT and GET only.** Never modify anything, never run dbt.

## The one design rule

**Report what MOVED, not what IS.** A daily message that repeats the same
absolute numbers gets skimmed after a week and ignored after two. Every number
below is paired with a delta or a baseline, and the report leads with whatever
needs a decision. If nothing moved, say so in two lines and stop.

Day-over-day deltas do not need any stored state: `dbt_dev_gold.fct_jobs_daily`
keeps one row per job per `snapshot_date`, so yesterday is a `WHERE` clause away.

## 1. Did the pipelines actually run? (Prefect, not CI)

This is the check nothing else does. GitHub Actions can report green while a
flow was killed mid-run — the job "completed" from CI's point of view, and
Prefect simply never receives a terminal state. Those runs sit in `RUNNING`
forever. On 2026-08-02 `vnw-pipeline` had two such runs and zero completions,
while the data quietly went stale.

```bash
python3 - <<'PY'
import json, urllib.request
from collections import defaultdict
def post(p, b):
    r = urllib.request.Request("http://127.0.0.1:4200/api"+p, data=json.dumps(b).encode(),
                               headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=20))
flows = {f["id"]: f["name"] for f in post("/flows/filter", {"limit": 50})}
runs = post("/flow_runs/filter", {"limit": 100, "sort": "START_TIME_DESC",
        "flow_runs": {"start_time": {"after_": "<ISO timestamp 24h ago>"}}})
agg = defaultdict(lambda: defaultdict(int))
for r in runs:
    agg[flows.get(r["flow_id"], "?")][str(r.get("state_type"))] += 1
for name, st in sorted(agg.items()):
    print(name, dict(st))
PY
```

Report per flow: completed / failed / still RUNNING.

- **A flow with 0 COMPLETED in 24h is the headline**, whatever else the numbers say.
- **RUNNING for more than 2 hours means it died**, not that it is busy. Nothing
  here legitimately runs that long except the crawl pipelines, and those have
  their own internal deadlines.

Expected daily: `vnw-pipeline`, `itviec-pipeline`, `topcv-pipeline`,
`linkedin-pipeline`, `skill-extraction-pipeline`, `sync-to-web`,
`warehouse-backup` once each, `alert-dispatch` twice.

## 2. Intake — how many jobs arrived

```sql
SELECT source,
       count(*) FILTER (WHERE parsed_at >= now() - interval '24 hours') AS last_24h,
       round(count(*) FILTER (WHERE parsed_at >= now() - interval '7 days')/7.0) AS avg_per_day_7d
FROM raw.job_detail GROUP BY 1 ORDER BY 2 DESC;
```

Flag any source whose 24h intake is **below half its 7-day average** — that is a
crawler degrading before it fails outright, which is the cheapest moment to catch it.

## 3. Inventory — the number the website shows

```sql
SELECT snapshot_date,
       count(*) AS jobs,
       count(*) FILTER (WHERE is_active) AS active
FROM dbt_dev_gold.fct_jobs_daily
WHERE snapshot_date >= current_date - 2
GROUP BY 1 ORDER BY 1 DESC;
```

Report today vs yesterday with the delta. A **drop in `active`** is worth a
sentence: it is either postings ageing out normally, or a crawler that stopped.
Section 2 tells you which.

## 4. Quality, with yesterday's value beside it

```sql
SELECT snapshot_date,
       round(100.0*count(*) FILTER (WHERE job_category='Other')/count(*),1)   AS pct_other,
       round(100.0*count(*) FILTER (WHERE city_canonical IS NULL)/count(*),1) AS pct_no_city,
       round(100.0*count(*) FILTER (WHERE salary_vnd_monthly_avg IS NOT NULL)/count(*),1) AS pct_salary
FROM dbt_dev_gold.fct_jobs_daily
WHERE snapshot_date >= current_date - 1
GROUP BY 1 ORDER BY 1 DESC;
```

Only mention these if a value **moved by more than 3 points**. Otherwise they
belong in the one-line summary, not in their own section. `pct_salary` matters
because the salary marts are built only from rows that have it.

## 5. Is the web copy current?

```sql
-- WEB_DSN
SELECT count(*), max(snapshot_date) FROM dbt_dev_gold.fct_jobs_daily;
-- WAREHOUSE_DSN, same query — the two should match after the 16:00 sync
```

Report only if they disagree. The website reads the web copy, so a stale copy
means users see yesterday's board no matter how healthy the warehouse is.

## Report format

Under 15 lines. Lead with action, then numbers.

```
TalentPulse — <date>

⚠ <only what needs a decision, one line each>

Pipelines : <n>/8 completed · <failed/stuck named, or "all clean">
Intake    : +<n> jobs (vnw <n>, itviec <n>, linkedin <n>, topcv <n>)
Inventory : <n> jobs, <n> active (<+/-n> vs yesterday)
Quality   : Other <n>% · no-city <n>% · salary <n>%   <only deltas >3pt noted>
Web copy  : <date> <"in sync" | "STALE — n days behind">
```

If nothing needs a decision, drop the ⚠ block entirely and keep the rest. Do not
invent a concern to fill space, and do not fix anything yourself — name what a
human should look at and stop there.
