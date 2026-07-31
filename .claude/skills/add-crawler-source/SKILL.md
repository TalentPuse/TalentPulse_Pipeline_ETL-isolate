---
name: add-crawler-source
description: End-to-end checklist for adding a new job board (or changing an existing one) across crawler, parser, seeder, loader, flow, Docker image, and GitHub Actions workflow. Use when adding a source, when a source's crawler/parser needs restructuring, or when checking whether an existing source is wired everywhere it should be.
---

# Adding a job source

Existing sources: `vietnamworks`, `itviec`, `linkedin`, `topcv`. The `source`
string is the partition key everywhere — `raw.crawl_log` PK, `raw.job_detail` PK,
object-storage prefixes, `SKIP_FOCUS_SOURCES`. Pick it once, lowercase, and use it
verbatim in all seven places below.

## The seven touch points

**1. Crawler — `src/crawlers/<source>/`**
Two modules: `listing.py` (discover job URLs/ids) and `detail.py` (fetch + gzip +
upload to object storage + update `crawl_log`). Pick a transport:

| Transport | Used by | When |
|---|---|---|
| `requests`/`httpx` against a JSON API | vietnamworks (`ms.vietnamworks.com/job-search`) | site exposes a search API — always prefer this |
| `src/crawlers/browser.py::StealthBrowser` (Playwright) | itviec, topcv | Cloudflare |
| dedicated `fetcher.py` + UA pool | linkedin | needs per-request UA/proxy rotation |

`StealthBrowser` options that matter: `persist_cookies=True` keeps the Cloudflare
`cf_clearance` token across requests (topcv needs it; other sources clear cookies
each fetch to reset the CF session); `min_len` + `retries` treat a short response
as a challenge page and re-fetch (topcv serves ~27 KB challenge vs ~1.6 MB real).

Every detail crawler must: honour `src/utils/safety.is_killed()`, record outcomes
into a `CircuitBreaker`, `jitter_sleep(config.CRAWLER_RATE_SECONDS)` between jobs,
and catch *unexpected* exceptions around `process_one` so a claimed row never stays
stuck in `in_progress`. Object key convention:
`details/<source>/html/{run_id}/{job_id}.html.gz`.

**2. Parser — `src/parsers/<source>/detail_parser.py`**
Subclass the base in `src/parsers/base.py` and set `VERSION`, `HTML_PREFIX`,
`PARSED_PREFIX`. Prefixes in use: `parsed/details/{vietnamworks,itviec,linkedin,topcv}/`.
Output JSON must carry `source`, `source_job_id`, `parsed_at`, `parser_version` —
the loader hard-fails without the first two and the repo raises without `parsed_at`.

**3. Seeder — `src/queue/<source>_seeder.py`**
Shape depends on what listing returns: `seed_from_listings(prefix)` (vnw, reads
listing JSON back out of storage), `seed_from_urls(urls)` (itviec, topcv),
`seed_from_job_ids(job_ids)` (linkedin). All of them must go through
`CrawlLog.enqueue_many` — never a per-row loop, see the `warehouse-io` skill.

**4. Loader prefix**
`JobDetailLoader.run_batch(prefix=...)`. `PARSED_PREFIX` in
`src/loaders/job_detail_loader.py` still defaults to vietnamworks; every other
flow passes its own `<SOURCE>_PARSED_PREFIX` constant explicitly. Do the same.

**5. Flow — `orchestration/flows/<source>_pipeline.py`**
Copy the closest existing flow and keep the stage order. See the `prefect-flows`
skill for retry/timeout budgets — in particular, Playwright-based detail crawls
use `retries=0`.

**6. Docker image — `orchestration/Dockerfile.worker.<source>`**
A separate image exists per source because the browser-based ones need Playwright
+ Chromium and the plain ones do not. Add the new file to the `strategy.matrix`
in `.github/workflows/deploy.yml` (`image_suffix` + `dockerfile`), or the image
is never built and the pipeline workflow pulls a tag that does not exist.

**7. Workflow — `.github/workflows/pipeline-<source>.yml`**
Copy an existing one. It supplies the `env:` block and calls the composite action
`./.github/actions/run-flow` with `image-suffix` + `module`. Any new env var must
be added to **both** the workflow's `env:` and the `docker run -e` list inside
`.github/actions/run-flow/action.yml`.

## Tests

`tests/crawlers/<source>/`, `tests/parsers/<source>/`, `tests/queue/`. Parser tests
use recorded fixtures (`_fixtures.py`), not live HTTP. Crawler tests stub the
fetcher/browser. `asyncio_mode=auto` is set in `pyproject.toml`.

## Config

`Config.CRAWL_KEYWORDS` in `src/utils/config.py` is the single hardcoded source of
truth for what to search — **not** env vars. VNW/LinkedIn search by text form,
ITviec/TopCV by the slugified form (`ITVIEC_KEYWORDS`). The `*_KEYWORDS` env vars
that the GHA workflows still pass are inert; do not add new ones expecting them to
work. Focus filtering is off by policy (`validators.validate`).
