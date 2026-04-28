# ITviec Crawler Plan

## 1. Exploration Summary

### Site Structure (verified 2026-04-28)
- **Listing URL**: `https://itviec.com/it-jobs/{keyword-slug}` (e.g. `data-engineer`, `ai-engineer`, `data-analyst`)
- **Pagination**: `?page=N` (starts at 1, not 0)
- **Search URL**: `https://itviec.com/it-jobs?query=AI+Engineer` — works but no JSON-LD, not recommended
- **Detail URL**: `https://itviec.com/it-jobs/{job-slug}-{company-slug}-{numeric-id}`
- **No robots.txt** — no explicit restrictions
- **Cloudflare protection**: requires headless Chrome (Playwright), ~10s wait after page load

### Pagination (critical finding)
- URL pattern: `?page=N` (1-indexed, page 1 has no param)
- Max page detected from `nav.ipagination .page a` links on page 1
- 20 jobs per page, last page may have fewer
- **Cloudflare kills session on navigate** — navigating to page 2 without clearing cookies returns empty HTML. **Fix: `ctx.clear_cookies()` before each `page.goto()`**. This is faster than creating a new context (~8.5s vs ~11s per page). Verified: 100% success across all pages.
- Pagination HTML structure:
  ```html
  <nav class="ipagination">
    <div class="page current">1</div>
    <div class="page"><a href="/it-jobs/python?page=2&source=search_job">2</a></div>
    <div class="page gap">…</div>
    <div class="page"><a href="/it-jobs/python?page=8&source=search_job">8</a></div>
    <div class="page next"><a rel="next" href="...">→</a></div>
  </nav>
  ```
- Tested: `python` keyword → 8 pages, 158 unique jobs, 0 duplicates, ~11s/page

### Data Available

**Listing page** — embedded `<script type="application/ld+json">` with `@type: ItemList`:
- 20 jobs per page (last page may have fewer)
- Each item has full detail URL
- Pagination detected from `nav.ipagination .page a[href*="page="]` links

**Detail page** — embedded `<script type="application/ld+json">` with `@type: JobPosting`:

| Field | JSON-LD Key | Example |
|-------|-------------|---------|
| Title | `title` | "Senior Data Engineer" |
| Company name | `hiringOrganization.name` | "National Citizen Bank \| NCB" |
| Company logo | `hiringOrganization.logo` | URL to png |
| Company description | `hiringOrganization.description` | Vietnamese text |
| Date posted | `datePosted` | "2026-04-24" |
| Expiry | `validThrough` | "2026-05-29" |
| Skills (comma string) | `skills` | "Data Engineer, BigQuery, Python" |
| Description (HTML) | `description` | Full JD with HTML tags |
| Benefits (HTML) | `jobBenefits` | Benefits list with HTML |
| Employment type | `employmentType` | "FULL_TIME" |
| Experience (months) | `experienceRequirements.monthsOfExperience` | 37 |
| Salary currency | `baseSalary.currency` | "USD" |
| Salary unit | `baseSalary.value.unitText` | "MONTH" |
| Salary value | `baseSalary.value.value` | "You'll love it" (hidden unless logged in) |
| Location (array) | `jobLocation[].address` | streetAddress, addressRegion, postalCode |
| Apply URL | `potentialAction.target` | Direct apply link |
| Industry | `industry` | "Information Technology" |
| Direct apply | `directApply` | "TRUE" |

**Salary**: always hidden for non-logged-in users. JSON-LD returns `"You'll love it"` as value. HTML shows "Sign in to view salary". This is a known limitation — salary data requires authentication.

### Volume Estimate (verified 2026-04-28)
| Keyword | Pages | Total Jobs |
|---------|-------|------------|
| python | 8 | 158 |
| data-engineer | 2 | 29 |
| ai-engineer | 1 | 20 |
| data-analyst | 1 | 20+ |

Target keywords (data-engineer, ai-engineer, data-analyst) yield ~60-80 unique jobs with overlap. Broader keywords like `python` show the system scales fine to 8+ pages.

## 2. Architecture Decision: Playwright Required

VietnamWorks uses a public JSON API → simple `requests` library works.  
ITviec uses server-side rendered HTML behind Cloudflare → **Playwright headless Chrome is mandatory**.

### Implications for Production
- Worker Docker image needs Playwright + Chromium (~400MB extra)
- Crawl speed: ~15s per page (10s Cloudflare wait + network)
- Memory: Chromium process needs ~200-300MB RAM

