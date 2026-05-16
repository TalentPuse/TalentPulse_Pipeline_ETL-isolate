"""Tests for LinkedIn listing crawler — parse job IDs, pagination, keyword crawling."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.crawlers.linkedin.listing import (
    LinkedInListingCrawler,
    _parse_job_ids,
    JOBS_PER_PAGE,
    MAX_START,
    MAX_CONSECUTIVE_EMPTY,
)


# ── _parse_job_ids ───────────────────────────────────────────────


class TestParseJobIds:
    def test_extracts_ids_from_html(self):
        html = '''
        <div data-entity-urn="urn:li:jobPosting:12345">Job 1</div>
        <div data-entity-urn="urn:li:jobPosting:67890">Job 2</div>
        '''
        ids = _parse_job_ids(html)
        assert ids == ["12345", "67890"]

    def test_no_ids_returns_empty(self):
        html = "<html>no job postings</html>"
        assert _parse_job_ids(html) == []

    def test_single_id(self):
        html = '<div data-entity-urn="urn:li:jobPosting:11111"></div>'
        assert _parse_job_ids(html) == ["11111"]

    def test_duplicate_ids_preserved(self):
        html = '''
        <div data-entity-urn="urn:li:jobPosting:12345"></div>
        <div data-entity-urn="urn:li:jobPosting:12345"></div>
        '''
        assert _parse_job_ids(html) == ["12345", "12345"]


# ── crawl_keyword ────────────────────────────────────────────────


class TestCrawlKeyword:
    def test_single_page(self, requests_mock, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.listing.time.sleep", lambda s: None)
        minio = MagicMock()
        crawler = LinkedInListingCrawler(minio=minio)
        requests_mock.get(
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
            text='''
            <div data-entity-urn="urn:li:jobPosting:111"></div>
            <div data-entity-urn="urn:li:jobPosting:222"></div>
            ''',
        )
        ids = crawler.crawl_keyword("data engineer", max_pages=1)
        assert ids == ["111", "222"]

    def test_dedup_across_pages(self, requests_mock, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.listing.time.sleep", lambda s: None)
        minio = MagicMock()
        crawler = LinkedInListingCrawler(minio=minio)
        requests_mock.get(
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
            [
                {"text": '<div data-entity-urn="urn:li:jobPosting:111"></div><div data-entity-urn="urn:li:jobPosting:222"></div>'},
                {"text": '<div data-entity-urn="urn:li:jobPosting:222"></div><div data-entity-urn="urn:li:jobPosting:333"></div>'},
            ],
        )
        ids = crawler.crawl_keyword("data engineer", max_pages=2)
        assert "111" in ids
        assert "222" in ids
        assert "333" in ids
        assert len(ids) == 3

    def test_stops_on_consecutive_empty(self, requests_mock, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.listing.time.sleep", lambda s: None)
        minio = MagicMock()
        crawler = LinkedInListingCrawler(minio=minio)
        requests_mock.get(
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
            "",
        )
        ids = crawler.crawl_keyword("obscure keyword xyz")
        assert ids == []

    def test_handles_request_error(self, requests_mock, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.listing.time.sleep", lambda s: None)
        minio = MagicMock()
        crawler = LinkedInListingCrawler(minio=minio)
        requests_mock.get(
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
            exc=requests.ConnectionError("refused"),
        )
        ids = crawler.crawl_keyword("data engineer", max_pages=5)
        assert ids == []

    def test_uploads_html_to_minio(self, requests_mock, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.listing.time.sleep", lambda s: None)
        minio = MagicMock()
        crawler = LinkedInListingCrawler(minio=minio)
        requests_mock.get(
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
            text='<div data-entity-urn="urn:li:jobPosting:111"></div>',
        )
        crawler.crawl_keyword("data engineer", max_pages=1)
        minio.upload_string.assert_called_once()
        call_kwargs = minio.upload_string.call_args[1] if minio.upload_string.call_args[1] else {}
        call_args = minio.upload_string.call_args[0]
        object_name = call_kwargs.get("object_name") or call_args[1]
        assert "listings/linkedin/" in object_name
        assert "data_engineer" in object_name


# ── crawl_all_listings ───────────────────────────────────────────


class TestCrawlAllListings:
    def test_multiple_keywords(self, requests_mock, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.listing.time.sleep", lambda s: None)
        minio = MagicMock()
        crawler = LinkedInListingCrawler(minio=minio)
        requests_mock.get(
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
            text='<div data-entity-urn="urn:li:jobPosting:111"></div>',
        )
        result = crawler.crawl_all_listings(keywords=["data engineer", "python"], max_pages=1)
        assert result["counters"]["keywords"] == 2
        assert result["counters"]["jobs_raw"] >= 2

    def test_default_keywords_from_config(self, requests_mock, monkeypatch):
        monkeypatch.setattr("src.crawlers.linkedin.listing.time.sleep", lambda s: None)
        minio = MagicMock()
        crawler = LinkedInListingCrawler(minio=minio)
        requests_mock.get(
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
            text="",
        )
        result = crawler.crawl_all_listings(max_pages=1)
        assert result["counters"]["keywords"] > 0
