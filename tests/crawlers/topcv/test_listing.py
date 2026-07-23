from unittest.mock import MagicMock

from src.crawlers.topcv.listing import (
    extract_detail_urls,
    detect_max_page,
    TopCVListingCrawler,
)

LISTING_HTML = """
<html><body>
<a href="https://www.topcv.vn/viec-lam/data-engineer/2179021.html?ta_source=JobSearchList_LinkDetail">x</a>
<a href="https://www.topcv.vn/viec-lam/data-engineer/2179021.html?ta_source=JobSearchList_ButtonApplyFormCard">x</a>
<a href="https://www.topcv.vn/viec-lam/etl-developer/1599438.html">y</a>
<a href="https://www.topcv.vn/cong-ty/acme/999.html">company (ignore)</a>
<a href="/tim-viec-lam-data-engineer?page=2">2</a>
<a href="/tim-viec-lam-data-engineer?page=5">5</a>
</body></html>
"""


def test_extract_detail_urls_dedups_and_canonicalizes():
    urls = extract_detail_urls(LISTING_HTML)
    assert urls == [
        "https://www.topcv.vn/viec-lam/data-engineer/2179021.html",
        "https://www.topcv.vn/viec-lam/etl-developer/1599438.html",
    ]


def test_detect_max_page():
    assert detect_max_page(LISTING_HTML) == 5
    assert detect_max_page("<html>no pages</html>") == 1


def test_crawl_keyword_stops_at_detected_max():
    browser = MagicMock()
    browser.fetch_page.return_value = LISTING_HTML
    minio = MagicMock()
    crawler = TopCVListingCrawler(browser=browser, minio=minio)
    # cap pages to 1 so the test does not loop to the detected max (5)
    urls = crawler.crawl_keyword("data-engineer", max_pages=1)
    assert len(urls) == 2
    minio.upload_string.assert_called_once()
