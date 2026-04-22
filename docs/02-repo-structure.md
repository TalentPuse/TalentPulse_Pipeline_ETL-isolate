# Task: Create repository structure for the pipeline project

## Goal
Design a clean, production-style repository structure for a data engineering pipeline that crawls VietnamWorks public jobs and processes them into analytics-ready datasets.

## Requirements
Use Python for crawling and transformations.
Prepare for:
- crawler modules
- parsers
- storage clients
- normalization logic
- deduplication
- skill extraction
- aggregation
- orchestration
- tests
- configs

## Expected repository structure
Include folders such as:
- src/
- crawlers/
- parsers/
- pipelines/
- models/
- sql/
- tests/
- configs/
- docs/
- scripts/
- data_contracts/

## What to produce
Generate:
1. A proposed folder tree
2. Short explanation for each top-level folder
3. Suggested naming conventions
4. Suggested environment variable names
5. A minimal `requirements.txt`
6. A minimal `.env.example`
7. A minimal `README.md` outline

## Constraints
- Keep it simple for MVP
- Make it easy to add new job boards later
- Separate raw ingestion logic from transformation logic
- Assume we will later orchestrate with Airflow or Prefect

## Output format
Return all content in markdown, with code blocks for tree structure and config examples.