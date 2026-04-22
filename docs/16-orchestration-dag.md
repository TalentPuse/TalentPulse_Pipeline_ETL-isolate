# Task: Design orchestration DAG for the pipeline

## Goal
Create an orchestration plan for the end-to-end pipeline.

## Pipeline stages
1. crawl listing pages
2. crawl detail pages
3. parse raw pages
4. normalize records
5. deduplicate jobs
6. extract skills
7. build weekly aggregates
8. build alert dataset
9. run data quality checks

## What to produce
Return a markdown DAG design with:
1. Task list
2. Dependencies
3. Retry strategy
4. Scheduling strategy
5. Failure handling
6. Backfill strategy
7. Idempotency strategy
8. Suggested Airflow or Prefect mapping

## Constraints
- Keep MVP manageable
- Separate daily pipeline from weekly pipeline if useful
- Support rerunning from raw data

## Output format
Return markdown with task dependency description and example DAG structure.