# TopCV Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `topcv` as a first-class ETL source in `pipeline_data`, mirroring the ITviec source end-to-end (listing → seed → detail crawl → parse → load → normalize → dbt → alerts).

**Architecture:** TopCV reuses the ITviec source pattern. Detail pages expose JSON-LD `JobPosting` (parsed almost identically to ITviec); listing pages do NOT expose an `ItemList`, so detail URLs are extracted via regex. Crawling uses the shared `StealthBrowser`. Storage/queue/loader/dbt are source-agnostic and need no schema change — only `source="topcv"` is threaded through `crawl_log`.

**Tech Stack:** Python 3.10, Playwright (`StealthBrowser`), MinIO/R2, Postgres `raw.crawl_log`, Prefect 2.16, pytest.

## Global Constraints

- Repo: `pipeline_data`, branch `develop`. Run all commands from `D:\TalentPulse\pipeline_data`. Python interpreter: `.venv/Scripts/python.exe`.
- No DB schema change. `crawl_log` is `source`-parameterized already.
- TopCV IS added to `SKIP_FOCUS_SOURCES` (default 'itviec,topcv') — job_function is an industry string that fails validate_focus; the keyword search is the filter (mirrors itviec).
- Follow ITviec file/naming conventions exactly. Reuse `strip_html`, `JobDetail`, `_is_past` — do not reimplement.
- Default keywords: `data-engineer,ai-engineer,data-analyst`.
- Commit after each task. Do NOT push (user deploys via CI/CD).

---

### Task 1: Config — `TOPCV_KEYWORDS`

**Files:**
- Modify: `src/utils/config.py` (after the `ITVIEC_KEYWORDS` block, ~line 40)
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Produces: `config.TOPCV_KEYWORDS: list[str]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_topcv_keywords_default():
    from src.utils.config import config
    assert config.TOPCV_KEYWORDS == ["data-engineer", "ai-engineer", "data-analyst"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py::test_topcv_keywords_default -v`
Expected: FAIL with `AttributeError: ... TOPCV_KEYWORDS` (assuming no `TOPCV_KEYWORDS` env var set).

- [ ] **Step 3: Write minimal implementation**

In `src/utils/config.py`, immediately after the `ITVIEC_KEYWORDS` list comprehension block:

```python
    TOPCV_KEYWORDS: list[str] = [
        k.strip() for k in os.getenv("TOPCV_KEYWORDS", "data-engineer,ai-engineer,data-analyst").split(",") if k.strip()
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py::test_topcv_keywords_default -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/utils/config.py tests/test_config.py
git commit -m "feat(topcv): add TOPCV_KEYWORDS config"
```

---

### Task 2: Seeder — `topcv_seeder.py`

**Files:**
- Create: `src/queue/topcv_seeder.py`
- Test: `tests/queue/test_topcv_seeder.py`

**Interfaces:**
- Consumes: `src.storage.crawl_log.CrawlLog.enqueue_many(items: list[tuple[str,str]], source: str) -> int`
- Produces:
  - `extract_job_id(url: str) -> str | None`
  - `canonical_url(url: str) -> str` (strips query string)
  - `seed_from_urls(urls: list[str], log: CrawlLog | None = None) -> dict` (keys: `enqueued`, `skipped`, `rejected_url`)

- [ ] **Step 1: Write the failing test**

Create `tests/queue/test_topcv_seeder.py`:

```python
from unittest.mock import MagicMock

from src.queue.topcv_seeder import extract_job_id, canonical_url, seed_from_urls


def test_extract_job_id_valid():
    url = "https://www.topcv.vn/viec-lam/data-engineer-junior-middle/2114998.html"
    assert extract_job_id(url) == "2114998"


def test_extract_job_id_with_query():
    url = "https://www.topcv.vn/viec-lam/etl-dev/1599438.html?ta_source=JobSearchList_LinkDetail&x=1"
    assert extract_job_id(url) == "1599438"


def test_extract_job_id_rejects_non_detail():
    assert extract_job_id("https://www.topcv.vn/cong-ty/viettel-digital/246114.html") is None
    assert extract_job_id("https://www.topcv.vn/tim-viec-lam-data-engineer") is None


def test_canonical_url_strips_query():
    url = "https://www.topcv.vn/viec-lam/etl-dev/1599438.html?ta_source=x&amp;y=2"
    assert canonical_url(url) == "https://www.topcv.vn/viec-lam/etl-dev/1599438.html"


def test_seed_from_urls_counts_and_dedups():
    log = MagicMock()
    log.enqueue_many.return_value = 2
    urls = [
        "https://www.topcv.vn/viec-lam/a/111.html?ta_source=x",
        "https://www.topcv.vn/viec-lam/a/111.html?ta_source=y",  # dup id -> one item
        "https://www.topcv.vn/viec-lam/b/222.html",
        "https://www.topcv.vn/cong-ty/c/333.html",               # rejected
    ]
    result = seed_from_urls(urls, log=log)
    assert result["rejected_url"] == 1
    # enqueue_many called with 2 unique (job_id, url) pairs, source="topcv"
    items, = log.enqueue_many.call_args.args
    kwargs = log.enqueue_many.call_args.kwargs
    assert kwargs["source"] == "topcv"
    assert sorted(j for j, _ in items) == ["111", "222"]
    assert all(u == canonical_url(u) for _, u in items)
    assert result["enqueued"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/queue/test_topcv_seeder.py -v`
Expected: FAIL with `ModuleNotFoundError: src.queue.topcv_seeder`.

- [ ] **Step 3: Write minimal implementation**

Create `src/queue/topcv_seeder.py`:

