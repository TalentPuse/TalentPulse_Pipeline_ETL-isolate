# Task: Define parser contract for VietnamWorks listing and detail pages

## Goal
Design parser contracts so that raw HTML can be converted into structured records reliably.

## Requirements
Define contracts for:
- listing page parser
- detail page parser

For detail pages, target fields may include:
- source_job_id if available
- title
- company_name
- location_raw
- role_raw
- posted_at_raw
- employment_type_raw
- seniority_raw
- description_raw
- requirements_raw
- benefits_raw
- source_url

## What to produce
Return a markdown spec with:
1. Parser inputs
2. Parser outputs
3. Required fields
4. Optional fields
5. Validation rules
6. Null handling rules
7. Parser versioning strategy
8. Examples of parsed output
9. Parser failure handling

## Constraints
- Preserve raw text for later reprocessing
- Avoid over-cleaning at parse stage
- Make outputs easy to normalize later

## Output format
Return markdown with tables and JSON examples.