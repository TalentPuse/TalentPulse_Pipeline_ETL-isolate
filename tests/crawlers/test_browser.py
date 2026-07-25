"""Tests for the shared StealthBrowser module.

These tests mock Playwright entirely — no real browser is launched.
"""
from unittest.mock import MagicMock, patch, call

import pytest

from src.crawlers.browser import (
    StealthBrowser,
    UA_POOL,
    VIEWPORT_POOL,
    STEALTH_JS,
    _parse_proxy,
)


class TestParseProxy:
    def test_none_and_empty(self):
        assert _parse_proxy(None) is None
        assert _parse_proxy("") is None

    def test_plain_server(self):
        assert _parse_proxy("http://proxy.example:3128") == {
            "server": "http://proxy.example:3128"
        }

    def test_with_credentials(self):
        assert _parse_proxy("http://user:pass@proxy.example:8080") == {
            "server": "http://proxy.example:8080",
            "username": "user",
            "password": "pass",
        }


class TestProxyWiring:
    @patch("src.crawlers.browser.sync_playwright")
    def test_start_passes_proxy_to_launch(self, mock_sp):
        pw = MagicMock()
        mock_sp.return_value.start.return_value = pw
        pw.chromium.launch.return_value = MagicMock()
        pw.chromium.launch.return_value.new_context.return_value.new_page.return_value = MagicMock()

        StealthBrowser(proxy="http://u:p@host:3128").start()

        assert pw.chromium.launch.call_args[1]["proxy"] == {
            "server": "http://host:3128", "username": "u", "password": "p",
        }

    @patch("src.crawlers.browser.sync_playwright")
    def test_start_no_proxy_key_when_unset(self, mock_sp):
        pw = MagicMock()
        mock_sp.return_value.start.return_value = pw
        pw.chromium.launch.return_value = MagicMock()
        pw.chromium.launch.return_value.new_context.return_value.new_page.return_value = MagicMock()

        StealthBrowser().start()

        assert "proxy" not in pw.chromium.launch.call_args[1]


class TestStealthBrowserInit:
    def test_default_params(self):
        sb = StealthBrowser()
        assert sb._headless is True
        assert sb._default_wait_ms == 8000

    def test_custom_params(self):
        sb = StealthBrowser(headless=False, default_wait_ms=5000)
        assert sb._headless is False
        assert sb._default_wait_ms == 5000


class TestStealthBrowserStart:
    @patch("src.crawlers.browser.sync_playwright")
    def test_start_launches_chromium(self, mock_sp):
        pw = MagicMock()
        mock_sp.return_value.start.return_value = pw
        browser = MagicMock()
        pw.chromium.launch.return_value = browser
        ctx = MagicMock()
        browser.new_context.return_value = ctx
        page = MagicMock()
        ctx.new_page.return_value = page

        sb = StealthBrowser(headless=True)
        result = sb.start()

        assert result is sb
        pw.chromium.launch.assert_called_once()
        launch_kwargs = pw.chromium.launch.call_args
        assert launch_kwargs[1]["headless"] is True
        browser.new_context.assert_called_once()
        ctx.new_page.assert_called_once()
        page.add_init_script.assert_called_once_with(STEALTH_JS)

    @patch("src.crawlers.browser.sync_playwright")
    def test_start_uses_ua_and_viewport(self, mock_sp):
        pw = MagicMock()
        mock_sp.return_value.start.return_value = pw
        browser = MagicMock()
        pw.chromium.launch.return_value = browser
        ctx = MagicMock()
        browser.new_context.return_value = ctx
        ctx.new_page.return_value = MagicMock()

        sb = StealthBrowser()
        sb.start()

        ctx_call = browser.new_context.call_args
        assert ctx_call[1]["user_agent"] in UA_POOL
        assert ctx_call[1]["viewport"] in VIEWPORT_POOL
        assert ctx_call[1]["locale"] == "vi-VN"
        assert ctx_call[1]["timezone_id"] == "Asia/Ho_Chi_Minh"


