"""Shared stealth Playwright browser for crawling Cloudflare-protected sites.

Usage:
    with StealthBrowser() as browser:
        html = browser.fetch_page("https://itviec.com/it-jobs/python")
        html2 = browser.fetch_page("https://itviec.com/it-jobs/python?page=2")
"""
from __future__ import annotations

import logging
import random
import time

from playwright.sync_api import sync_playwright, Playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

VIEWPORT_POOL = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
]

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
const origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (params) =>
    params.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : origQuery(params);
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [
            {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
            {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
            {name: 'Native Client', filename: 'internal-nacl-plugin'},
        ];
        arr.__proto__ = PluginArray.prototype;
        return arr;
    }
});
Object.defineProperty(navigator, 'languages', {
    get: () => ['vi-VN', 'vi', 'en-US', 'en'],
});
"""


class StealthBrowser:
    """Playwright Chromium with anti-detection patches.

    Clears cookies before each navigation to bypass Cloudflare session tracking.
    """

    def __init__(self, headless: bool = True, default_wait_ms: int = 8000):
        self._headless = headless
        self._default_wait_ms = default_wait_ms
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None

    def start(self) -> "StealthBrowser":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self._headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._create_context()
        return self

    def _create_context(self) -> None:
        self._ctx = self._browser.new_context(
            user_agent=random.choice(UA_POOL),
            viewport=random.choice(VIEWPORT_POOL),
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
        )
        self._page = self._ctx.new_page()
        self._page.add_init_script(STEALTH_JS)

    def _fetch_once(self, url: str, wait: int) -> str:
        self._ctx.clear_cookies()
        self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        self._page.wait_for_timeout(wait)
        return self._page.content()

    def fetch_page(
        self,
        url: str,
        wait_ms: int | None = None,
        retries: int = 0,
        min_len: int = 0,
    ) -> str:
        """Navigate to URL with stealth, return full HTML.

        Clears cookies before each request to reset Cloudflare session.

        When ``min_len`` > 0, a response shorter than it is treated as a
        transient Cloudflare interstitial/challenge (topcv.vn serves a ~27KB
        challenge vs ~1.6MB real pages) and re-fetched up to ``retries`` times
        before giving up. Defaults (retries=0, min_len=0) preserve the original
        single-shot behaviour for callers that don't opt in.
        """
        wait = wait_ms if wait_ms is not None else self._default_wait_ms
        html = ""
        for attempt in range(retries + 1):
            html = self._fetch_once(url, wait)
            if len(html) >= min_len:
                return html
            if attempt < retries:
                logger.warning(
                    f"Short response ({len(html)}B < {min_len}) for {url}; "
                    f"likely a challenge, retry {attempt + 1}/{retries}"
                )
                time.sleep(1.5)
        return html

    def close(self) -> None:
        if self._ctx:
            self._ctx.close()
            self._ctx = None
            self._page = None
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._pw:
            self._pw.stop()
            self._pw = None

    def __enter__(self) -> "StealthBrowser":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()