```python
"""Seeder: enqueue TopCV detail URLs into raw.crawl_log."""
from __future__ import annotations

import logging
import re

from src.storage.crawl_log import CrawlLog

logger = logging.getLogger(__name__)

# TopCV detail URLs: https://www.topcv.vn/viec-lam/{slug}/{numeric_id}.html[?query]
TOPCV_URL_RE = re.compile(
    r"^https://www\.topcv\.vn/viec-lam/[\w\-]+/(\d+)\.html(?:\?.*)?$"
)


def extract_job_id(url: str) -> str | None:
    """Extract numeric job ID from a TopCV detail URL, ignoring any query string."""
    m = TOPCV_URL_RE.match(url)
    return m.group(1) if m else None


def canonical_url(url: str) -> str:
    """Drop the query string so stored URLs are stable and dedup-friendly."""
    return url.split("?", 1)[0]


def seed_from_urls(urls: list[str], log: CrawlLog | None = None) -> dict:
    """Enqueue TopCV detail URLs into crawl_log.

    Returns a counter dict: enqueued, skipped, rejected_url.
    """
    log = log or CrawlLog()
    counters = {"enqueued": 0, "skipped": 0, "rejected_url": 0}

    seen: set[str] = set()
    items: list[tuple[str, str]] = []
    for url in urls:
        job_id = extract_job_id(url)
        if not job_id:
            counters["rejected_url"] += 1
            logger.debug(f"Rejected URL (no job_id): {url}")
            continue
        if job_id in seen:
            continue
        seen.add(job_id)
        items.append((job_id, canonical_url(url)))

    counters["enqueued"] = log.enqueue_many(items, source="topcv")
    logger.info(f"TopCV seed result: {counters}")
    return counters
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/queue/test_topcv_seeder.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/queue/topcv_seeder.py tests/queue/test_topcv_seeder.py
git commit -m "feat(topcv): add detail-URL seeder"
```

---

### Task 3: Listing crawler — `crawlers/topcv/listing.py`

**Files:**
- Create: `src/crawlers/topcv/__init__.py` (empty)
- Create: `src/crawlers/topcv/listing.py`
- Test: `tests/crawlers/topcv/__init__.py` (empty), `tests/crawlers/topcv/test_listing.py`

**Interfaces:**
- Consumes: `StealthBrowser.fetch_page(url) -> str`; `MinioClient.upload_string(...)`; `config.S3_BUCKET_NAME`, `config.TOPCV_KEYWORDS`.
- Produces:
  - `extract_detail_urls(html: str) -> list[str]` (deduped canonical detail URLs)
  - `detect_max_page(html: str) -> int`
  - `class TopCVListingCrawler(browser, minio=None)` with `crawl_keyword(keyword, max_pages=None) -> list[str]` and `crawl_all_listings(keywords=None, max_pages=None) -> dict` (returns `{"counters": {...}, "urls": [...]}`)

- [ ] **Step 1: Write the failing test**

Create empty `tests/crawlers/topcv/__init__.py`, then `tests/crawlers/topcv/test_listing.py`:

```python
from unittest.mock import MagicMock

from src.crawlers.topcv.listing import (
    extract_detail_urls,
    detect_max_page,
    TopCVListingCrawler,
)

LISTING_HTML = """
<html><body>
<a href="https://www.topcv.vn/viec-lam/data-engineer/2179021.html?ta_source=JobSearchList_LinkDetail">x</a>
<a href="https://www.topcv.vn/viec-lam/data-engineer/2179021.html?ta_source=JobSearchList_ButtonApplyFormCard">x</a>
<a href="https://www.topcv.vn/viec-lam/etl-developer/1599438.html">y</a>
<a href="https://www.topcv.vn/cong-ty/acme/999.html">company (ignore)</a>
<a href="/tim-viec-lam-data-engineer?page=2">2</a>
<a href="/tim-viec-lam-data-engineer?page=5">5</a>
</body></html>
"""


def test_extract_detail_urls_dedups_and_canonicalizes():
    urls = extract_detail_urls(LISTING_HTML)
    assert urls == [
        "https://www.topcv.vn/viec-lam/data-engineer/2179021.html",
        "https://www.topcv.vn/viec-lam/etl-developer/1599438.html",
    ]


def test_detect_max_page():
    assert detect_max_page(LISTING_HTML) == 5
    assert detect_max_page("<html>no pages</html>") == 1


def test_crawl_keyword_stops_at_detected_max():
    browser = MagicMock()
    browser.fetch_page.return_value = LISTING_HTML
    minio = MagicMock()
    crawler = TopCVListingCrawler(browser=browser, minio=minio)
    # cap pages to 1 so the test does not loop to the detected max (5)
    urls = crawler.crawl_keyword("data-engineer", max_pages=1)
    assert len(urls) == 2
    minio.upload_string.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/crawlers/topcv/test_listing.py -v`
Expected: FAIL with `ModuleNotFoundError: src.crawlers.topcv.listing`.

- [ ] **Step 3: Write minimal implementation**

Create empty `src/crawlers/topcv/__init__.py`. Create `src/crawlers/topcv/listing.py`:

