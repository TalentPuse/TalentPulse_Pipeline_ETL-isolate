from unittest.mock import MagicMock

from src.queue.topcv_seeder import extract_job_id, canonical_url, seed_from_urls


def test_extract_job_id_valid():
    url = "https://www.topcv.vn/viec-lam/data-engineer-junior-middle/2114998.html"
    assert extract_job_id(url) == "2114998"


def test_extract_job_id_with_query():
    url = "https://www.topcv.vn/viec-lam/etl-dev/1599438.html?ta_source=JobSearchList_LinkDetail&x=1"
    assert extract_job_id(url) == "1599438"


def test_extract_job_id_rejects_non_detail():
    assert extract_job_id("https://www.topcv.vn/cong-ty/viettel-digital/246114.html") is None
    assert extract_job_id("https://www.topcv.vn/tim-viec-lam-data-engineer") is None


def test_canonical_url_strips_query():
    url = "https://www.topcv.vn/viec-lam/etl-dev/1599438.html?ta_source=x&amp;y=2"
    assert canonical_url(url) == "https://www.topcv.vn/viec-lam/etl-dev/1599438.html"


def test_seed_from_urls_counts_and_dedups():
    log = MagicMock()
    log.enqueue_many.return_value = 2
    urls = [
        "https://www.topcv.vn/viec-lam/a/111.html?ta_source=x",
        "https://www.topcv.vn/viec-lam/a/111.html?ta_source=y",  # dup id -> one item
        "https://www.topcv.vn/viec-lam/b/222.html",
        "https://www.topcv.vn/cong-ty/c/333.html",               # rejected
    ]
    result = seed_from_urls(urls, log=log)
    assert result["rejected_url"] == 1
    # enqueue_many called with 2 unique (job_id, url) pairs, source="topcv"
    items, = log.enqueue_many.call_args.args
    kwargs = log.enqueue_many.call_args.kwargs
    assert kwargs["source"] == "topcv"
    assert sorted(j for j, _ in items) == ["111", "222"]
    assert all(u == canonical_url(u) for _, u in items)
    assert result["enqueued"] == 2
