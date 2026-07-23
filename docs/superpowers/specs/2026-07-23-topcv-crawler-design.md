# TopCV Crawler — Design

**Date:** 2026-07-23
**Repo:** `pipeline_data` (canonical `origin` → `TalentPuse/TalentPulse_Pipeline_ETL`, branch `develop`)
**Goal:** Add `topcv` as a first-class ETL source, mirroring the existing ITviec source end-to-end (listing → seed → detail crawl → parse → load → normalize → dbt → alerts).

---

## 1. Site reconnaissance (verified live via project `StealthBrowser`)

Fetched with the project's own `src/crawlers/browser.py::StealthBrowser` (Playwright, stealth). Plain `WebFetch` returns **403** — TopCV has anti-bot protection, so the browser path is required (same reason ITviec/LinkedIn use it).

- **Not blocked:** both listing and detail pages return full HTML (~1.6–1.9 MB) with `blocked=False`.
- **Listing page** `https://www.topcv.vn/tim-viec-lam-{keyword}`:
  - JSON-LD present but only `BreadcrumbList` + `SearchResultsPage/CollectionPage` — **no `ItemList` of jobs** (unlike ITviec).
  - Detail URLs are extractable via regex: `https://www.topcv.vn/viec-lam/{slug}/{numeric_id}.html?...` (140 links/page found, each appears twice with different `ta_source` query params).
  - Pagination via `?page=N` (observed `page=2`).
- **Detail page** `.../viec-lam/{slug}/{id}.html`:
  - Contains JSON-LD **`JobPosting`** with keys: `title, description, identifier, datePosted, validThrough, employmentType, hiringOrganization, jobLocation, baseSalary, industry, jobBenefits, employerOverview, occupationalCategory, experienceRequirements, skills, totalJobOpenings`.
  - **`identifier.value` is the COMPANY id** (matches `hiringOrganization.sameAs .../{id}.html`), NOT the job id. The job id must come from the URL/object key.
  - `jobLocation` is a **single dict** (`{@type: Place, address: {...}}`), not an array like ITviec.
  - `baseSalary.value.value` may be a sentinel string like `"Thoả thuận"` (negotiable) — filter like ITviec's `"You'll love it"`.

## 2. Architecture — mirror the ITviec source

A "source" in this pipeline is 6 layers. TopCV reuses the ITviec pattern; the parser reuses `strip_html`, `JobDetail`, and `_is_past` from the VietnamWorks/ITviec code. **Only 3 deltas** from ITviec.

| Layer | New file | Behavior | Delta vs ITviec |
|---|---|---|---|
| Listing crawler | `src/crawlers/topcv/listing.py` `TopCVListingCrawler` | crawl `tim-viec-lam-{kw}` + `?page=N`; upload listing HTML → `listings/topcv/`; `detect_max_page` from `page=` links; circuit breaker on `MAX_CONSECUTIVE_EMPTY=3` | **Δ1**: no `ItemList` JSON-LD → extract detail URLs via regex `viec-lam/{slug}/(\d+)\.html`, strip query, dedup |
| Detail crawler | `src/crawlers/topcv/detail.py` `TopCVDetailCrawler` | fetch → gzip → MinIO `details/topcv/html/{run_id}/{job_id}.html.gz`; `crawl_log` queue `source="topcv"`; block-detect; `CircuitBreaker`; `jitter_sleep` | copy of ITviec detail, only `SOURCE="topcv"` |
| Seeder | `src/queue/topcv_seeder.py` | `extract_job_id` regex on `.../(\d+)\.html`; `enqueue_many(items, source="topcv")` | different URL regex; stores canonical URL without query string |
| Parser | `src/parsers/topcv/detail_parser.py` `TopCVDetailParser` | `MinIOParser`; `VERSION="topcv-v1"`, `HTML_PREFIX="details/topcv/html/"`, `PARSED_PREFIX="parsed/details/topcv/"`; JSON-LD `JobPosting` → `JobDetail` | **Δ2**: normalize `jobLocation` dict-or-list; salary sentinel filter; `source_job_id` from key; `source_url` rebuilt from job_id/slug (no `potentialAction` on TopCV) |
| Flow | `orchestration/flows/topcv_pipeline.py` | mirror the 8-task ITviec Prefect flow; `TOPCV_PARSED_PREFIX="parsed/details/topcv/"`; `.serve()` cron in `__main__` guarded by `PREFECT_DEPLOY` | task/artifact names + prefix |
| Config | `src/utils/config.py` | add `TOPCV_KEYWORDS` (default `data-engineer,ai-engineer,data-analyst`) | — |