```python
"""TopCV listing page crawler using Playwright stealth browser.

TopCV listing pages do NOT expose an ItemList JSON-LD (unlike ITviec), so detail
URLs are extracted from the raw HTML by regex.
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Optional

from src.crawlers.browser import StealthBrowser
from src.storage.minio_client import MinioClient
from src.utils.config import config

logger = logging.getLogger(__name__)

TOPCV_BASE = "https://www.topcv.vn"
MAX_CONSECUTIVE_EMPTY = 3

# Matches a TopCV detail link, capturing everything up to `.html` (query stripped).
_DETAIL_RE = re.compile(r"https://www\.topcv\.vn/viec-lam/[\w\-]+/\d+\.html")
_PAGE_RE = re.compile(r"[?&]page=(\d+)")


def extract_detail_urls(html: str) -> list[str]:
    """Extract deduped, query-stripped job detail URLs from listing HTML."""
    urls = _DETAIL_RE.findall(html)
    return list(dict.fromkeys(urls))


def detect_max_page(html: str) -> int:
    """Parse pagination links to find the last page number."""
    max_page = 1
    for m in _PAGE_RE.finditer(html):
        try:
            max_page = max(max_page, int(m.group(1)))
        except ValueError:
            continue
    return max_page


class TopCVListingCrawler:
    """Crawl TopCV listing pages, extract job URLs via regex."""

    def __init__(self, browser: StealthBrowser, minio: MinioClient | None = None):
        self.browser = browser
        self.minio = minio or MinioClient()
        self.bucket = config.S3_BUCKET_NAME

    def _upload_html(self, html: str, keyword: str, page: int) -> None:
        slug = keyword.replace(" ", "_") or "nokeyword"
        object_name = f"listings/topcv/list_{slug}_p{page}_{int(time.time())}.html"
        self.minio.upload_string(
            bucket_name=self.bucket,
            object_name=object_name,
            content=html,
            content_type="text/html",
        )
        logger.info(f"Uploaded listing: {object_name}")

    def crawl_keyword(self, keyword: str, max_pages: Optional[int] = None) -> list[str]:
        """Crawl all pages for one keyword slug. Returns list of detail URLs."""
        all_urls: list[str] = []
        page = 1
        page_cap = max_pages or 99
        consecutive_empty = 0

        while page <= page_cap:
            url = f"{TOPCV_BASE}/tim-viec-lam-{keyword}"
            if page > 1:
                url += f"?page={page}"

            logger.info(f"Fetching listing: {url}")
            try:
                html = self.browser.fetch_page(url)
            except Exception as e:
                logger.error(f"Failed to fetch {url}: {e}")
                consecutive_empty += 1
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    logger.warning("Circuit breaker: too many consecutive failures")
                    break
                page += 1
                continue

            urls = extract_detail_urls(html)

            if not urls:
                consecutive_empty += 1
                logger.warning(f"Empty page {page} for '{keyword}' (fail #{consecutive_empty})")
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    logger.warning("Circuit breaker: stopping keyword")
                    break
                page += 1
                continue

            consecutive_empty = 0
            all_urls.extend(urls)
            logger.info(f"[{keyword}] page {page}: {len(urls)} jobs")

            try:
                self._upload_html(html, keyword, page)
            except Exception as e:
                logger.error(f"MinIO upload failed for {keyword} p{page}: {e}")

            if page == 1:
                detected_max = detect_max_page(html)
                page_cap = min(max_pages or detected_max, detected_max)
                logger.info(f"[{keyword}] detected {detected_max} pages, cap={page_cap}")

            if page >= page_cap:
                break

            page += 1
            time.sleep(2 + 2 * random.random())

        return all_urls

    def crawl_all_listings(
        self,
        keywords: list[str] | None = None,
        max_pages: Optional[int] = None,
    ) -> dict:
        """Crawl all keywords. Returns counters + collected URLs."""
        keywords = keywords or config.TOPCV_KEYWORDS
        all_urls: list[str] = []
        counters = {"keywords": 0, "pages": 0, "jobs_raw": 0}

        for kw in keywords:
            urls = self.crawl_keyword(kw, max_pages=max_pages)
            counters["keywords"] += 1
            counters["jobs_raw"] += len(urls)
            all_urls.extend(urls)
            logger.info(
                f"[topcv-listing] keyword '{kw}' done: {len(urls)} URLs"
                f" ({counters['keywords']}/{len(keywords)} keywords)"
            )

        unique = list(dict.fromkeys(all_urls))
        counters["jobs_unique"] = len(unique)
        logger.info(f"Listing crawl done: {counters}")
        return {"counters": counters, "urls": unique}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/crawlers/topcv/test_listing.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/crawlers/topcv/__init__.py src/crawlers/topcv/listing.py tests/crawlers/topcv/
git commit -m "feat(topcv): add listing crawler (regex URL extraction)"
```

---

### Task 4: Detail parser — `parsers/topcv/detail_parser.py`

**Files:**
- Create: `src/parsers/topcv/__init__.py` (empty)
- Create: `src/parsers/topcv/detail_parser.py`
- Test: `tests/parsers/topcv/__init__.py` (empty), `tests/parsers/topcv/test_detail_parser.py`
- Fixture (already captured): `tests/fixtures/topcv/detail_2114998.html.gz`

**Interfaces:**
- Consumes: `src.parsers.base.MinIOParser`; `src.parsers.vietnamworks.detail.html_cleaner.strip_html`; `src.parsers.vietnamworks.detail.schema.JobDetail`.
- Produces: `class TopCVDetailParser(MinIOParser)` with `VERSION="topcv-v1"`, `HTML_PREFIX="details/topcv/html/"`, `PARSED_PREFIX="parsed/details/topcv/"`, and `parse_html(html, source_job_id=None) -> JobDetail`; module-level `TopCVParseError`.

- [ ] **Step 1: Write the failing test**

Create empty `tests/parsers/topcv/__init__.py`, then `tests/parsers/topcv/test_detail_parser.py`:

```python
import gzip
from pathlib import Path

import pytest

from src.parsers.topcv.detail_parser import TopCVDetailParser, TopCVParseError

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "topcv" / "detail_2114998.html.gz"


@pytest.fixture
def html() -> str:
    return gzip.decompress(FIXTURE.read_bytes()).decode("utf-8")


def test_parse_real_fixture(html):
    detail = TopCVDetailParser().parse_html(html, source_job_id="2114998")
    assert detail.source == "topcv"
    # job id comes from the arg, NOT identifier.value (which is the company id 246114)
    assert detail.source_job_id == "2114998"
    assert detail.source_url == "https://www.topcv.vn/viec-lam/2114998.html"
    assert detail.title == "Data Engineer (Junior/Middle)"
    assert "VIETTEL" in (detail.company_name or "")
    assert detail.parser_version == "topcv-v1"
    # negotiable salary "Thoả thuận" -> not visible, no min/max
    assert detail.is_salary_visible is False
    assert detail.salary_min is None and detail.salary_max is None
    assert detail.salary_currency == "VND"
    # experienceRequirements.monthsOfExperience == 12 -> 1 year
    assert detail.years_of_experience == 1
    assert detail.employment_type == "FULL_TIME"
    # single-dict jobLocation normalized to one location, city from addressRegion
    assert len(detail.locations) == 1
    assert detail.locations[0]["city"] == "Hà Nội"
    assert detail.job_description_text and "Mô tả công việc" in detail.job_description_text
    assert detail.is_expired is True  # validThrough 2026-08-04 < today (run date)


def test_parse_missing_jobposting_raises():
    with pytest.raises(TopCVParseError):
        TopCVDetailParser().parse_html("<html>no json-ld</html>")
```

