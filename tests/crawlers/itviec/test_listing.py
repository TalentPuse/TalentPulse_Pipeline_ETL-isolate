"""Tests for ITviec listing crawler."""
import json
from unittest.mock import MagicMock, patch

import pytest

from src.crawlers.itviec.listing import (
    ITviecListingCrawler,
    extract_json_ld_urls,
    detect_max_page,
    MAX_CONSECUTIVE_EMPTY,
)
from tests.parsers.itviec._fixtures import (
    SAMPLE_LISTING_URLS,
    make_listing_html,
)


# ── extract_json_ld_urls ────────────────────────────────────────────


class TestExtractJsonLdUrls:
    def test_extracts_urls_from_item_list(self):
        html = make_listing_html(SAMPLE_LISTING_URLS)
        urls = extract_json_ld_urls(html)
        assert urls == SAMPLE_LISTING_URLS

    def test_returns_empty_on_no_json_ld(self):
        html = "<html><body>No json-ld here</body></html>"
        assert extract_json_ld_urls(html) == []

    def test_ignores_non_itemlist_json_ld(self):
        data = {"@type": "BreadcrumbList", "itemListElement": []}
        html = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        assert extract_json_ld_urls(html) == []

    def test_ignores_malformed_json(self):
        html = '<script type="application/ld+json">{bad json</script>'
        assert extract_json_ld_urls(html) == []

    def test_filters_non_it_jobs_urls(self):
        item_list = {
            "@type": "ItemList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "url": "https://itviec.com/it-jobs/real-job-123"},
                {"@type": "ListItem", "position": 2, "url": "https://itviec.com/companies/acme"},
            ],
        }
        html = f'<script type="application/ld+json">{json.dumps(item_list)}</script>'
        urls = extract_json_ld_urls(html)
        assert urls == ["https://itviec.com/it-jobs/real-job-123"]

    def test_handles_empty_itemlistelement(self):
        item_list = {"@type": "ItemList", "itemListElement": []}
        html = f'<script type="application/ld+json">{json.dumps(item_list)}</script>'
        assert extract_json_ld_urls(html) == []

    def test_handles_items_without_url(self):
        item_list = {
            "@type": "ItemList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1},
            ],
        }
        html = f'<script type="application/ld+json">{json.dumps(item_list)}</script>'
        assert extract_json_ld_urls(html) == []

    def test_picks_correct_block_among_multiple(self):
        """Multiple JSON-LD blocks; only ItemList should be parsed."""
        breadcrumb = json.dumps({"@type": "BreadcrumbList"})
        item_list = json.dumps({
            "@type": "ItemList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "url": "https://itviec.com/it-jobs/job-111"},
            ],
        })
        website = json.dumps({"@type": "WebSite"})
        html = f"""
        <script type="application/ld+json">{breadcrumb}</script>
        <script type="application/ld+json">{item_list}</script>
        <script type="application/ld+json">{website}</script>
        """
        assert extract_json_ld_urls(html) == ["https://itviec.com/it-jobs/job-111"]


# ── detect_max_page ──────────────────────────────────────────────────


class TestDetectMaxPage:
    def test_single_page_no_pagination(self):
        html = make_listing_html(max_page=1)
        assert detect_max_page(html) == 1

    def test_multi_page_pagination(self):
        html = make_listing_html(page=1, max_page=5)
        assert detect_max_page(html) == 5

    def test_no_pagination_links(self):
        html = "<html><body>No pages</body></html>"
        assert detect_max_page(html) == 1

    def test_ignores_non_numeric_page_params(self):
        html = '<a href="?page=abc">bad</a>'
        assert detect_max_page(html) == 1


# ── ITviecListingCrawler ─────────────────────────────────────────────