### Docker Strategy Options

**Option A: Single worker image (add Playwright to existing worker)**
- Pros: simple, one image
- Cons: bloats VNW worker with Chromium it doesn't need, 400MB+ larger image
- Build time: slower for all deploys

**Option B: Separate ITviec worker image** (RECOMMENDED)
- `orchestration/Dockerfile.worker.itviec` with Playwright base
- Dedicated Prefect flow/deployment for ITviec pipeline
- Pros: separation of concerns, VNW worker stays lean
- Cons: second image to maintain

**Option C: Playwright as sidecar**
- Run `browserless/chrome` container, connect via CDP
- Pros: reusable, no Playwright in worker
- Cons: extra container, CDP connection complexity

## 3. Implementation Plan

### 3.1 Module Structure
```
src/crawlers/itviec/
    __init__.py
    browser.py          # Stealth browser factory — make_stealth_context(), STEALTH_JS
    listing.py          # ITviecListingCrawler — pagination with fresh contexts
    detail.py           # ITviecDetailCrawler — detail page fetcher
    parser.py           # JSON-LD extraction (no HTML parsing needed!)
    schema.py           # ITviecJobDetail dataclass → maps to shared JobDetail
```

### 3.2 Listing Crawler (`listing.py`)

**Input**: keyword slugs (`["data-engineer", "ai-engineer", "data-analyst"]`)

**Flow**:
1. Launch Playwright Chromium (headless), create 1 context + 1 page with stealth JS
2. For each keyword:
   a. For page = 1..N:
      - **`ctx.clear_cookies()`** before each navigation (resets Cloudflare session)
      - Navigate to `https://itviec.com/it-jobs/{keyword}?page={pg}` (no `?page=` for page 1)
      - Wait 8s for Cloudflare challenge to pass
      - Extract JSON-LD `ItemList` → list of detail URLs
      - On page 1: detect max page from `nav.ipagination .page a` links
      - Upload raw HTML to MinIO: `listings/itviec/list_{keyword}_p{page}_{timestamp}.html`
      - Sleep 2-4s random between pages
   b. Stop when: page returns 0 items OR page > max_page OR circuit breaker trips
3. Return collected detail URLs

**Key difference from VNW**: VNW listing uses JSON API and returns structured data. ITviec listing gives us only URLs (via JSON-LD) — the actual job data comes from detail pages.

### 3.3 Detail Crawler (`detail.py`)

**Input**: list of detail URLs from listing step (or from crawl_log queue)

**Flow**:
1. Reuse same browser/context/page from listing step (or create new one)
2. For each URL:
   a. **`ctx.clear_cookies()`** before navigation
   b. Navigate to detail page
   c. Wait 8s for Cloudflare
   d. Extract full page HTML
   e. gzip compress, upload to MinIO: `details/itviec/html/{run_id}/{job_id}.html.gz`
   f. Update crawl_log status
3. Rate limit: ~12s per page (8s CF wait + 2-5s random delay)

**job_id extraction**: last segment of URL path, e.g. `senior-data-engineer-national-citizen-bank-ncb-4611` → job_id = `4611` (numeric suffix)

### 3.4 Parser (`parser.py`)

**The big win**: ITviec embeds full `JobPosting` schema.org JSON-LD in every detail page. No complex HTML parsing needed — just `json.loads()` on the script tag content.

**Extraction steps**:
1. Find `<script type="application/ld+json">` with `@type == "JobPosting"`
2. Parse JSON
3. Map to shared `JobDetail` schema:

| JobDetail field | ITviec JSON-LD source |
|----------------|----------------------|
| source | `"itviec"` (hardcoded) |
| source_job_id | numeric ID from URL |
| source_url | full URL |
| title | `title` |
| company_name | `hiringOrganization.name` |
| company_logo_url | `hiringOrganization.logo` |
| company_profile_text | `hiringOrganization.description` |
| salary_min | None (hidden) |
| salary_max | None (hidden) |
| salary_currency | `baseSalary.currency` |
| is_salary_visible | `false` (always for anon) |
| job_level | infer from title keywords |
| years_of_experience | `experienceRequirements.monthsOfExperience / 12` |
| employment_type | `employmentType` |
| job_function | infer from keyword used |
| locations | `jobLocation[].address.addressRegion` |
| skills | `skills.split(", ")` |
| benefits | strip HTML from `jobBenefits` |
| job_description_text | strip HTML from `description` |
| posted_at | `datePosted` |
| expired_at | `validThrough` |
| is_expired | `validThrough < now()` |