> NOTE on `is_expired`: the fixture's `validThrough` is `2026-08-04`. If running this test on/after that date it is `True`; if the assertion ever fails due to date, relax it to `assert detail.is_expired in (True, False)`. Keep the strict form unless it breaks.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/parsers/topcv/test_detail_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: src.parsers.topcv.detail_parser`.

- [ ] **Step 3: Write minimal implementation**

Create empty `src/parsers/topcv/__init__.py`. Create `src/parsers/topcv/detail_parser.py`:

```python
"""TopCV detail page parser: HTML -> JobDetail via JSON-LD JobPosting extraction."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from src.parsers.base import MinIOParser
from src.parsers.vietnamworks.detail.html_cleaner import strip_html
from src.parsers.vietnamworks.detail.schema import JobDetail
from src.storage.minio_client import MinioClient

logger = logging.getLogger(__name__)

# Salary value strings that mean "negotiable / not disclosed", not a real figure.
_SALARY_SENTINELS = {"thoả thuận", "thỏa thuận", "cạnh tranh", "negotiable"}


class TopCVParseError(Exception):
    pass


def _is_past(dt_str: str | None) -> bool:
    """Return True if the ISO-8601 date/datetime is in the past."""
    if not dt_str:
        return False
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt < datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def _extract_json_ld(html: str, target_type: str = "JobPosting") -> dict | None:
    """Find and parse a JSON-LD script block with the given @type (handles arrays)."""
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL,
    ):
        try:
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == target_type:
                return item
    return None


def _parse_locations(job_location) -> list[dict]:
    """Normalize TopCV jobLocation (single dict OR list) into our location format."""
    if isinstance(job_location, dict):
        job_location = [job_location]
    out: list[dict] = []
    for loc in (job_location or []):
        if not isinstance(loc, dict):
            continue
        addr = loc.get("address", {})
        if not isinstance(addr, dict):
            continue
        region = addr.get("addressRegion")
        out.append({
            "city": region,
            "city_vi": region,
            "district": addr.get("addressLocality"),
            "address": addr.get("streetAddress"),
        })
    return out


def _parse_experience(exp_req) -> int | None:
    """Extract whole years of experience from experienceRequirements."""
    if not isinstance(exp_req, dict):
        return None
    months = exp_req.get("monthsOfExperience")
    if months is None:
        return None
    try:
        return max(1, int(float(months)) // 12)
    except (ValueError, TypeError):
        return None


def _parse_skills(skills) -> list[dict]:
    """Parse skills (comma string) into a list of dicts; TopCV often omits this."""
    if not skills or not isinstance(skills, str):
        return []
    return [{"name": s.strip()} for s in skills.split(",") if s.strip()]


class TopCVDetailParser(MinIOParser):
    VERSION = "topcv-v1"
    HTML_PREFIX = "details/topcv/html/"
    PARSED_PREFIX = "parsed/details/topcv/"

    def __init__(self, minio: MinioClient | None = None):
        super().__init__(minio)

    def parse_html(self, html: str, source_job_id: str | None = None) -> JobDetail:
        data = _extract_json_ld(html, "JobPosting")
        if data is None:
            raise TopCVParseError("No JobPosting JSON-LD found")

        org = data.get("hiringOrganization") or {}
        salary = data.get("baseSalary") or {}
        salary_val = salary.get("value") or {}

        salary_display = None
        salary_min = None
        salary_max = None
        is_salary_visible = False

        if isinstance(salary_val, dict):
            raw_val = salary_val.get("value")
            if isinstance(raw_val, str) and raw_val.strip().lower() not in _SALARY_SENTINELS:
                salary_display = raw_val.strip() or None

            mv = salary_val.get("minValue")
            xv = salary_val.get("maxValue")
            if mv is not None or xv is not None:
                try:
                    salary_min = float(mv) if mv is not None else None
                    salary_max = float(xv) if xv is not None else None
                    is_salary_visible = salary_min is not None or salary_max is not None
                except (ValueError, TypeError):
                    pass

        job_id = source_job_id or ""
        source_url = f"https://www.topcv.vn/viec-lam/{job_id}.html" if job_id else None

        return JobDetail(
            source="topcv",
            source_job_id=job_id,
            source_url=source_url,
            parser_version=self.VERSION,
            parsed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            title=data.get("title"),
            company_name=org.get("name") if isinstance(org, dict) else None,
            company_logo_url=org.get("logo") if isinstance(org, dict) else None,
            company_profile_text=None,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary.get("currency"),
            is_salary_visible=is_salary_visible,
            pretty_salary=salary_display,
            years_of_experience=_parse_experience(data.get("experienceRequirements")),
            employment_type=data.get("employmentType"),
            job_function=data.get("industry") or "IT",
            locations=_parse_locations(data.get("jobLocation")),
            skills=_parse_skills(data.get("skills")),
            benefits=[],
            job_description_text=strip_html(data.get("description")),
            job_requirement_text=None,
            posted_at=data.get("datePosted"),
            expired_at=data.get("validThrough"),
            is_expired=_is_past(data.get("validThrough")),
            is_active=not _is_past(data.get("validThrough")),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/parsers/topcv/test_detail_parser.py -v`
Expected: PASS (2 tests). If `test_parse_real_fixture` fails only on the `is_expired` assertion, apply the NOTE in Step 1.

- [ ] **Step 5: Commit**

```bash
git add src/parsers/topcv/ tests/parsers/topcv/ tests/fixtures/topcv/
git commit -m "feat(topcv): add detail parser (JSON-LD JobPosting) + fixture"
```

