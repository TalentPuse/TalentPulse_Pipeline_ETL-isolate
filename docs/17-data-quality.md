# Task: Define data quality checks for the job pipeline

## Goal
Create a data quality framework for the pipeline.

## What to validate
- raw fetch success rate
- parser success rate
- required field completeness
- duplicate rate
- invalid date rate
- invalid location rate
- skill extraction coverage
- weekly aggregate freshness

## What to produce
Return a markdown document containing:
1. Quality dimensions
2. Specific checks
3. Thresholds
4. Severity levels
5. Stop-the-line vs warning checks
6. Example failed cases
7. Suggested implementation points in the pipeline

## Constraints
- Must be practical for MVP
- Prioritize checks that protect downstream alert quality
- Support automated monitoring later

## Output format
Return markdown with tables and checklists.