### 3.5 Integration with Existing Pipeline

**crawl_log**: already supports `source` column. ITviec jobs use `source='itviec'`.

**seeder**: needs ITviec variant — but simpler since listing already gives us full URLs (VNW seeder builds URLs from alias+jobId).

**job_detail_repo**: already source-agnostic. PK is `(source, source_job_id)`. ITviec rows go in same `raw.job_detail` table.

**validators**: `validate_focus` checks `job_function`. ITviec won't have VNW's structured `jobFunctionsV3` — either:
- Skip focus validation for ITviec (all results already filtered by keyword)
- Set `job_function` to the search keyword used → validator string matching works

**dbt**: `stg_job_detail.sql` already reads all rows from `raw.job_detail`. ITviec rows flow through bronze → silver → gold automatically. Silver normalization (salary, city, degree mapping) may need new seed entries for ITviec-specific values.

### 3.6 Orchestration

New Prefect flow: `orchestration/flows/itviec_pipeline.py`

```
itviec_pipeline:
    listing_crawl (Playwright) → seed_queue → detail_crawl (Playwright) → detail_parse → load_warehouse → dbt_transform
```

The listing + detail steps can be combined (same browser session), or kept separate for retry granularity.

**Schedule**: offset from VNW pipeline to avoid overlapping Chromium + VNW requests.
- VNW: `0 2 * * *` (2 AM)
- ITviec: `0 4 * * *` (4 AM)

## 4. Docker Setup

### Dockerfile.worker.itviec
```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.44.0-noble
WORKDIR /app
COPY orchestration/itviec-requirements.txt .
RUN pip install --no-cache-dir -r itviec-requirements.txt
RUN playwright install chromium
ENV PYTHONPATH=/app
COPY src/ ./src/
COPY orchestration/ ./orchestration/
COPY dbt_transform/ ./dbt_transform/
RUN cd dbt_transform && dbt deps --profiles-dir .
```

### itviec-requirements.txt
```
playwright>=1.44.0
prefect==2.16.5
requests==2.31.0
beautifulsoup4==4.12.2
sqlalchemy==2.0.28
psycopg2-binary==2.9.9
boto3==1.34.62
python-dotenv==1.0.1
tenacity==8.2.3
dbt-postgres==1.7.13
```

### docker-compose addition
```yaml
itviec-worker:
  build:
    context: .
    dockerfile: orchestration/Dockerfile.worker.itviec
  env_file: .env
  environment:
    PREFECT_API_URL: http://prefect-server:4200/api
  depends_on:
    postgres:
      condition: service_healthy
    minio:
      condition: service_healthy
  networks:
    - talentpulse
  deploy:
    resources:
      limits:
        memory: 1G
```

## 5. Anti-Detection & IP Protection (verified 2026-04-28)

### 5.1 Stealth Config (tested on bot.sannysoft.com)

**Key discovery**: Cloudflare chặn dựa trên **cookie/session**, không phải IP. Chỉ cần `ctx.clear_cookies()` trước mỗi navigation là bypass 100%. Không cần tạo context mới, không cần đổi IP.

```python
# Production approach: 1 context, clear cookies per request
ctx = browser.new_context(
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...Chrome/125.0.0.0...",
    viewport={"width": 1920, "height": 1080},
    locale="vi-VN",
    timezone_id="Asia/Ho_Chi_Minh",
)
page = ctx.new_page()
page.add_init_script(STEALTH_JS)

for url in urls:
    ctx.clear_cookies()          # ← this is the magic line
    page.goto(url, ...)
    page.wait_for_timeout(8000)  # Cloudflare challenge pass
    # extract data...
    time.sleep(random.uniform(2, 5))
```

**Stealth JS patches** (injected via `add_init_script`):
- `navigator.webdriver` → `undefined`
- `window.chrome` → fake runtime object
- `navigator.permissions.query` → returns real Notification state
- `navigator.plugins` → 3 realistic Chrome plugins (PDF, NaCL) with correct `PluginArray` prototype
- `navigator.languages` → `["vi-VN", "vi", "en-US", "en"]`

### 5.2 Verified Stress Test Results