---

### Task 5: Detail crawler — `crawlers/topcv/detail.py`

**Files:**
- Create: `src/crawlers/topcv/detail.py`
- Test: `tests/crawlers/topcv/test_detail.py`

**Interfaces:**
- Consumes: `StealthBrowser.fetch_page(url) -> str`; `CrawlLog.claim_next(source)`, `.mark_success(job_id, status, key, run_id, source)`, `.mark_failed(job_id, status, err, source)`; `MinioClient.upload_bytes(bucket, key, payload, ctype)`; `CircuitBreaker`; `jitter_sleep`; `safety`.
- Produces: `class TopCVDetailCrawler(browser, log, minio, run_id=None, breaker=None)` with `process_one(job_id, url) -> str` and `run(max_jobs=None) -> dict`. Module constant `SOURCE="topcv"`.

- [ ] **Step 1: Write the failing test**

Create `tests/crawlers/topcv/test_detail.py`:

```python
import gzip
from unittest.mock import MagicMock

from src.crawlers.topcv.detail import TopCVDetailCrawler

URL = "https://www.topcv.vn/viec-lam/data-engineer/2114998.html"


def make_crawler(claim_jobs, fetch_side_effect):
    browser = MagicMock()
    browser.fetch_page.side_effect = fetch_side_effect
    log = MagicMock()
    log.claim_next.side_effect = list(claim_jobs) + [None]
    minio = MagicMock()
    breaker = MagicMock()
    breaker.is_open.return_value = False
    crawler = TopCVDetailCrawler(
        browser=browser, log=log, minio=minio, run_id="testrun", breaker=breaker,
    )
    return crawler, log, minio


def test_run_success_uploads_gzip_and_marks_success(monkeypatch):
    monkeypatch.setattr("src.crawlers.topcv.detail.jitter_sleep", lambda *a, **k: None)
    monkeypatch.setattr("src.crawlers.topcv.detail.safety.is_killed", lambda: False)

    body = "<html>" + "x" * 2000 + "</html>"
    crawler, log, minio = make_crawler(
        claim_jobs=[("2114998", URL)],
        fetch_side_effect=[body],
    )

    counters = crawler.run()
    assert counters["success"] == 1

    minio.upload_bytes.assert_called_once()
    bucket, key, payload, ctype = minio.upload_bytes.call_args.args
    assert key == "details/topcv/html/testrun/2114998.html.gz"
    assert ctype == "application/gzip"
    assert gzip.decompress(payload).decode() == body
    log.mark_success.assert_called_once_with(
        "2114998", 200, "details/topcv/html/testrun/2114998.html.gz", "testrun", source="topcv"
    )


def test_run_tiny_response_marks_failed(monkeypatch):
    monkeypatch.setattr("src.crawlers.topcv.detail.jitter_sleep", lambda *a, **k: None)
    monkeypatch.setattr("src.crawlers.topcv.detail.safety.is_killed", lambda: False)

    crawler, log, minio = make_crawler(
        claim_jobs=[("2114998", URL)],
        fetch_side_effect=["<html>tiny</html>"],
    )
    counters = crawler.run()
    assert counters["failed"] == 1
    minio.upload_bytes.assert_not_called()
    log.mark_failed.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/crawlers/topcv/test_detail.py -v`
Expected: FAIL with `ModuleNotFoundError: src.crawlers.topcv.detail`.

- [ ] **Step 3: Write minimal implementation**

Create `src/crawlers/topcv/detail.py`:

```python
"""TopCV detail page crawler using Playwright stealth browser."""
from __future__ import annotations

import gzip
import logging
import time
import uuid

from src.crawlers.browser import StealthBrowser
from src.utils.circuit_breaker import CircuitBreaker
from src.utils.rate_limiter import jitter_sleep
from src.storage.crawl_log import CrawlLog
from src.storage.minio_client import MinioClient
from src.utils import safety
from src.utils.config import config

logger = logging.getLogger(__name__)

SOURCE = "topcv"


class TopCVDetailCrawler:
    """Fetch TopCV detail pages via Playwright and store HTML in MinIO."""

    def __init__(
        self,
        browser: StealthBrowser,
        log: CrawlLog,
        minio: MinioClient,
        run_id: str | None = None,
        breaker: CircuitBreaker | None = None,
    ):
        self.browser = browser
        self.log = log
        self.minio = minio
        self.bucket = config.S3_BUCKET_NAME
        self.run_id = run_id or time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.breaker = breaker or CircuitBreaker()

    def _object_key(self, job_id: str) -> str:
        return f"details/topcv/html/{self.run_id}/{job_id}.html.gz"

    def process_one(self, job_id: str, url: str) -> str:
        """Fetch + store a single job. Returns final status string."""
        try:
            html = self.browser.fetch_page(url)
        except Exception as e:
            logger.error(f"Fetch failed for {url}: {e}")
            self.breaker.record(None)
            self.log.mark_failed(job_id, None, str(e), source=SOURCE)
            return "failed"

        if not html or len(html) < 1000:
            logger.warning(f"Empty/tiny response for {url} ({len(html or '')} chars)")
            self.breaker.record(None)
            self.log.mark_failed(job_id, None, "empty response", source=SOURCE)
            return "failed"

        lowered = html[:4000].lower()
        if "attention required" in lowered or "access denied" in lowered:
            logger.error(f"BLOCKED on {url}")
            self.breaker.record(403)
            self.log.mark_failed(job_id, 403, "cloudflare block detected", source=SOURCE)
            safety.trigger()
            return "failed"

        self.breaker.record(200)
        key = self._object_key(job_id)
        payload = gzip.compress(html.encode("utf-8"))
        self.minio.upload_bytes(self.bucket, key, payload, "application/gzip")
        self.log.mark_success(job_id, 200, key, self.run_id, source=SOURCE)
        logger.info(f"OK {job_id} ({len(payload)}B gz) -> {key}")
        return "success"

    def run(self, max_jobs: int | None = None) -> dict:
        """Process pending TopCV jobs from crawl_log queue."""
        counters = {"success": 0, "failed": 0, "skipped": 0}

        if safety.is_killed():
            logger.warning("Kill switch active before start, aborting")
            return counters

        processed = 0
        while True:
            if max_jobs is not None and processed >= max_jobs:
                break
            if safety.is_killed():
                logger.warning("Kill switch active, stopping")
                break
            if self.breaker.is_open():
                logger.warning(f"Breaker open ({self.breaker.reason()}), stopping")
                break

            job = self.log.claim_next(source=SOURCE)
            if job is None:
                logger.info("No more pending TopCV jobs")
                break
            job_id, url = job

            outcome = self.process_one(job_id, url)
            counters[outcome] = counters.get(outcome, 0) + 1
            processed += 1
            logger.info(
                f"[topcv] {processed}/{max_jobs or '?'} {outcome}"
                f" — job_id={job_id} (ok={counters['success']} fail={counters['failed']})"
            )

            jitter_sleep(config.CRAWLER_RATE_SECONDS)

        logger.info(f"TopCV detail run finished: {counters}")
        return counters
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/crawlers/topcv/test_detail.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/crawlers/topcv/detail.py tests/crawlers/topcv/test_detail.py
git commit -m "feat(topcv): add detail crawler"
```

