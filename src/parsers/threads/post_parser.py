"""Map a Threads post onto `JobDetail` — and be honest that the fit is poor.

## The impedance mismatch (read this before trusting a threads row)

`JobDetail` was designed around a job-board posting: a structured record with a
title, a hiring company, a salary band, a location, an expiry date. A Threads
post is a paragraph of free text written by a person. The API gives us exactly
nine fields, and only four of them carry any hiring signal:

    id          -> source_job_id
    permalink   -> source_url
    timestamp   -> posted_at
    text        -> job_description_text
    username      the AUTHOR'S ACCOUNT HANDLE — not a company (see below)
    media_type    IMAGE / TEXT / VIDEO / CAROUSEL_ALBUM — no hiring signal
    has_replies / is_quote_post / is_reply — thread structure, no hiring signal

Everything else on `JobDetail` — `title`, `company_name`, `salary_min/max`,
`locations`, `employment_type`, `job_level`, `expired_at`, `industries`,
`benefits` — has **no counterpart in the payload**. This module leaves all of
them `None`/empty. It does not derive a title from the first line of the text,
and it does not put `@username` in `company_name`:

* `username` is an account handle. It may be a recruiter, an agency, a company
  page, or someone resharing. Writing it into `company_name` would flow into
  `mart_company_hiring` and invent companies that do not exist.
* A first-line-of-text "title" would flow into `fct_jobs_daily` and the job
  board as if it were a real job title.

Both would be silent fabrication in a gold mart, which is worse than a gap.

## Consequence you must know about

`src/loaders/validators.py::validate_business_rules` rejects any payload with no
`title` or no `company_name` (`MISSING_TITLE` / `MISSING_COMPANY`). Since this
parser produces neither, **every threads row is quarantined into
`raw.job_detail_rejects` and none reach `raw.job_detail`** while validation is
on. That is the correct default — it keeps fabricated rows out of the warehouse
— but it also means this source produces no warehouse data until one of two
decisions is made:

  (a) give Threads its own table (e.g. `raw.social_post`) with a schema that
      fits a social post, plus an LLM extraction step that turns the free text
      into title/company/skills before anything reaches the job marts; or
  (b) relax the validator for `source = 'threads'` and accept NULL
      title/company in `raw.job_detail`, then teach every gold mart to cope.

(a) is the honest one. This parser is written so that either path can reuse it:
the mapping below is lossless with respect to what the API actually returns.

`orchestration/flows/threads_pipeline.py` therefore stops after the parse stage
by default and says so loudly instead of shipping an all-rejects load.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from src.parsers.base import MinIOParser
from src.parsers.vietnamworks.detail.schema import JobDetail
from src.storage.minio_client import MinioClient

logger = logging.getLogger(__name__)


class ThreadsParseError(Exception):
    """Raised when the payload is not a usable Threads post."""


def parse_post(post: dict, source_job_id: str | None = None) -> JobDetail:
    """Map one Threads post dict onto `JobDetail`. Pure — no I/O.

    Raises `ThreadsParseError` when there is no post id, because
    `source_job_id` is a primary-key component in `raw.job_detail` and the
    loader hard-fails without it.
    """
    if not isinstance(post, dict):
        raise ThreadsParseError(f"expected a dict, got {type(post).__name__}")

    post_id = str(post.get("id") or source_job_id or "").strip()
    if not post_id:
        raise ThreadsParseError("post has no `id` and no fallback source_job_id")

    text = post.get("text")
    text = text.strip() if isinstance(text, str) and text.strip() else None

    return JobDetail(
        source="threads",
        source_job_id=post_id,
        source_url=post.get("permalink"),
        parser_version=ThreadsPostParser.VERSION,
        parsed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # title / company_name / salary / locations / expiry: deliberately absent.
        # See the module docstring — the API returns nothing that maps to them,
        # and guessing would fabricate rows in the gold marts.
        job_description_text=text,
        posted_at=post.get("timestamp"),
    )


class ThreadsPostParser(MinIOParser):
    """Read archived Threads posts (`.json.gz`) and write parsed `JobDetail` JSON.

    Subclasses `MinIOParser` for `process_one`'s download -> parse -> upload
    shape, but `run_batch` is overridden: the base implementation only picks up
    keys ending in `.html.gz`, and Threads archives JSON.
    """

    VERSION = "threads-v1"
    HTML_PREFIX = "details/threads/json/"
    PARSED_PREFIX = "parsed/details/threads/"
    OBJECT_SUFFIX = ".json.gz"

    def __init__(self, minio: MinioClient | None = None):
        super().__init__(minio)

    def _extract_job_id(self, html_key: str) -> str:
        return html_key.split("/")[-1].replace(self.OBJECT_SUFFIX, "")

    def parse_html(self, html: str, source_job_id: str | None = None) -> JobDetail:
        """`html` is the decompressed archive body — JSON text, not HTML.

        The parameter keeps the `MinIOParser` signature; renaming it would break
        the base class contract for no gain.
        """
        try:
            post = json.loads(html)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ThreadsParseError(f"archived post is not valid JSON: {exc}") from exc
        return parse_post(post, source_job_id=source_job_id)

    def run_batch(
        self, prefix: str | None = None, *, force: bool = False, max_workers: int = 16
    ) -> dict:
        """Same as `MinIOParser.run_batch` but keyed on `.json.gz`."""
        prefix = prefix or self.HTML_PREFIX
        keys: list[str] = []
        paginator = self.minio.s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                if obj["Key"].endswith(self.OBJECT_SUFFIX):
                    keys.append(obj["Key"])

        counters = {"success": 0, "skipped": 0, "failed": 0}
        workers = max(1, min(max_workers, len(keys)))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.process_one, k, force=force): k for k in keys}
            for fut in as_completed(futures):
                key = futures[fut]
                try:
                    counters["skipped" if fut.result() is None else "success"] += 1
                except Exception as exc:
                    logger.error(f"Parse failed {key}: {exc}")
                    counters["failed"] += 1

        logger.info(f"threads parse batch done: {counters} ({len(keys)} files)")
        return counters


def parse_posts_to_dicts(posts: list[dict]) -> tuple[list[dict], int]:
    """In-memory convenience: map posts -> payload dicts, return (payloads, failed).

    Used by callers that already hold the search response and do not want a
    storage round-trip (tests, ad-hoc inspection).
    """
    out: list[dict] = []
    failed = 0
    for post in posts:
        try:
            out.append(parse_post(post).to_dict())
        except ThreadsParseError as exc:
            failed += 1
            logger.error(f"[threads] unparsable post: {exc}")
    return out, failed
