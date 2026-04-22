# Task: Design downstream dataset for job alert service

## Goal
Create a dataset design for a downstream alert/recommendation service based on normalized and enriched job postings.

## What to produce
Return a markdown design covering:
1. Purpose of the alert dataset
2. Required columns
3. Freshness requirements
4. Filtering rules
5. Priority/ranking-friendly fields
6. Skill-related fields
7. Delivery options
8. Example records

## Suggested columns
- alert_job_id
- job_uid
- title
- normalized_title
- company_name
- city
- role_family
- posted_date
- source
- source_url
- extracted_skills
- skill_count
- is_new_job
- quality_score
- dedup_cluster_id

## Constraints
- Support daily or near-daily refresh
- Keep design simple for MVP
- Must work after weekly aggregation pipeline is in place

## Output format
Return markdown with schema tables and notes.