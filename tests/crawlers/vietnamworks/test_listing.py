"""Tests for VietnamWorks listing crawler.

v2: server-side filter via jobFunctionsV3.jobFunctionV3Id, multi-page
pagination using meta.nbPages, payload builder is unit-testable.
"""
from unittest.mock import patch

import requests_mock

from src.crawlers.vietnamworks.listing import VietnamWorksListingCrawler


@patch("src.crawlers.vietnamworks.listing.MinioClient")
def test_build_payload_default(_mock_minio):
    crawler = VietnamWorksListingCrawler()
    payload = crawler.build_payload(keyword="Data Engineer", page=2)
    assert payload["query"] == "Data Engineer"
    assert payload["page"] == 2
    assert payload["hitsPerPage"] == 50
    # Only city filter by default
    assert payload["filter"] == [
        {"field": "workingLocations.cityId", "value": "29"}
    ]


@patch("src.crawlers.vietnamworks.listing.MinioClient")
def test_build_payload_with_function_filter(_mock_minio):
    crawler = VietnamWorksListingCrawler()
    payload = crawler.build_payload(
        keyword="", page=0, job_function_ids=[27, 28]
    )
    fields = [(f["field"], f["value"]) for f in payload["filter"]]
    assert ("workingLocations.cityId", "29") in fields
    assert ("jobFunctionsV3.jobFunctionV3Id", "27") in fields
    assert ("jobFunctionsV3.jobFunctionV3Id", "28") in fields


@patch("src.crawlers.vietnamworks.listing.MinioClient")
def test_fetch_search_page_success(mock_minio_class):
    crawler = VietnamWorksListingCrawler()
    fake_response = {
        "meta": {"nbHits": 1, "nbPages": 1, "page": 0},
        "data": [{"jobId": 1, "jobTitle": "DE"}],
    }
    with requests_mock.Mocker() as m:
        m.post(crawler.SEARCH_ENDPOINT, json=fake_response, status_code=200)
        result = crawler.fetch_search_page(keyword="x", page=0)
    assert result == fake_response


@patch("src.crawlers.vietnamworks.listing.MinioClient")
def test_fetch_search_page_failure_returns_none(mock_minio_class):
    crawler = VietnamWorksListingCrawler()
    with requests_mock.Mocker() as m:
        m.post(crawler.SEARCH_ENDPOINT, status_code=500)
        result = crawler.fetch_search_page(keyword="x", page=0)
    assert result is None


@patch("src.crawlers.vietnamworks.listing.MinioClient")
def test_crawl_all_listings_uploads_each_page(mock_minio_class):
    """Multi-page: nbPages=3 → 3 fetches → 3 uploads."""
    crawler = VietnamWorksListingCrawler()
    mock_minio = mock_minio_class.return_value

    def make_resp(page):
        return {
            "meta": {"nbHits": 150, "nbPages": 3, "page": page},
            "data": [{"jobId": page * 50 + i} for i in range(50)],
        }

    with requests_mock.Mocker() as m:
        m.post(crawler.SEARCH_ENDPOINT, json=make_resp(0))
        # requests_mock returns the same response for repeated POSTs unless we
        # use response_list — but fetch only uses the body, page differs in payload
        # so we just assert call_count.
        counters = crawler.crawl_all_listings(keyword="x", max_pages=10)

    assert counters["pages"] == 3
    assert counters["jobs"] == 150
    assert mock_minio.upload_string.call_count == 3


@patch("src.crawlers.vietnamworks.listing.MinioClient")
def test_crawl_all_listings_respects_max_pages_cap(mock_minio_class):
    crawler = VietnamWorksListingCrawler()
    mock_minio = mock_minio_class.return_value

    big_resp = {
        "meta": {"nbHits": 500, "nbPages": 10, "page": 0},
        "data": [{"jobId": i} for i in range(50)],
    }
    with requests_mock.Mocker() as m:
        m.post(crawler.SEARCH_ENDPOINT, json=big_resp)
        counters = crawler.crawl_all_listings(keyword="x", max_pages=2)

    assert counters["pages"] == 2
    assert mock_minio.upload_string.call_count == 2


@patch("src.crawlers.vietnamworks.listing.MinioClient")
def test_crawl_passes_function_filter_through(mock_minio_class):
    crawler = VietnamWorksListingCrawler()
    with requests_mock.Mocker() as m:
        m.post(
            crawler.SEARCH_ENDPOINT,
            json={"meta": {"nbPages": 1}, "data": [{"jobId": 1}]},
        )
        crawler.crawl_all_listings(
            keyword="", max_pages=1, job_function_ids=[27]
        )
        sent = m.last_request.json()
    fields = [(f["field"], f["value"]) for f in sent["filter"]]
    assert ("jobFunctionsV3.jobFunctionV3Id", "27") in fields


@patch("src.crawlers.vietnamworks.listing.MinioClient")
def test_crawl_stops_on_fetch_failure(mock_minio_class):
    crawler = VietnamWorksListingCrawler()
    mock_minio = mock_minio_class.return_value
    with requests_mock.Mocker() as m:
        m.post(crawler.SEARCH_ENDPOINT, status_code=500)
        counters = crawler.crawl_all_listings(keyword="x", max_pages=5)
    assert counters["pages"] == 0
    assert counters["failures"] == 1
    mock_minio.upload_string.assert_not_called()
