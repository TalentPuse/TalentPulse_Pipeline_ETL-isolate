# Task: Define source policy and crawl rules for VietnamWorks

## Goal
Create a source policy document for crawling VietnamWorks public job listings and public job detail pages.

## Context
We only want to crawl public pages and must respect robots.txt. We do not want to touch login, apply flows, account pages, private APIs, or disallowed routes.

## What to produce
Write a markdown policy document containing:
1. Source name
2. Allowed scope
3. Disallowed scope
4. Crawl boundaries
5. Request throttling guidelines
6. User-agent strategy
7. Retry and backoff rules
8. Logging requirements
9. Data fields allowed to collect
10. Compliance checklist before running crawler

## Important notes
- Do not assume permission for hidden APIs
- Only use public HTML pages unless clearly allowed
- Design the policy so engineers can implement guardrails in code
- Include a pre-run checklist and runtime checks

## Output format
Return a markdown document with explicit rules, checklists, and implementation notes.   