# Task: Define raw data contract for crawled VietnamWorks pages

## Goal
Create a raw-zone data contract for storing VietnamWorks crawl results before transformation.

## Context
We need replayability and traceability. Store raw HTML or raw extracted payloads from public listing and detail pages.

## Requirements
Design schemas for:
- crawl_run metadata
- listing page raw records
- job detail page raw records
- fetch status / errors

## Fields to include
At minimum consider:
- source
- crawl_run_id
- fetched_at
- page_type
- page_url
- final_url
- http_status
- content_type
- raw_html_path or raw_payload
- checksum
- request_latency_ms
- parser_version
- discovered_job_url
- parent_listing_url

## What to produce
Return a markdown spec containing:
1. Raw storage principles
2. Raw object naming convention
3. JSON schema examples
4. Partition strategy
5. Required vs optional fields
6. Sample records for listing page and detail page
7. Failure/error record schema

## Constraints
- Support idempotent reprocessing
- Support debugging of parser failures
- Keep it extensible for future job sources

## Output format
Return markdown with tables and JSON examples.