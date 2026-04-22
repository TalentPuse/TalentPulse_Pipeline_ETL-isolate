# Task: Design normalized schema for job postings

## Goal
Create the normalized schema for job postings after parsing and cleaning.

## Context
The pipeline needs standardized job records for analytics and downstream job alert service.

## Requirements
Design a normalized table or model containing standardized fields such as:
- job_uid
- source
- source_job_id
- source_url
- canonical_url
- title
- normalized_title
- company_name
- normalized_company_name
- city
- district if available
- role_family
- role_focus
- seniority
- employment_type
- posted_date
- ingested_at
- description_text
- requirements_text
- benefits_text
- language
- is_hcmc_target
- is_data_engineer_target
- is_ai_engineer_target

## What to produce
Return a markdown design with:
1. Field dictionary
2. Data types
3. Required fields
4. Standardization rules
5. Example normalized record
6. Notes on target role tagging
7. Notes on location filtering

## Constraints
- Optimize for MVP clarity
- Support later addition of other locations and roles
- Keep source-specific fields separate from normalized fields if needed

## Output format
Return markdown with schema tables and examples.