---

### Task 6: Prefect flow — `orchestration/flows/topcv_pipeline.py`

**Files:**
- Create: `orchestration/flows/topcv_pipeline.py`
- Test: `tests/orchestration/__init__.py` (empty, if missing), `tests/orchestration/test_topcv_pipeline_import.py`

**Interfaces:**
- Consumes: everything from Tasks 2–5, plus `orchestration.flows._shared` (`counters_table`, `dispatch_dashboard_alerts`, `fmt_duration`, `run_dbt`, `run_normalizer`), `JobDetailLoader`.
- Produces: `topcv_pipeline(keywords=None, max_listing_pages=None, detail_max_jobs=None, force_reparse=False) -> dict`; Prefect flow named `topcv-pipeline`.

- [ ] **Step 1: Write the failing test** (import smoke test — the flow has no unit-testable pure logic)

Create `tests/orchestration/__init__.py` (empty) if absent, then `tests/orchestration/test_topcv_pipeline_import.py`:

```python
def test_topcv_pipeline_imports_and_is_a_flow():
    from orchestration.flows.topcv_pipeline import topcv_pipeline
    # Prefect flows expose a .name attribute
    assert topcv_pipeline.name == "topcv-pipeline"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/orchestration/test_topcv_pipeline_import.py -v`
Expected: FAIL with `ModuleNotFoundError: orchestration.flows.topcv_pipeline`.

- [ ] **Step 3: Write minimal implementation**

Create `orchestration/flows/topcv_pipeline.py`:

