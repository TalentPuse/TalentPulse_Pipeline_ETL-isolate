import pytest

from src.parsers.vietnamworks.detail.rsc_decoder import ParseError, decode, find_main_job_ref
from tests.parsers.vietnamworks.detail._fixtures import load_fixture_html


def test_decode_returns_ref_table():
    html = load_fixture_html("2041552")
    table = decode(html)
    assert len(table) > 10
    # All keys are hex
    for k in table:
        assert all(c in "0123456789abcdef" for c in k)


def test_find_main_job_ref():
    html = load_fixture_html("2041552")
    table = decode(html)
    main = find_main_job_ref(table)
    assert main["jobTitle"] == "CDP Backend / Data Engineer (Laravel, ETL, Olap)"
    assert main["jobId"] == 2041552


def test_decode_raises_on_empty():
    with pytest.raises(ParseError):
        decode("")


def test_decode_raises_when_no_chunks():
    with pytest.raises(ParseError):
        decode("<html><body>hi</body></html>")


def test_find_main_raises_when_missing():
    with pytest.raises(ParseError):
        find_main_job_ref({"1": {"foo": "bar"}})
