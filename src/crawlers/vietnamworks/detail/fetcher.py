"""HTTP fetcher with status-aware retry/backoff for VietnamWorks detail pages."""
import logging
import time

import requests

from src.crawlers.vietnamworks.detail.user_agents import default_headers
from src.utils.config import config

logger = logging.getLogger(__name__)


class BlockedError(Exception):
    """Raised on 403 or other clear block signals — caller should stop."""


class ExpiredError(Exception):
    """Raised on 404 — job no longer exists."""


class TransientError(Exception):
    """5xx / network error after retries exhausted."""

    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


# Backoff schedules (seconds) — picked off the plan
_BACKOFF = {
    429: [30, 60, 120],
    503: [10, 30, 90],
    "5xx": [5, 15, 45],
    "timeout": [5, 15, 45],
}

_BLOCK_KEYWORDS = ("captcha", "cloudflare", "attention required", "access denied")


class Fetcher:
    def __init__(self, session: requests.Session | None = None, sleeper=time.sleep):
        self.session = session or requests.Session()
        self.session.headers.update(default_headers())
        self._sleep = sleeper
        self._timeout = config.CRAWLER_REQUEST_TIMEOUT

    def fetch(self, url: str) -> tuple[int, str, int]:
        """
        GET url with retry. Returns (status, html, latency_ms).
        Raises BlockedError, ExpiredError, or TransientError.
        """
        last_exc: Exception | None = None
        attempt = 0
        while True:
            started = time.monotonic()
            try:
                resp = self.session.get(url, timeout=self._timeout, allow_redirects=True)
            except (requests.Timeout, requests.ConnectionError) as e:
                latency_ms = int((time.monotonic() - started) * 1000)
                logger.warning(f"network error on {url} (attempt {attempt}): {e}")
                if attempt >= len(_BACKOFF["timeout"]):
                    raise TransientError(f"network error: {e}", None) from e
                self._sleep(_BACKOFF["timeout"][attempt])
                attempt += 1
                last_exc = e
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            status = resp.status_code

            if status == 200:
                body = resp.text
                lowered = body[:4000].lower()
                if any(k in lowered for k in _BLOCK_KEYWORDS):
                    raise BlockedError(f"block keyword detected in body for {url}")
                return status, body, latency_ms

            if status == 403:
                raise BlockedError(f"403 Forbidden on {url}")
            if status == 404:
                raise ExpiredError(f"404 Not Found on {url}")

            schedule_key = status if status in _BACKOFF else ("5xx" if 500 <= status < 600 else None)
            if schedule_key is None:
                # 4xx other than 403/404/429 — non-retryable
                raise TransientError(f"unexpected status {status} on {url}", status)

            schedule = _BACKOFF[schedule_key]
            if attempt >= len(schedule):
                raise TransientError(f"retries exhausted for status {status} on {url}", status)

            wait = schedule[attempt]
            if status == 429:
                ra = resp.headers.get("Retry-After")
                if ra and ra.isdigit():
                    wait = max(wait, int(ra))
            logger.warning(f"status {status} on {url}, sleeping {wait}s (attempt {attempt})")
            self._sleep(wait)
            attempt += 1
            last_exc = TransientError(f"status {status}", status)