```python
"""TalentPulse TopCV pipeline orchestrated with Prefect.

    listing_crawl -> seed_queue -> detail_crawl -> detail_parse -> load_warehouse -> dbt_transform
"""
from __future__ import annotations

import time

from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact

from orchestration.flows._shared import counters_table, dispatch_dashboard_alerts, fmt_duration, run_dbt, run_normalizer
from src.utils.config import config
from src.crawlers.browser import StealthBrowser
from src.crawlers.topcv.detail import TopCVDetailCrawler
from src.crawlers.topcv.listing import TopCVListingCrawler
from src.loaders.job_detail_loader import JobDetailLoader
from src.parsers.topcv.detail_parser import TopCVDetailParser
from src.queue.topcv_seeder import seed_from_urls
from src.storage.crawl_log import CrawlLog
from src.storage.minio_client import MinioClient


TOPCV_PARSED_PREFIX = "parsed/details/topcv/"


@task(name="topcv_listing_crawl", retries=1, timeout_seconds=1800)
def listing_crawl(keywords: list[str], max_pages: int | None = None) -> list[str]:
    logger = get_run_logger()
    t0 = time.time()
    with StealthBrowser() as browser:
        crawler = TopCVListingCrawler(browser=browser)
        result = crawler.crawl_all_listings(keywords=keywords, max_pages=max_pages)
    dur = time.time() - t0
    logger.info(f"Listing done: {result['counters']}")
    create_markdown_artifact(
        markdown=counters_table("topcv", "listing_crawl", result["counters"], dur),
        key="topcv-listing",
        description="TopCV listing crawl results",
    )
    return result["urls"]


@task(name="topcv_seed_queue", retries=1)
def seed_queue(urls: list[str]) -> dict:
    t0 = time.time()
    result = seed_from_urls(urls)
    dur = time.time() - t0
    get_run_logger().info(f"Seed result: {result}")
    create_markdown_artifact(
        markdown=counters_table("topcv", "seed_queue", result, dur),
        key="topcv-seed",
        description="TopCV queue seeding results",
    )
    return result


@task(name="topcv_detail_crawl", retries=1, timeout_seconds=3600)
def detail_crawl(max_jobs: int | None = None) -> dict:
    logger = get_run_logger()
    t0 = time.time()
    with StealthBrowser() as browser:
        crawler = TopCVDetailCrawler(
            browser=browser,
            log=CrawlLog(),
            minio=MinioClient(),
        )
        result = crawler.run(max_jobs=max_jobs)
    dur = time.time() - t0
    logger.info(f"Detail crawl done: {result}")
    create_markdown_artifact(
        markdown=counters_table("topcv", "detail_crawl", result, dur),
        key="topcv-detail-crawl",
        description="TopCV detail crawl results",
    )
    return result


@task(name="topcv_detail_parse", retries=1)
def detail_parse(force: bool = False) -> dict:
    t0 = time.time()
    result = TopCVDetailParser().run_batch(force=force)
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("topcv", "detail_parse", result, dur),
        key="topcv-parse",
        description="TopCV parse results",
    )
    return result


@task(name="topcv_load_warehouse", retries=2)
def load_warehouse() -> dict:
    t0 = time.time()
    result = JobDetailLoader().run_batch(prefix=TOPCV_PARSED_PREFIX)
    dur = time.time() - t0
    create_markdown_artifact(
        markdown=counters_table("topcv", "load_warehouse", result, dur),
        key="topcv-load",
        description="TopCV warehouse load results",
    )
    return result


@task(name="topcv_dbt_transform", retries=1, timeout_seconds=600)
def dbt_transform() -> str:
    return run_dbt()


@task(name="topcv_normalize", retries=1, timeout_seconds=600)
def normalize() -> str:
    return run_normalizer()


@task(name="topcv_dispatch_alerts", retries=1, timeout_seconds=180)
def dispatch_alerts() -> dict:
    return dispatch_dashboard_alerts(source="topcv_etl")


@flow(name="topcv-pipeline")
def topcv_pipeline(
    keywords: list[str] | None = None,
    max_listing_pages: int | None = None,
    detail_max_jobs: int | None = None,
    force_reparse: bool = False,
) -> dict:
    """End-to-end TopCV pipeline."""
    flow_t0 = time.time()
    keywords = keywords or config.TOPCV_KEYWORDS
    urls = listing_crawl(keywords, max_listing_pages)
    seed_result = seed_queue(urls)
    crawl_result = detail_crawl(max_jobs=detail_max_jobs)
    parse_result = detail_parse(force=force_reparse)
    load_result = load_warehouse()
    norm_result = normalize()
    dbt_result = dbt_transform()
    alert_result = dispatch_alerts()

    total_dur = fmt_duration(time.time() - flow_t0)
    summary = (
        "## Pipeline Summary -- TopCV\n"
        "| Stage | Result |\n|-------|--------|\n"
        f"| Listing | {len(urls)} URLs collected |\n"
        f"| Seed | {seed_result.get('enqueued', 0)} enqueued, {seed_result.get('skipped', 0)} skipped |\n"
        f"| Detail Crawl | {crawl_result.get('success', 0)} success, {crawl_result.get('failed', 0)} failed |\n"
        f"| Parse | {parse_result.get('success', 0)} success, {parse_result.get('failed', 0)} failed |\n"
        f"| Load | {load_result.get('loaded', 0)} loaded, {load_result.get('rejected', 0)} rejected |\n"
        f"| Normalize | {norm_result} |\n"
        f"| dbt | {dbt_result} |\n"
        f"| Alerts | {alert_result.get('dispatched', 0)} dispatched |\n"
        f"| **Total Duration** | **{total_dur}** |"
    )
    create_markdown_artifact(
        markdown=summary,
        key="topcv-pipeline-summary",
        description=f"TopCV pipeline run summary ({total_dur})",
    )

    return {
        "seed": seed_result,
        "crawl": crawl_result,
        "parse": parse_result,
        "load": load_result,
        "normalize": norm_result,
        "dbt": dbt_result,
        "alerts": alert_result,
    }


if __name__ == "__main__":
    import os

    if os.getenv("PREFECT_DEPLOY", "0") == "1":
        topcv_pipeline.serve(
            name="topcv-pipeline-daily",
            cron="0 5 * * *",
            tags=["topcv", "etl"],
            parameters={
                "keywords": config.TOPCV_KEYWORDS,
            },
        )
    else:
        topcv_pipeline()
```

> Cron note: ITviec uses `0 4 * * *` (UTC). TopCV uses `0 5 * * *` to stagger the two browser-heavy crawls one hour apart.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/orchestration/test_topcv_pipeline_import.py -v`
Expected: PASS. (This only imports the module — it does not run the flow.)

- [ ] **Step 5: Commit**

```bash
git add orchestration/flows/topcv_pipeline.py tests/orchestration/
git commit -m "feat(topcv): add Prefect pipeline flow"
```

---

### Task 7: CI/CD wiring (dedicated TopCV image)

**Files:**
- Create: `orchestration/topcv-requirements.txt`
- Create: `orchestration/Dockerfile.worker.topcv`
- Modify: `.github/workflows/deploy.yml` (build matrix, ~lines 50-56)
- Create: `.github/workflows/pipeline-topcv.yml`
- Modify: `.github/actions/run-flow/action.yml` (add `-e TOPCV_KEYWORDS`, ~line 79)

This task has no unit test; verification is file-content + a YAML sanity parse.

- [ ] **Step 1: Create the requirements file**

Create `orchestration/topcv-requirements.txt` (identical to `itviec-requirements.txt`):

```text
# TopCV worker: base deps + Playwright + Prefect (not in base image).
-r base-requirements.txt
playwright>=1.44.0
prefect==2.16.5
griffe<1.0
```

- [ ] **Step 2: Create the Dockerfile**

Create `orchestration/Dockerfile.worker.topcv` (copy of `Dockerfile.worker.itviec`, swapping the requirements filename):

```dockerfile
FROM mcr.microsoft.com/playwright/python:jammy
RUN ln -sf /usr/share/zoneinfo/Asia/Ho_Chi_Minh /etc/localtime && echo "Asia/Ho_Chi_Minh" > /etc/timezone
ENV TZ=Asia/Ho_Chi_Minh
WORKDIR /app
COPY orchestration/base-requirements.txt orchestration/topcv-requirements.txt ./
RUN pip install --no-cache-dir -r topcv-requirements.txt
RUN playwright install chromium
ENV PYTHONPATH=/app
COPY src/ ./src/
COPY orchestration/ ./orchestration/
COPY dbt_transform/ ./dbt_transform/
COPY configs/ ./configs/
COPY scripts/ ./scripts/
RUN cd dbt_transform && dbt deps --profiles-dir .
```

- [ ] **Step 3: Add the deploy.yml build-matrix entry**

In `.github/workflows/deploy.yml`, in the `matrix:` block that currently lists `worker`/`itviec`/`linkedin`, add after the `linkedin` entry (match the existing indentation exactly):

```yaml
          - image_suffix: topcv
            dockerfile: orchestration/Dockerfile.worker.topcv