class TestStealthBrowserFetchPage:
    @patch("src.crawlers.browser.sync_playwright")
    def test_fetch_page_clears_cookies_first(self, mock_sp):
        pw = MagicMock()
        mock_sp.return_value.start.return_value = pw
        browser_mock = MagicMock()
        pw.chromium.launch.return_value = browser_mock
        ctx = MagicMock()
        browser_mock.new_context.return_value = ctx
        page = MagicMock()
        ctx.new_page.return_value = page
        page.content.return_value = "<html>test</html>"

        sb = StealthBrowser()
        sb.start()
        html = sb.fetch_page("https://example.com")

        ctx.clear_cookies.assert_called_once()
        page.goto.assert_called_once_with(
            "https://example.com",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        page.wait_for_timeout.assert_called_once_with(8000)
        assert html == "<html>test</html>"

    @patch("src.crawlers.browser.sync_playwright")
    def test_fetch_page_custom_wait(self, mock_sp):
        pw = MagicMock()
        mock_sp.return_value.start.return_value = pw
        browser_mock = MagicMock()
        pw.chromium.launch.return_value = browser_mock
        ctx = MagicMock()
        browser_mock.new_context.return_value = ctx
        page = MagicMock()
        ctx.new_page.return_value = page
        page.content.return_value = "<html></html>"

        sb = StealthBrowser(default_wait_ms=5000)
        sb.start()
        sb.fetch_page("https://example.com", wait_ms=3000)

        page.wait_for_timeout.assert_called_once_with(3000)

    @patch("src.crawlers.browser.sync_playwright")
    def test_fetch_page_explicit_zero_wait(self, mock_sp):
        pw = MagicMock()
        mock_sp.return_value.start.return_value = pw
        browser_mock = MagicMock()
        pw.chromium.launch.return_value = browser_mock
        ctx = MagicMock()
        browser_mock.new_context.return_value = ctx
        page = MagicMock()
        ctx.new_page.return_value = page
        page.content.return_value = "<html></html>"

        sb = StealthBrowser()
        sb.start()
        sb.fetch_page("https://example.com", wait_ms=0)

        page.wait_for_timeout.assert_called_once_with(0)


class TestStealthBrowserClose:
    @patch("src.crawlers.browser.sync_playwright")
    def test_close_releases_resources(self, mock_sp):
        pw = MagicMock()
        mock_sp.return_value.start.return_value = pw
        browser_mock = MagicMock()
        pw.chromium.launch.return_value = browser_mock
        ctx = MagicMock()
        browser_mock.new_context.return_value = ctx
        ctx.new_page.return_value = MagicMock()

        sb = StealthBrowser()
        sb.start()
        sb.close()

        ctx.close.assert_called_once()
        browser_mock.close.assert_called_once()
        pw.stop.assert_called_once()
        assert sb._ctx is None
        assert sb._browser is None
        assert sb._pw is None

    @patch("src.crawlers.browser.sync_playwright")
    def test_close_idempotent(self, mock_sp):
        pw = MagicMock()
        mock_sp.return_value.start.return_value = pw
        browser_mock = MagicMock()
        pw.chromium.launch.return_value = browser_mock
        ctx = MagicMock()
        browser_mock.new_context.return_value = ctx
        ctx.new_page.return_value = MagicMock()

        sb = StealthBrowser()
        sb.start()
        sb.close()
        sb.close()  # should not raise

        ctx.close.assert_called_once()


class TestStealthBrowserContextManager:
    @patch("src.crawlers.browser.sync_playwright")
    def test_context_manager_starts_and_closes(self, mock_sp):
        pw = MagicMock()
        mock_sp.return_value.start.return_value = pw
        browser_mock = MagicMock()
        pw.chromium.launch.return_value = browser_mock
        ctx = MagicMock()
        browser_mock.new_context.return_value = ctx
        page = MagicMock()
        ctx.new_page.return_value = page
        page.content.return_value = "<html>ctx test</html>"

        with StealthBrowser() as sb:
            html = sb.fetch_page("https://example.com")

        assert html == "<html>ctx test</html>"
        ctx.close.assert_called_once()
        browser_mock.close.assert_called_once()
        pw.stop.assert_called_once()


class TestFetchPageChallengeRetry:
    """min_len/retries: retry short Cloudflare-challenge responses (topcv)."""

    def _browser_with_sequence(self, monkeypatch, seq):
        monkeypatch.setattr("src.crawlers.browser.time.sleep", lambda *a, **k: None)
        sb = StealthBrowser()
        calls = []

        def fake_once(url, wait, ready_min_len=0, persist_cookies=False):
            calls.append(url)
            return seq[len(calls) - 1]

        sb._fetch_once = fake_once
        return sb, calls

    def test_retries_until_page_exceeds_min_len(self, monkeypatch):
        sb, calls = self._browser_with_sequence(
            monkeypatch, ["x" * 100, "x" * 100, "y" * 70000]
        )
        html = sb.fetch_page("http://t", retries=4, min_len=60000)
        assert len(html) == 70000
        assert len(calls) == 3  # stopped as soon as a real page arrived

    def test_returns_last_short_response_when_retries_exhausted(self, monkeypatch):
        sb, calls = self._browser_with_sequence(monkeypatch, ["s" * 100] * 5)
        html = sb.fetch_page("http://t", retries=3, min_len=60000)
        assert len(html) == 100
        assert len(calls) == 4  # initial try + 3 retries

    def test_default_is_single_shot_no_retry(self, monkeypatch):
        sb, calls = self._browser_with_sequence(monkeypatch, ["small"])
        html = sb.fetch_page("http://t")
        assert html == "small"
        assert len(calls) == 1


class TestConstants:
    def test_ua_pool_not_empty(self):
        assert len(UA_POOL) >= 2

    def test_viewport_pool_not_empty(self):
        assert len(VIEWPORT_POOL) >= 2

    def test_all_viewports_have_dimensions(self):
        for vp in VIEWPORT_POOL:
            assert "width" in vp
            assert "height" in vp
            assert isinstance(vp["width"], int)
            assert isinstance(vp["height"], int)

    def test_stealth_js_patches_webdriver(self):
        assert "webdriver" in STEALTH_JS

    def test_stealth_js_patches_chrome(self):
        assert "window.chrome" in STEALTH_JS

    def test_stealth_js_patches_plugins(self):
        assert "plugins" in STEALTH_JS

    def test_stealth_js_patches_languages(self):
        assert "languages" in STEALTH_JS