class TestITviecListingCrawler:
    def _make_crawler(self, fetch_pages: dict[str, str] | None = None):
        """Return (crawler, browser_mock, minio_mock)."""
        browser = MagicMock()
        minio = MagicMock()

        if fetch_pages:
            def side_effect(url, **kw):
                for key, html in fetch_pages.items():
                    if key in url:
                        return html
                return ""
            browser.fetch_page.side_effect = side_effect
        else:
            browser.fetch_page.return_value = ""

        crawler = ITviecListingCrawler(browser=browser, minio=minio)
        return crawler, browser, minio

    @patch("src.crawlers.itviec.listing.time.sleep", lambda *a: None)
    def test_crawl_keyword_single_page(self):
        html = make_listing_html(SAMPLE_LISTING_URLS, page=1, max_page=1)
        crawler, browser, minio = self._make_crawler()
        browser.fetch_page.return_value = html

        urls = crawler.crawl_keyword("data-engineer", max_pages=1)

        assert urls == SAMPLE_LISTING_URLS
        browser.fetch_page.assert_called_once()
        minio.upload_string.assert_called_once()

    @patch("src.crawlers.itviec.listing.time.sleep", lambda *a: None)
    def test_crawl_keyword_multi_page(self):
        page1_urls = ["https://itviec.com/it-jobs/job-a-111"]
        page2_urls = ["https://itviec.com/it-jobs/job-b-222"]
        html_p1 = make_listing_html(page1_urls, page=1, max_page=2)
        html_p2 = make_listing_html(page2_urls, page=2, max_page=2)

        browser = MagicMock()
        browser.fetch_page.side_effect = [html_p1, html_p2]
        minio = MagicMock()
        crawler = ITviecListingCrawler(browser=browser, minio=minio)

        urls = crawler.crawl_keyword("python")

        assert set(urls) == {"https://itviec.com/it-jobs/job-a-111", "https://itviec.com/it-jobs/job-b-222"}
        assert browser.fetch_page.call_count == 2

    @patch("src.crawlers.itviec.listing.time.sleep", lambda *a: None)
    def test_crawl_keyword_circuit_breaker_on_empty_pages(self):
        """3 consecutive empty pages → stop."""
        browser = MagicMock()
        browser.fetch_page.return_value = "<html><body>empty</body></html>"
        minio = MagicMock()
        crawler = ITviecListingCrawler(browser=browser, minio=minio)

        urls = crawler.crawl_keyword("noresults", max_pages=10)

        assert urls == []
        assert browser.fetch_page.call_count == MAX_CONSECUTIVE_EMPTY

    @patch("src.crawlers.itviec.listing.time.sleep", lambda *a: None)
    def test_crawl_keyword_circuit_breaker_on_fetch_errors(self):
        browser = MagicMock()
        browser.fetch_page.side_effect = RuntimeError("connection failed")
        minio = MagicMock()
        crawler = ITviecListingCrawler(browser=browser, minio=minio)

        urls = crawler.crawl_keyword("fail", max_pages=10)

        assert urls == []
        assert browser.fetch_page.call_count == MAX_CONSECUTIVE_EMPTY

    @patch("src.crawlers.itviec.listing.time.sleep", lambda *a: None)
    def test_crawl_keyword_recovers_from_transient_empty(self):
        """One empty page between good pages should NOT trigger breaker."""
        good_html = make_listing_html(
            ["https://itviec.com/it-jobs/job-x-999"], page=1, max_page=3
        )
        empty_html = "<html><body>nothing</body></html>"
        good_html_p3 = make_listing_html(
            ["https://itviec.com/it-jobs/job-y-888"], page=3, max_page=3
        )

        browser = MagicMock()
        browser.fetch_page.side_effect = [good_html, empty_html, good_html_p3]
        minio = MagicMock()
        crawler = ITviecListingCrawler(browser=browser, minio=minio)

        urls = crawler.crawl_keyword("mixed", max_pages=3)

        assert "https://itviec.com/it-jobs/job-x-999" in urls
        assert "https://itviec.com/it-jobs/job-y-888" in urls

    @patch("src.crawlers.itviec.listing.time.sleep", lambda *a: None)
    def test_crawl_keyword_max_pages_caps_detected(self):
        """max_pages=1 overrides detected pagination."""
        html = make_listing_html(SAMPLE_LISTING_URLS, page=1, max_page=5)
        browser = MagicMock()
        browser.fetch_page.return_value = html
        minio = MagicMock()
        crawler = ITviecListingCrawler(browser=browser, minio=minio)

        urls = crawler.crawl_keyword("test", max_pages=1)

        assert browser.fetch_page.call_count == 1

    @patch("src.crawlers.itviec.listing.time.sleep", lambda *a: None)
    def test_crawl_all_listings_deduplicates(self):
        shared_url = "https://itviec.com/it-jobs/overlap-job-555"
        html = make_listing_html([shared_url], page=1, max_page=1)

        browser = MagicMock()
        browser.fetch_page.return_value = html
        minio = MagicMock()
        crawler = ITviecListingCrawler(browser=browser, minio=minio)

        result = crawler.crawl_all_listings(keywords=["kw1", "kw2"], max_pages=1)

        assert result["urls"] == [shared_url]
        assert result["counters"]["jobs_unique"] == 1
        assert result["counters"]["jobs_raw"] == 2

    @patch("src.crawlers.itviec.listing.time.sleep", lambda *a: None)
    def test_crawl_keyword_minio_failure_does_not_crash(self):
        html = make_listing_html(SAMPLE_LISTING_URLS, page=1, max_page=1)
        browser = MagicMock()
        browser.fetch_page.return_value = html
        minio = MagicMock()
        minio.upload_string.side_effect = RuntimeError("S3 down")
        crawler = ITviecListingCrawler(browser=browser, minio=minio)

        urls = crawler.crawl_keyword("data", max_pages=1)

        assert urls == SAMPLE_LISTING_URLS
