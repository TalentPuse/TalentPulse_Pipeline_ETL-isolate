---
name: talentpulse-weekly-market
description: Weekly report on what the Vietnamese tech job market is doing — which skills are rising or falling, salary by level, who is hiring, and which new job titles the classifier cannot handle yet. Use for the Monday market report.
license: MIT
metadata:
  hermes:
    tags: [TalentPulse, weekly report, market analysis, skills, salary]
---

# TalentPulse weekly market report

Read `talentpulse-platform` first. **Read-only: SELECT only.**

The daily brief answers "is the machine working". This one answers "what is the
market doing" — the question the product actually exists to answer. Weekly, not
daily, because none of these numbers move meaningfully in 24 hours and a daily
version would be noise.

## 1. Skill demand, week over week

```sql
WITH this_week AS (
  SELECT skill_name, count(DISTINCT source_job_id) AS n
  FROM dbt_dev_silver.silver_skill_unified u
  JOIN dbt_dev_gold.fct_jobs_daily f USING (source, source_job_id)
  WHERE f.snapshot_date = current_date AND f.posted_at >= current_date - 7
  GROUP BY 1
), prev_week AS (
  SELECT skill_name, count(DISTINCT source_job_id) AS n
  FROM dbt_dev_silver.silver_skill_unified u
  JOIN dbt_dev_gold.fct_jobs_daily f USING (source, source_job_id)
  WHERE f.snapshot_date = current_date
    AND f.posted_at >= current_date - 14 AND f.posted_at < current_date - 7
  GROUP BY 1
)
SELECT t.skill_name, t.n AS this_week, coalesce(p.n,0) AS prev_week,
       t.n - coalesce(p.n,0) AS change
FROM this_week t LEFT JOIN prev_week p USING (skill_name)
WHERE t.n >= 5
ORDER BY change DESC;
```

Report the top 5 risers and top 5 fallers. **Ignore anything under 5 mentions** —
below that, one company posting five near-identical roles looks like a trend and
is not.

## 2. Salary by level

```sql
SELECT job_level,
       count(*) FILTER (WHERE salary_vnd_monthly_avg IS NOT NULL) AS with_salary,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY salary_vnd_monthly_avg)/1e6, 1) AS median_m_vnd
FROM dbt_dev_gold.fct_jobs_daily
WHERE snapshot_date = current_date AND is_active
GROUP BY 1 ORDER BY median_m_vnd DESC NULLS LAST;
```

Always report `with_salary` next to the median. Most postings hide the salary,
so a median over a handful of rows is not a market rate — say so rather than
quoting it bare.

## 3. Who is hiring

```sql
SELECT company_name, count(*) AS open_roles
FROM dbt_dev_gold.fct_jobs_daily
WHERE snapshot_date = current_date AND is_active
GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
```

Note any company that entered or left the top 10 versus last week — that is the
part worth reading.

## 4. What the classifier still cannot handle

```sql
SELECT title, count(*) AS n
FROM dbt_dev_gold.fct_jobs_daily
WHERE snapshot_date = current_date AND job_category = 'Other'
  AND posted_at >= current_date - 7
GROUP BY 1 ORDER BY 2 DESC LIMIT 15;
```

This is the most actionable section, and the reason it is worth running weekly.
The seed maps in `dbt_transform/seeds/job_title_category_map.csv` only recognise
titles somebody has already added. Every week the market invents new ones and
they silently pile into `Other`.

Group the results into themes and propose concrete seed rows
(`keyword,job_category,priority`). Two rules learned the hard way:

- Vietnamese titles need **both** the accented and unaccented spelling — the
  seed once carried only unaccented forms and matched almost nothing, because
  `lower()` does not strip diacritics.
- Spelling variants matter as much: `back end` / `back-end` / `backend` are
  three different strings and the seed needs all three.

Propose the rows. Do not edit the seed yourself.

## Report format

Under 25 lines, and lead with section 4 — it is the only part with an action
attached.

```
TalentPulse market — week of <date>

New titles falling into Other (<n> jobs) — suggested seed rows:
  <keyword>,<category>,<priority>     × up to 5

Skills rising : <skill> +<n>, ...
Skills falling: <skill> -<n>, ...
Salary median : Senior <n>M · Mid <n>M · Fresher <n>M   (n=<with_salary> of <total>)
Top hiring    : <company> (<n>), ...   <new entrants marked>
Board         : <n> active jobs, <+/-n> vs last week
```

Numbers, not adjectives. If a section has too little data to be meaningful, write
one line saying so instead of quoting a number that will be believed.