```

- [ ] **Step 4: Add `-e TOPCV_KEYWORDS` to run-flow**

In `.github/actions/run-flow/action.yml`, inside the `docker run --rm \` env list, add a line after `-e ITVIEC_KEYWORDS \` (keep the trailing backslash and indentation):

```yaml
          -e TOPCV_KEYWORDS \
```

- [ ] **Step 5: Create the pipeline workflow**

Create `.github/workflows/pipeline-topcv.yml` (copy of `pipeline-itviec.yml` with topcv substitutions and a staggered cron):

```yaml
name: Pipeline - TopCV

# Cron is UTC. "0 5 * * *" UTC == 12:00 Asia/Ho_Chi_Minh (UTC+7).
# Staggered one hour after ITviec (04:00 UTC) to avoid overlapping browser crawls.
on:
  schedule:
    - cron: "0 5 * * *"
  workflow_dispatch: {}

concurrency:
  group: pipeline-topcv
  cancel-in-progress: false

permissions:
  contents: read
  packages: read

jobs:
  run:
    name: Run topcv-pipeline (run-once)
    runs-on: ubuntu-latest
    timeout-minutes: 45
    env:
      PREFECT_API_URL: http://${{ vars.WAREHOUSE_TAILNET_IP }}:4200/api

      DB_HOST: ${{ vars.WAREHOUSE_TAILNET_IP }}
      DB_PORT: "5432"
      DB_USER: ${{ secrets.DB_USER }}
      DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
      DB_NAME: ${{ vars.DB_NAME || 'warehouse' }}

      S3_ENDPOINT_URL: ${{ secrets.S3_ENDPOINT_URL }}
      S3_ACCESS_KEY: ${{ secrets.S3_ACCESS_KEY }}
      S3_SECRET_KEY: ${{ secrets.S3_SECRET_KEY }}
      S3_BUCKET_NAME: ${{ vars.S3_BUCKET_NAME || 'talentpulse-raw' }}

      CRAWLER_CONTACT_EMAIL: ${{ vars.CRAWLER_CONTACT_EMAIL }}
      TOPCV_KEYWORDS: ${{ vars.TOPCV_KEYWORDS || 'data-engineer,ai-engineer,data-analyst' }}
      FOCUS_KEYWORDS: ${{ vars.FOCUS_KEYWORDS || 'data engineer,data analyst,ai,machine learning,data science,data scientist,big data,analytics,business development,business analyst,technical sales' }}
      SKIP_FOCUS_SOURCES: ${{ vars.SKIP_FOCUS_SOURCES || 'itviec' }}

      DASHBOARD_API_URL: http://${{ vars.WEB_TAILNET_IP }}:8001
      ALERT_DISPATCH_SECRET: ${{ secrets.ALERT_DISPATCH_SECRET }}
      TELEGRAM_WEBHOOK_SECRET: ${{ secrets.TELEGRAM_WEBHOOK_SECRET }}
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Run topcv-pipeline
        uses: ./.github/actions/run-flow
        with:
          image-suffix: topcv
          module: orchestration.flows.topcv_pipeline
          image-tag: develop
          ts-oauth-client-id: ${{ secrets.TS_OAUTH_CLIENT_ID }}
          ts-oauth-secret: ${{ secrets.TS_OAUTH_SECRET }}
          github-token: ${{ secrets.GITHUB_TOKEN }}

# GitHub registers schedule/workflow_dispatch workflows only when the file is
# added or modified in a push to the default branch. This line forces that.
```

- [ ] **Step 6: Sanity-check the YAML parses**

Run:
```bash
.venv/Scripts/python.exe -c "import yaml; [yaml.safe_load(open(f,encoding='utf-8')) for f in ['.github/workflows/pipeline-topcv.yml','.github/workflows/deploy.yml','.github/actions/run-flow/action.yml']]; print('yaml ok')"
```
Expected: `yaml ok` (no exception). If `yaml` is missing: `.venv/Scripts/python.exe -m pip install pyyaml` first.

- [ ] **Step 7: Commit**

```bash
git add orchestration/topcv-requirements.txt orchestration/Dockerfile.worker.topcv \
  .github/workflows/deploy.yml .github/workflows/pipeline-topcv.yml .github/actions/run-flow/action.yml
git commit -m "ci(topcv): add worker image, pipeline workflow, and env wiring"
```

---

### Task 8: Full-suite regression + docs

**Files:**
- Modify (optional): `D:\TalentPulse\CLAUDE.md` — mention topcv source under `pipeline_data`.

- [ ] **Step 1: Run the whole test suite for the new source**

Run:
```bash
.venv/Scripts/python.exe -m pytest tests/queue/test_topcv_seeder.py tests/crawlers/topcv/ tests/parsers/topcv/ tests/orchestration/test_topcv_pipeline_import.py tests/test_config.py -v
```
Expected: all PASS.

- [ ] **Step 2: Confirm no regressions elsewhere**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: no NEW failures introduced by this work (pre-existing failures, if any, unchanged).

- [ ] **Step 3 (optional): note topcv in the workspace guide**

If desired, add `topcv` alongside `itviec` in `D:\TalentPulse\CLAUDE.md`'s pipeline_data description. Skip if the user prefers minimal doc churn.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test(topcv): full-suite regression pass"
```

---

## Post-implementation notes (for the operator, not steps)

- **Do not push** — the user deploys via CI/CD. When ready: `git push origin develop` (bare push is safe in `pipeline_data`).
- **GHA registration:** `pipeline-topcv.yml` only becomes schedulable after it lands on the default branch (`develop`). The trailing-comment trick forces registration.
- **First real run:** trigger `Pipeline - TopCV` via `workflow_dispatch` and watch the Prefect artifacts; expect the `topcv` Docker image build (~15-20 min cold) on the first `deploy.yml` run.
- **Follow-up (out of scope):** salary min/max once a numeric TopCV posting is observed; `jobBenefits` HTML → `benefits`; non-IT keyword expansion.
