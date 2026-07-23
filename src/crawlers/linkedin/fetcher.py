"""HTTP fetcher with LinkedIn-specific anti-block tuning."""
import logging
import time

import requests

from src.crawlers.linkedin.user_agents import random_headers
from src.utils.config import config

logger = logging.getLogger(__name__)


class BlockedError(Exception):
    pass


class TransientError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


# Backoff budget per job, NOT per crawl session. The old 60/120/300 schedule
# let a single rate-limited job burn 8 minutes of sleep; a handful of them ate
# the whole CI job window and the run was killed before parse/load ever ran.
# LinkedIn's guest API rate-limit does not clear in seconds anyway — better to
# give up quickly and leave the job pending for the next scheduled run.
_BACKOFF = {
    429: [20, 60],
    999: [20, 60],
    503: [10, 30],
    "5xx": [5, 15],
    "timeout": [5, 15],
}

# Upper bound on any single sleep, including a server-sent Retry-After.
MAX_BACKOFF_SECONDS = 60

_BLOCK_KEYWORDS = (
    "authwall", "sign in", "join now", "captcha",
    "access denied", "attention required",
)

DETAIL_API = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"


class Fetcher:
    def __init__(self, session: requests.Session | None = None, sleeper=time.sleep):
        self.session = session or requests.Session()
        self._sleep = sleeper
        self._timeout = config.CRAWLER_REQUEST_TIMEOUT
        proxy = getattr(config, "LINKEDIN_PROXY_URL", None)
        if proxy:
            self.session.proxies = {"https": proxy, "http": proxy}

    def fetch(self, url: str) -> tuple[int, str, int]:
        """GET url with retry. Returns (status, html, latency_ms)."""
        attempt = 0
        while True:
            self.session.headers.update(random_headers())
            started = time.monotonic()
            try:
                resp = self.session.get(url, timeout=self._timeout, allow_redirects=True)
            except (requests.Timeout, requests.ConnectionError) as e:
                logger.warning(f"network error on {url} (attempt {attempt}): {e}")
                if attempt >= len(_BACKOFF["timeout"]):
                    raise TransientError(f"network error: {e}", None) from e
                self._sleep(_BACKOFF["timeout"][attempt])
                attempt += 1
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            status = resp.status_code

            if status == 200:
                body = resp.text
                lowered = body[:5000].lower()
                if any(k in lowered for k in _BLOCK_KEYWORDS):
                    raise BlockedError(f"block keyword detected in body for {url}")
                return status, body, latency_ms

            if status == 403:
                raise BlockedError(f"403 Forbidden on {url}")

            if status == 404:
                raise TransientError(f"404 Not Found on {url}", 404)

            schedule_key = status if status in _BACKOFF else ("5xx" if 500 <= status < 600 else None)
            if schedule_key is None:
                raise TransientError(f"unexpected status {status} on {url}", status)

            schedule = _BACKOFF[schedule_key]
            if attempt >= len(schedule):
                raise TransientError(f"retries exhausted for status {status} on {url}", status)

            wait = schedule[attempt]
            if status == 429:
                ra = resp.headers.get("Retry-After")
                if ra and ra.isdigit():
                    wait = max(wait, int(ra))
            wait = min(wait, MAX_BACKOFF_SECONDS)
            logger.warning(f"status {status} on {url}, sleeping {wait}s (attempt {attempt})")
            self._sleep(wait)
            attempt += 1
