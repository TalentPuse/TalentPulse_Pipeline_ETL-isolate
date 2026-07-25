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


def _parse_proxy(url: str | None) -> dict | None:
    """Turn a proxy URL into Playwright's proxy dict, splitting embedded auth.

    'http://user:pass@host:3128' -> {'server': 'http://host:3128',
    'username': 'user', 'password': 'pass'}. Returns None for falsy input.
    """
    if not url:
        return None
    from urllib.parse import urlparse

    p = urlparse(url)
    host = p.hostname or url
    server = f"{p.scheme or 'http'}://{host}"
    if p.port:
        server += f":{p.port}"
    cfg: dict = {"server": server}
    if p.username:
        cfg["username"] = p.username
    if p.password:
        cfg["password"] = p.password
    return cfg


class StealthBrowser:
    """Playwright Chromium with anti-detection patches.

    Clears cookies before each navigation to bypass Cloudflare session tracking.
    """

    def __init__(
        self,
        headless: bool = True,
        default_wait_ms: int = 8000,
        proxy: str | None = None,
    ):
        self._headless = headless
        self._default_wait_ms = default_wait_ms
        self._proxy = proxy or None
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None

    def start(self) -> "StealthBrowser":
        self._pw = sync_playwright().start()
        launch_kwargs = dict(
            headless=self._headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        proxy_cfg = _parse_proxy(self._proxy)
        if proxy_cfg:
            launch_kwargs["proxy"] = proxy_cfg
            logger.info(f"StealthBrowser routing via proxy {proxy_cfg['server']}")
        self._browser = self._pw.chromium.launch(**launch_kwargs)
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

    def _fetch_once(
        self, url: str, wait: int, ready_min_len: int = 0, persist_cookies: bool = False
    ) -> str:
        # Persisting cookies keeps Cloudflare's cf_clearance token across requests
        # so a challenge only has to be solved once (needed for topcv, whose CF is
        # stricter). Other sources clear cookies each time to reset CF sessions.
        if not persist_cookies:
            self._ctx.clear_cookies()
        self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        if ready_min_len <= 0:
            # No readiness hint: keep the original fixed dwell (other sources).
            self._page.wait_for_timeout(wait)
            return self._page.content()
        # Readiness polling: server-rendered pages (topcv JSON-LD) are already
        # complete at domcontentloaded, so return as soon as the content passes
        # ready_min_len instead of always dwelling the full `wait`. Only genuinely
        # slow / challenged pages consume the whole budget. Cuts crawl time ~3-5x.
        step = 400
        elapsed = 0
        html = self._page.content()
        while len(html) < ready_min_len and elapsed < wait:
            self._page.wait_for_timeout(step)
            elapsed += step
            html = self._page.content()
        return html

    def fetch_page(
        self,
        url: str,
        wait_ms: int | None = None,
        retries: int = 0,
        min_len: int = 0,
        persist_cookies: bool = False,
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
            html = self._fetch_once(
                url, wait, ready_min_len=min_len, persist_cookies=persist_cookies
            )
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