### Field mapping (JSON-LD `JobPosting` → `JobDetail`)

| JobDetail field | Source | Note |
|---|---|---|
| `source` | `"topcv"` | |
| `source_job_id` | object key (`{id}.html.gz`) | NOT `identifier.value` (= company id) |
| `source_url` | `https://www.topcv.vn/viec-lam/{slug}/{id}.html` | rebuilt |
| `title` | `title` | |
| `company_name` / `company_logo_url` | `hiringOrganization.name` / `.logo` | |
| `company_profile_text` | `None` | TopCV `employerOverview` duplicates description; skip |
| `salary_min/max` | `baseSalary.value.minValue/maxValue` if present | else `None` |
| `salary_currency` | `baseSalary.currency` | e.g. `VND` |
| `pretty_salary` / `is_salary_visible` | `baseSalary.value.value` unless sentinel (`Thoả thuận`, …) | |
| `years_of_experience` | `experienceRequirements.monthsOfExperience // 12` (min 1) | same helper as ITviec |
| `employment_type` | `employmentType` | e.g. `FULL_TIME` |
| `job_function` | `industry` or `"IT"` | |
| `locations` | normalize `jobLocation` (dict→[dict]); `city=addressRegion`, `address=streetAddress`, district=`addressLocality` | **Δ2** |
| `skills` | `skills` (comma string) if present | often absent → `[]` |
| `benefits` | `[]` (v1) | `jobBenefits` HTML deferred |
| `job_description_text` | `strip_html(description)` | |
| `posted_at` / `expired_at` / `is_expired` / `is_active` | `datePosted` / `validThrough` / `_is_past(validThrough)` | same as ITviec |

## 3. Tests (existing layout)

- `tests/crawlers/topcv/test_listing.py` — URL regex extraction + `detect_max_page` on a small HTML sample.
- `tests/parsers/topcv/test_detail_parser.py` — parse the captured real JSON-LD fixture (Viettel "Data Engineer (Junior/Middle)", id `2114998`): asserts title, company, `years_of_experience=1`, negotiable salary → `is_salary_visible=False`, `source_job_id` from key (not `246114`), single-dict location → `city="Hà Nội"`.
- `tests/queue/test_topcv_seeder.py` — `extract_job_id` accepts `.../{slug}/2114998.html`, rejects malformed.

Fixture: capture one real detail HTML.gz into `tests/fixtures/topcv/`.

## 4. CI/CD (decision: dedicated TopCV image)

- `orchestration/Dockerfile.worker.topcv` — copy of `Dockerfile.worker.itviec` (Playwright base).
- `orchestration/topcv-requirements.txt` — copy of `itviec-requirements.txt`.
- `deploy.yml` build matrix — add `{ image_suffix: topcv, dockerfile: orchestration/Dockerfile.worker.topcv }`.
- `.github/workflows/pipeline-topcv.yml` — copy of `pipeline-itviec.yml`; `image-suffix: topcv`, `module: orchestration.flows.topcv_pipeline`, `TOPCV_KEYWORDS` env.
- `.github/actions/run-flow/action.yml` — add `-e TOPCV_KEYWORDS` to the `docker run` env list.

**GHA registration gotcha:** a new `schedule`/`workflow_dispatch` workflow only registers when the file is added/modified in a push to the **default** branch (canonical repo has no `main`; default is `develop`). The trailing-comment trick in `pipeline-itviec.yml` applies.

## 5. Explicitly out of scope (YAGNI)

- No DB schema change (`crawl_log` is `source`-parameterized; loader/normalizer/dbt are source-agnostic).
- TopCV **added** to `SKIP_FOCUS_SOURCES` (default 'itviec,topcv'): its `job_function` is an industry string, never a focus keyword, so `validate_focus` would reject every job. The focused keyword search is the filter, same as itviec. (Corrected after live-data test on 2026-07-23.)
- `benefits`, non-IT keyword expansion, and `jobBenefits` HTML parsing deferred to a later iteration.
