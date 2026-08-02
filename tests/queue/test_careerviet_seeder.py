"""Tests for the CareerViet seeder."""
from unittest.mock import MagicMock

from src.crawlers.careerviet.listing import build_listing_url, extract_detail_urls
from src.queue.careerviet_seeder import extract_job_id, seed_from_urls


class TestExtractJobId:
    def test_hex_id_from_detail_url(self):
        url = "https://careerviet.vn/vi/tim-viec-lam/senior-data-analyst.35C80246.html"
        assert extract_job_id(url) == "35C80246"

    def test_id_is_upper_cased(self):
        url = "https://careerviet.vn/vi/tim-viec-lam/abc.35c80246.html"
        assert extract_job_id(url) == "35C80246"

    def test_slug_with_dots_still_resolves(self):
        url = "https://careerviet.vn/vi/tim-viec-lam/a.b.c-role.35C7F173.html"
        assert extract_job_id(url) == "35C7F173"

    def test_rejects_non_detail_urls(self):
        assert extract_job_id("https://careerviet.vn/viec-lam/data-engineer-k-vi.html") is None
        assert extract_job_id("https://example.com/vi/tim-viec-lam/x.35C80246.html") is None


class TestSeed:
    def test_batches_through_enqueue_many(self):
        """One round-trip, not one per URL — the per-row path is what made
        seeding slow over the tailnet."""
        log = MagicMock()
        log.enqueue_many.return_value = 2
        urls = [
            "https://careerviet.vn/vi/tim-viec-lam/a.35C80246.html",
            "https://careerviet.vn/vi/tim-viec-lam/b.35C7F173.html",
        ]
        result = seed_from_urls(urls, log=log)

        assert result["enqueued"] == 2
        log.enqueue_many.assert_called_once()
        items, kwargs = log.enqueue_many.call_args[0], log.enqueue_many.call_args[1]
        assert kwargs["source"] == "careerviet"
        assert items[0] == [("35C80246", urls[0]), ("35C7F173", urls[1])]

    def test_bad_urls_counted_not_crashed(self):
        log = MagicMock()
        log.enqueue_many.return_value = 1
        result = seed_from_urls(
            ["https://careerviet.vn/vi/tim-viec-lam/a.35C80246.html", "https://careerviet.vn/junk"],
            log=log,
        )
        assert result["rejected_url"] == 1
        assert result["enqueued"] == 1


class TestListingUrls:
    def test_page_one_and_page_n_differ(self):
        """Page 1 and page N use different URL shapes on CareerViet."""
        assert build_listing_url("kinh-doanh", 1) == (
            "https://careerviet.vn/viec-lam/kinh-doanh-k-vi.html"
        )
        assert build_listing_url("kinh-doanh", 3) == (
            "https://careerviet.vn/viec-lam/kinh-doanh-k-trang-3-vi.html"
        )

    def test_extract_detail_urls_absolutises_and_dedupes(self):
        html = (
            '<a href="/vi/tim-viec-lam/a.35C80246.html">x</a>'
            '<a href="/vi/tim-viec-lam/a.35C80246.html">dup</a>'
            '<a href="/vi/nha-tuyen-dung/company.html">not a job</a>'
        )
        urls = extract_detail_urls(html)
        assert urls == ["https://careerviet.vn/vi/tim-viec-lam/a.35C80246.html"]