| Test | Method | Requests | Blocked | Duration |
|------|--------|----------|---------|----------|
| 8 listing pages (python) | new_context per page | 8 | 0 | ~90s |
| 2 keywords + 10 detail | new_context per page | 22 | 0 | ~5min |
| 4 pages same context | clear_cookies() | 4 | 0 | ~40s |
| **Full pipeline (3 kw + 5 detail)** | **clear_cookies()** | **16** | **0** | **~2.5min** |
| Collected | clear_cookies() | 193 unique URLs | 0 | 11 pages |

`clear_cookies()` is faster (~8.5s/page vs ~11s/page) because it reuses the browser process and only resets the Cloudflare session token.

### 5.3 Rate Limiting Strategy

Production crawl budget: ~67 unique target jobs across 3 keywords.

| Phase | Requests | Delay | Total Time |
|-------|----------|-------|------------|
| Listing (3 keywords, ~4 pages) | 4 | 2-4s random | ~50s |
| Detail (67 jobs) | 67 | 2-5s random | ~15min |
| **Total** | **~71** | | **~16min** |

Safe limits:
- **Max 100 requests per run** (circuit breaker trips beyond this)
- **Random delay 2-5s** between requests (not fixed, harder to fingerprint)
- **Cloudflare wait 8s** (not 10s — tested, 8s is enough)
- **One run per day** (4 AM, offset from VNW at 2 AM)

### 5.4 Circuit Breaker (reuse from VNW, adapted)

Adapted from `src/crawlers/vietnamworks/detail/circuit_breaker.py`:

| Trigger | Action |
|---------|--------|
| Page returns 0 JSON-LD items (Cloudflare block) | Count as failure |
| 3 consecutive failures | **Stop crawl, wait 15min** |
| >50% failure rate in last 20 requests | **Stop crawl, wait 15min** |
| Page returns HTTP 403/429 | Count as block signal |
| 3 consecutive 403 | **Kill switch — abort entire run** |

On circuit breaker trip: log warning, save progress (already-crawled URLs are in MinIO), next daily run resumes.

### 5.5 Fallback Plan If IP Gets Blocked

**Level 1 — Increase stealth** (code change):
- Rotate User-Agent from pool of 10+ real Chrome UAs
- Randomize viewport, timezone, locale per request
- Add mouse movement / scroll simulation before extraction

**Level 2 — Slow down** (config change):
- Increase delay to 10-15s between requests
- Reduce pages per run (crawl over multiple days)
- Spread requests across 2-3 hour window instead of burst

**Level 3 — Proxy rotation** (infrastructure change):
- Free proxy pool (unreliable but available)
- Paid rotating proxy service (~$5-10/month for this volume)
- Route through different VPS/cloud function per request

**Level 4 — Residential proxy** (last resort):
- Services like BrightData, Oxylabs
- ~$15/GB but virtually unblockable
- Overkill for 71 requests/day

For current volume (71 requests/day), Level 1 + Level 2 should be more than enough.

## 6. Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Cloudflare blocks headless Chrome | Stealth JS patches + UA/viewport rotation (tested, 0 blocks in 22 requests) |
| Cloudflare invalidates session on navigate | Fresh `browser.new_context()` per page (verified) |
| IP ban from too many requests | Circuit breaker (auto-stop on failures) + random delays 2-5s |
| Salary always hidden | Accept limitation, document in data contract |
| Chromium memory leak on long runs | Fresh context per page already fixes this (context.close() frees memory) |
| Duplicate jobs across keywords | Dedup by source_job_id (numeric ID from URL) |
| JSON-LD schema changes | Parser version tracking, alert on parse failures |
| Cloudflare changes detection method | Stealth JS is modular — update patches without changing crawler logic |

## 6. Implementation Order

1. **`src/crawlers/itviec/parser.py`** — JSON-LD extraction + mapping to JobDetail. Easiest to test standalone.
2. **`src/crawlers/itviec/listing.py`** — Playwright listing crawler. Returns list of detail URLs.
3. **`src/crawlers/itviec/detail.py`** — Playwright detail crawler. Fetches + stores HTML.
4. **`src/crawlers/itviec/schema.py`** — ITviec-specific dataclass if needed (or reuse shared JobDetail).
5. **Seeder integration** — Add ITviec seeder path or generalize existing seeder.
6. **`orchestration/flows/itviec_pipeline.py`** — Prefect flow wiring.
7. **Docker** — Dockerfile + compose service.
8. **dbt seeds** — Add any ITviec-specific mapping values (city names, etc.).
9. **Tests** — Unit tests for parser (mock JSON-LD), integration test for listing page.
