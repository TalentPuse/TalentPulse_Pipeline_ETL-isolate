# Task: Design deduplication strategy for job postings

## Goal
Create a deduplication strategy for job postings from VietnamWorks, designed to later support multiple sources.

## Context
The pipeline must remove duplicate job postings while preserving traceability.

## What to produce
Return a markdown document with:
1. Dedup objectives
2. Exact-match rules
3. Near-duplicate rules
4. Candidate duplicate keys
5. Survivorship rules
6. Duplicate cluster concept
7. Audit fields
8. False-positive risk handling
9. Example duplicate scenarios

## Suggested signals
- canonical_url
- source_job_id
- normalized_title
- normalized_company_name
- posted_date
- content similarity
- location

## Constraints
- Prefer conservative dedup in MVP
- Never delete raw records
- Keep duplicate mapping table for audit

## Output format
Return markdown with rules, scoring ideas, and sample scenarios.