# Task: Design storage layers for the pipeline

## Goal
Design the storage architecture for raw, normalized, and analytics-ready job data.

## Context
The MVP pipeline flow is:
ingest -> raw storage -> normalization -> dedup -> skill extraction -> weekly aggregation

## What to produce
Write a markdown design document covering:
1. Storage layers: bronze/raw, silver/normalized, gold/aggregated
2. Recommended technologies for MVP
3. Object storage path conventions
4. Database schema grouping
5. Retention strategy
6. Reprocessing strategy
7. Tradeoffs between PostgreSQL, DuckDB, and Parquet

## Assumptions
- Python-based pipeline
- One source first, more later
- Weekly reporting and downstream alert service required
- Team wants Data Engineer-friendly stack

## Output format
Return a practical design with example table names and storage paths.