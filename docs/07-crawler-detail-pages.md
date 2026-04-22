# Task: Implement VietnamWorks public job detail crawler plan

## Goal
Create a detailed implementation plan for fetching public VietnamWorks job detail pages discovered from listing pages.

## Requirements
The detail crawler should:
- read discovered job URLs from raw or intermediate storage
- avoid duplicate fetches in the same run
- fetch job detail HTML
- capture fetch metadata
- save raw content
- record failures cleanly

## What to produce
Return a markdown implementation plan with:
1. Input/output contract
2. Dedup logic at crawl stage
3. Fetch workflow
4. Retry behavior
5. Rate limiting
6. Raw record examples
7. Suggested Python classes/modules
8. Acceptance criteria
9. Test cases

## Constraints
- No code
- Must support future multi-source extension
- Must preserve traceability back to listing pages

## Output format
Return markdown, implementation-ready.  