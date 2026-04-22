# Task: Recommend coding order for implementing the pipeline (ETL Plan)

## Goal
Produce the exact coding order for building the MVP pipeline from scratch.

## Context
We start with VietnamWorks public pages only, focusing on an MVP that supports both Job Market Insight reporting and a downstream Job Match/Alert engine.

## Implementation Order (The ETL Plan)

### Step 1: Foundation & The "Thin Slice" Extract (EXTRACT - Part 1)
- **What to code first:** 
  1. Base project structure (`src/`, `crawlers/`, configuration, logging).
  2. A robust Base Fetcher (`requests`/`httpx`) with rate-limiting, retries, and User-Agent rotation.
  3. The `VietnamWorksListingCrawler` to fetch search result pages (Data Engineer/AI Engineer in HCMC) and extract raw Job Detail URLs.
- **Why this reduces risk:** Networking and anti-bot mechanisms are the biggest unknowns. Getting raw HTML out reliably proves the core engine works.
- **Mocking:** Mock HTML responses for testing the URL extraction logic.
- **Definition of Done (DoD):** A script runs, paginates through search results, and saves raw listing HTML to local disk (`raw_zone/listings/`).

### Step 2: Full Job Detail Crawling (EXTRACT - Part 2)
- **What to code first:** 
  1. The `VietnamWorksDetailCrawler`.
  2. Input: A list of URLs extracted from Step 1.
  3. Process: Fetch and save the complete HTML of the job post.
- **Why this reduces risk:** Separating listing and detail fetches prevents data loss. If detail fetch fails, we don't have to re-fetch listings.
- **Definition of Done (DoD):** System successfully iterates over a list of job URLs and saves complete job descriptions to `raw_zone/details/`.

### Step 3: Parsing & Core Processing (TRANSFORM - Part 1)
- **What to code first:** 
  1. `VietnamWorksParser`: Parses raw HTML (from Step 2) into semi-structured JSON (Title, Company, Location, Raw Description).
  2. `Normalizer`: Cleans up titles (e.g., standardizing "Senior Data Eng" to "Data Engineer - Senior"), dates, and standardizes locations to HCMC.
- **Why this reduces risk:** Parse logic fails often due to website UI updates. Operating purely on stored raw HTML allows instant, offline reprocessing without hitting the target server again.
- **Initial Dataset:** `silver_normalized_jobs`.
- **Definition of Done (DoD):** A script reads offline raw HTML, parses it, standardizes fields, and writes to a structured database/Parquet file. 

### Step 4: Deduplication & Skill Enrichment (TRANSFORM - Part 2)
- **What to code first:** 
  1. `Deduplicator`: Basic rule matching (Job Title + Company Name + Location = Same Job) to prevent spam in alerts.
  2. `SkillExtractor`: Regular Expression (RegEx) or Keyword matching against the normalized `description_text` using the V1 Taxonomy.
- **Why this reduces risk:** Enrichment (skills) and exactness (dedup) are the two core value propositions of the product. Building them after basic parsing ensures stable input data.
- **Definition of Done (DoD):** Normalized jobs are clustered (duplicates tagged) and enriched with a list of extracted technical skills (e.g., `["Python", "Airflow", "SQL"]`).

### Step 5: Downstream Datasets & Serving (LOAD)
- **What to code first:** 
  1. `AlertDatasetBuilder`: Filter active, new jobs specifically formatted for the Telegram Bot / Notification layer.
  2. `WeeklyAggregator`: Count skill frequencies, job volumes by role to build the Market Trend insights.
- **Earliest end-to-end thin slice:** By the end of Step 5, the pipeline from raw internet HTML to clean JSON ready for a Telegram Bot is complete.
- **Definition of Done (DoD):** Two separate output tables/files generated (`gold_alert_jobs` and `gold_weekly_metrics`).

### Step 6: Orchestration & Automated Operations
- **What to code first:** 
  1. Wrap Steps 1 to 5 into an orchestrator (Prefect, Dagster, or a simple Cron job scheduling `main.py`).
  2. Implement basic Data Quality alerts (e.g., "Crawler returned 0 jobs").
- **Why this reduces risk:** Ensures the pipeline runs autonomously without manual terminal commands.
- **Definition of Done (DoD):** The pipeline runs fully automated on a schedule (e.g., daily at 2:00 AM) and outputs fresh datasets.