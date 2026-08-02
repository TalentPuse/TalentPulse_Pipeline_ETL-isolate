"""Threads (Meta) keyword-search crawler.

Source of truth: https://developers.facebook.com/docs/threads/keyword-search/

Why this source exists at all: recruiters post openings in their own feed
("team minh dang tuyen 2 ban Data Engineer, inbox nhe") and those never reach a
job board. Threads is the only one of the three social networks we looked at
with a *legitimate* keyword-search path — LinkedIn's content search is behind
login and trips a security checkpoint after a couple of logged-out page views,
and Meta's Content Library (Facebook) is research-only and cleanroom-bound.

Four facts about the API that shape this module:

1. **Permissions.** `threads_basic` on every call plus `threads_keyword_search`
   on this endpoint. **Until the app is approved, the endpoint returns only
   posts owned by the authenticated user** — so a run that yields nothing today
   is expected, not a bug. Public posts become searchable after review.

2. **Rate limit: 2,200 queries per rolling 24 h per user.** Queries that return
   no results do NOT count against it. `QueryBudget` below mirrors that rule
   (charge, then refund on an empty page) and refuses to keep going once the
   budget is gone, rather than burning through it and getting the token
   throttled.

3. **The empty-array trap.** For keywords Meta deems sensitive or offensive the
   API returns `{"data": []}` — HTTP 200, no error, no warning, no reason code.
   That is indistinguishable from "nobody posted a job today" unless we treat it
   as its own signal. This repo has already lost 7 days of a source to a silent
   failure, so an empty keyword is logged at ERROR and counted separately in
   `empty_keywords`, which the flow surfaces in its artifact.

4. **No `owner` field.** Meta explicitly excludes it. `username` is the only
   author identity we get, and it is an account handle, not a company.

Transport is `requests` (already in requirements.txt); httpx is deliberately not
used because it is not installed in the worker image.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import time
from datetime import datetime, timezone

import requests

from src.storage.minio_client import MinioClient
from src.utils import safety
from src.utils.config import config

logger = logging.getLogger(__name__)

THREADS_API_BASE = "https://graph.threads.net/v1.0"
KEYWORD_SEARCH_PATH = "/keyword_search"

# The full documented field list. `owner` is excluded by Meta and is not
# requestable — asking for it makes the whole call fail.
POST_FIELDS = (
    "id,text,media_type,permalink,timestamp,username,"
    "has_replies,is_quote_post,is_reply"
)

# Per Meta: 2,200 queries per rolling 24 h per user.
DAILY_QUERY_BUDGET = 2200

# Threads pages at 25 by default; ask explicitly so the page count is predictable.
PAGE_SIZE = 25

# Safety net on pagination. A cursor that keeps handing back a non-empty page
# forever would otherwise spend the entire daily budget on one keyword.
MAX_PAGES_PER_KEYWORD = 20

# Object-storage layout, mirroring the other sources
# (`details/<source>/html/{run_id}/{job_id}.html.gz`). These are JSON, not HTML,
# so the suffix differs — see src/parsers/threads/post_parser.py.
RAW_PREFIX = "details/threads/json/"
SEARCH_ARCHIVE_PREFIX = "listings/threads/"


class ThreadsConfigError(RuntimeError):
    """Missing/blank credentials. Raised at construction, never mid-run."""


class ThreadsAuthError(RuntimeError):
    """The token was rejected (401/403). Every later call would fail too."""


class ThreadsQuotaExceeded(RuntimeError):
    """The 2,200/day query budget is spent."""


def get_access_token() -> str:
    """Read `THREADS_ACCESS_TOKEN` or refuse to run.

    Same stance as `orchestration/flows/sync_to_web.py::_web_dsn`: there is no
    sane default for a credential, and a connector that starts up without one
    only fails later, further from the cause.
    """
    token = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
    if not token:
        raise ThreadsConfigError(
            "THREADS_ACCESS_TOKEN chua duoc dat. Threads keyword search doi mot "
            "user access token co scope `threads_basic` + `threads_keyword_search`. "
            "Dat no trong GitHub Secrets va truyen qua env cua workflow; khong co "
            "gia tri mac dinh nao dung ca."
        )
    return token


class QueryBudget:
    """Tracks the rolling-24h query allowance for one token.

    In-process only: it protects a single run from spending the whole day's
    allowance, it does NOT know what earlier runs spent. With one scheduled run
    per day over a handful of keywords that is plenty; if Threads ever gets more
    than one run a day, this needs to move to the warehouse.
    """

    def __init__(self, limit: int = DAILY_QUERY_BUDGET):
        self.limit = limit
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def spend(self) -> None:
        if self.remaining <= 0:
            raise ThreadsQuotaExceeded(
                f"Threads query budget exhausted ({self.used}/{self.limit} in this run)"
            )
        self.used += 1

    def refund(self) -> None:
        """Give back a query that returned no results — Meta does not count those."""
        self.used = max(0, self.used - 1)


class ThreadsSearchCrawler:
    """Walk the Threads keyword-search endpoint and archive the raw posts.

    Unlike every other source in this repo there is no listing -> queue ->
    fetch-detail split: the search response already carries the post text, so
    one call produces finished content. That is why there is no
    `src/queue/threads_seeder.py` — a queue here would enqueue ids we already
    hold the bodies for.
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        minio: MinioClient | None = None,
        *,
        token: str | None = None,
        budget: QueryBudget | None = None,
        archive: bool = True,
    ):
        # Resolved eagerly: fail at construction, before any keyword is touched.
        self.token = token or get_access_token()
        self.session = session or requests.Session()
        self.budget = budget or QueryBudget()
        self.archive = archive
        self.minio = minio if minio is not None else (MinioClient() if archive else None)
        self.bucket = config.S3_BUCKET_NAME
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # ── HTTP ──────────────────────────────────────────────────────

    def _get_page(self, keyword: str, after: str | None) -> dict:
        """One search request. Charges the budget; refunds it if empty."""
        params = {
            "q": keyword,
            "search_type": "TOP",
            "fields": POST_FIELDS,
            "limit": PAGE_SIZE,
            "access_token": self.token,
        }
        if after:
            params["after"] = after

        self.budget.spend()
        resp = self.session.get(
            f"{THREADS_API_BASE}{KEYWORD_SEARCH_PATH}",
            params=params,
            timeout=config.CRAWLER_REQUEST_TIMEOUT,
        )
        if resp.status_code in (401, 403):
            # Do not keep hammering with a token the API already refused: every
            # later keyword would fail the same way and burn the budget doing it.
            raise ThreadsAuthError(
                f"Threads API rejected the token (HTTP {resp.status_code}): "
                f"{resp.text[:300]}"
            )
        resp.raise_for_status()
        payload = resp.json()

        if not payload.get("data"):
            self.budget.refund()
        return payload

    # ── archiving ─────────────────────────────────────────────────

    def _archive_post(self, post: dict) -> None:
        if not self.archive or self.minio is None:
            return
        post_id = post.get("id")
        if not post_id:
            return
        key = f"{RAW_PREFIX}{self.run_id}/{post_id}.json.gz"
        body = gzip.compress(json.dumps(post, ensure_ascii=False).encode("utf-8"))
        self.minio.s3_client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ContentEncoding="gzip",
        )

    def _archive_response(self, keyword: str, page: int, payload: dict) -> None:
        if not self.archive or self.minio is None:
            return
        key = f"{SEARCH_ARCHIVE_PREFIX}{self.run_id}/{_slug(keyword)}_p{page}.json"
        self.minio.upload_string(
            bucket_name=self.bucket,
            object_name=key,
            content=json.dumps(payload, ensure_ascii=False),
            content_type="application/json",
        )

    # ── crawling ──────────────────────────────────────────────────

    def search_keyword(self, keyword: str, max_pages: int | None = None) -> dict:
        """Search one keyword, following cursors.

        Returns ``{"posts": [...], "pages": n, "empty": bool, "error": str|None}``.
        ``empty`` means the FIRST page came back as an empty array — the trap
        described in the module docstring.
        """
        cap = min(max_pages or MAX_PAGES_PER_KEYWORD, MAX_PAGES_PER_KEYWORD)
        posts: list[dict] = []
        after: str | None = None
        pages = 0
        empty_first_page = False

        for page in range(1, cap + 1):
            try:
                payload = self._get_page(keyword, after)
            except (ThreadsAuthError, ThreadsQuotaExceeded):
                raise  # fatal for the whole run — let it out
            except requests.RequestException as exc:
                logger.error(f"[threads] '{keyword}' p{page} request failed: {exc}")
                return {
                    "posts": posts,
                    "pages": pages,
                    "empty": False,
                    "error": str(exc),
                }
            except ValueError as exc:  # json decode
                logger.error(f"[threads] '{keyword}' p{page} returned non-JSON: {exc}")
                return {"posts": posts, "pages": pages, "empty": False, "error": str(exc)}

            pages += 1
            batch = payload.get("data") or []

            try:
                self._archive_response(keyword, page, payload)
            except Exception as exc:
                # A failed archive upload must not lose posts we already hold.
                logger.error(f"[threads] archive of '{keyword}' p{page} failed: {exc}")

            if not batch:
                if page == 1:
                    empty_first_page = True
                    logger.error(
                        "[threads] EMPTY RESULT for keyword %r. The API returns an "
                        "empty array with HTTP 200 both for a genuinely quiet day AND "
                        "for keywords Meta classifies as sensitive/offensive, and it "
                        "never says which. Do NOT read this as 'no jobs today'. "
                        "Counted as empty_keywords; check the keyword by hand.",
                        keyword,
                    )
                break

            for post in batch:
                posts.append(post)
                try:
                    self._archive_post(post)
                except Exception as exc:
                    logger.error(f"[threads] archive of post {post.get('id')} failed: {exc}")

            after = (payload.get("paging") or {}).get("cursors", {}).get("after")
            if not after:
                break
            time.sleep(config.CRAWLER_RATE_SECONDS)

        logger.info(
            f"[threads] '{keyword}': {len(posts)} posts over {pages} page(s), "
            f"budget {self.budget.used}/{self.budget.limit}"
        )
        return {"posts": posts, "pages": pages, "empty": empty_first_page, "error": None}

    def search_all(
        self,
        keywords: list[str] | None = None,
        max_pages: int | None = None,
    ) -> dict:
        """Search every keyword. Returns counters + the deduped post list.

        Counters:
            keywords          keywords attempted
            posts_raw         posts returned, before dedup
            posts_unique      distinct post ids
            empty_keywords    keywords whose FIRST page was an empty array
            failed_keywords   keywords that hit an HTTP/JSON error
            queries_used      search requests charged against the daily budget
            budget_exhausted  1 if the run stopped early on the 2,200/day cap
        """
        keywords = keywords if keywords is not None else config.THREADS_KEYWORDS
        counters = {
            "keywords": 0,
            "posts_raw": 0,
            "posts_unique": 0,
            "empty_keywords": 0,
            "failed_keywords": 0,
            "queries_used": 0,
            "budget_exhausted": 0,
        }
        empty_keyword_list: list[str] = []
        by_id: dict[str, dict] = {}

        for keyword in keywords:
            if safety.is_killed():
                logger.warning("[threads] kill switch set — stopping keyword loop")
                break
            counters["keywords"] += 1
            try:
                result = self.search_keyword(keyword, max_pages=max_pages)
            except ThreadsQuotaExceeded as exc:
                counters["budget_exhausted"] = 1
                logger.error(f"[threads] {exc} — stopping with keywords left unsearched")
                break

            if result["error"]:
                counters["failed_keywords"] += 1
            if result["empty"]:
                counters["empty_keywords"] += 1
                empty_keyword_list.append(keyword)

            counters["posts_raw"] += len(result["posts"])
            for post in result["posts"]:
                post_id = post.get("id")
                if post_id:
                    by_id.setdefault(str(post_id), post)

        counters["posts_unique"] = len(by_id)
        counters["queries_used"] = self.budget.used

        if counters["empty_keywords"]:
            logger.error(
                "[threads] %d/%d keywords returned an empty array: %s. Treat this as "
                "a possible sensitive-keyword block, not as an absence of jobs.",
                counters["empty_keywords"],
                counters["keywords"],
                ", ".join(empty_keyword_list),
            )

        logger.info(f"[threads] search done: {counters}")
        return {
            "counters": counters,
            "posts": list(by_id.values()),
            "empty_keyword_list": empty_keyword_list,
            "run_id": self.run_id,
        }


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-") or "kw"
