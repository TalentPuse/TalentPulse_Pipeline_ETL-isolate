import pytest

from src.crawlers.vietnamworks.detail.url_builder import build_detail_url, is_allowed


def test_build_detail_url_basic():
    url = build_detail_url("data-engineer-at-acme", 12345)
    assert url == "https://www.vietnamworks.com/data-engineer-at-acme-12345-jv"
    assert is_allowed(url)


def test_build_detail_url_slug_already_complete():
    url = build_detail_url("data-engineer-at-acme-12345-jv", 12345)
    assert url == "https://www.vietnamworks.com/data-engineer-at-acme-12345-jv"


def test_build_detail_url_strips_leading_slash():
    url = build_detail_url("/some-job", 99)
    assert url == "https://www.vietnamworks.com/some-job-99-jv"


def test_build_detail_url_empty_slug():
    with pytest.raises(ValueError):
        build_detail_url("", 1)


def test_is_allowed_blocks_random_paths():
    assert not is_allowed("https://www.vietnamworks.com/login")
    assert not is_allowed("https://www.vietnamworks.com/api/jobs/1")
    assert not is_allowed("https://evil.com/job-1-jv")
    assert not is_allowed("")


def test_is_allowed_accepts_canonical():
    assert is_allowed("https://www.vietnamworks.com/abc-xyz-1-jv")
    assert is_allowed("https://www.vietnamworks.com/abc-1-jv/")
