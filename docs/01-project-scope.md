# Task: Define project scope for Job Market Trends Pipeline MVP

## Context
We are building a data pipeline MVP for a job alert product. The pipeline serves two purposes:
1. Help end users receive relevant jobs faster.
2. Produce weekly job market insights for internal product/data teams.

The MVP scope is limited to:
- Roles: Data Engineer, AI Engineer
- Location: Ho Chi Minh City, Vietnam
- Initial source: VietnamWorks public listing pages and public job detail pages only
- Must respect robots.txt and avoid private/authenticated/apply/login areas

## Business goals
Build an end-to-end pipeline:
- ingest
- raw storage
- normalization
- deduplication
- technical skill extraction
- weekly aggregation

Outputs:
- normalized job dataset
- skill taxonomy v1 for Data Engineer and AI Engineer
- weekly report dataset
- downstream dataset for alert service

Out of scope:
- auto-apply
- full Vietnam market expansion
- salary benchmarking
- long-term forecasting

## What to produce
Create a concise technical design note in markdown with:
1. Problem statement
2. MVP scope
3. In-scope / out-of-scope
4. Core entities
5. End-to-end data flow
6. Success criteria
7. Risks and assumptions

## Constraints
- Keep architecture practical for a solo or small team Data Engineer build
- Prioritize simplicity and iteration speed
- Use VietnamWorks as source 1 only
- Design for easy extension to more sources later

## Output format
Return a markdown document with clear sections and bullet points.