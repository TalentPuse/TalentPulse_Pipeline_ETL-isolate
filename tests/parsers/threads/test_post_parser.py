"""Tests for the Threads post parser.

The interesting assertions here are the NEGATIVE ones: the parser must leave
title/company/salary/location empty rather than inventing them out of the post
text or the author's handle. See the module docstring of
`src/parsers/threads/post_parser.py`.
"""
import gzip
import json
from unittest.mock import MagicMock

import pytest

from src.loaders.validators import validate
from src.parsers.threads.post_parser import (
    ThreadsParseError,
    ThreadsPostParser,
    parse_post,
    parse_posts_to_dicts,
)

POST = {
    "id": "17901234567890123",
    "text": "Team minh dang tuyen 2 ban Data Engineer (Python, dbt, Airflow) "
            "tai HCM, luong up to 3000 USD. Inbox minh nhe!",
    "media_type": "TEXT_POST",
    "permalink": "https://www.threads.net/@recruiter_vn/post/C8xYz",
    "timestamp": "2026-08-01T09:30:00+0000",
    "username": "recruiter_vn",
    "has_replies": True,
    "is_quote_post": False,
    "is_reply": False,
}


@pytest.fixture
def parser():
    # parse_html is pure; no MinIO client needed.
    return ThreadsPostParser.__new__(ThreadsPostParser)


class TestMapping:
    def test_identity_fields(self):
        d = parse_post(POST)
        assert d.source == "threads"
        assert d.source_job_id == "17901234567890123"
        assert d.source_url == POST["permalink"]
        assert d.parser_version == "threads-v1"
        assert d.parsed_at.endswith("Z")

    def test_text_becomes_description(self):
        assert parse_post(POST).job_description_text == POST["text"]

    def test_timestamp_becomes_posted_at(self):
        assert parse_post(POST).posted_at == "2026-08-01T09:30:00+0000"

    def test_blank_text_is_none_not_empty_string(self):
        assert parse_post({**POST, "text": "   "}).job_description_text is None

    def test_missing_text_is_none(self):
        post = {k: v for k, v in POST.items() if k != "text"}
        assert parse_post(post).job_description_text is None

    def test_to_dict_is_loader_ready(self):
        payload = parse_post(POST).to_dict()
        # The loader hard-fails without these two.
        assert payload["source"] == "threads"
        assert payload["source_job_id"]
        assert payload["parsed_at"]


class TestNothingIsInvented:
    """A social post has no company, no salary, no expiry. Do not make them up."""

    def test_no_title_is_derived_from_the_text(self):
        assert parse_post(POST).title is None

    def test_username_does_not_become_a_company(self):
        d = parse_post(POST)
        assert d.company_name is None
        assert d.company_id is None
        assert d.company_logo_url is None

    def test_no_salary_is_scraped_from_the_text(self):
        d = parse_post(POST)  # text literally says "up to 3000 USD"
        assert d.salary_min is None
        assert d.salary_max is None
        assert d.salary_currency is None
        assert d.is_salary_visible is False
        assert d.pretty_salary is None

    def test_no_location_is_scraped_from_the_text(self):
        assert parse_post(POST).locations == []  # text says "tai HCM"

    def test_no_expiry_is_fabricated(self):
        d = parse_post(POST)
        assert d.expired_at is None
        assert d.is_expired is False

    def test_taxonomy_lists_stay_empty(self):
        d = parse_post(POST)
        assert (d.skills, d.industries, d.benefits) == ([], [], [])

    def test_job_profile_fields_stay_empty(self):
        d = parse_post(POST)
        assert d.job_level is None
        assert d.employment_type is None
        assert d.job_function is None
        assert d.years_of_experience is None


class TestIdRequired:
    def test_missing_id_raises(self):
        post = {k: v for k, v in POST.items() if k != "id"}
        with pytest.raises(ThreadsParseError):
            parse_post(post)

    def test_falls_back_to_storage_key_id(self):
        post = {k: v for k, v in POST.items() if k != "id"}
        assert parse_post(post, source_job_id="from-key").source_job_id == "from-key"

    def test_non_dict_raises(self):
        with pytest.raises(ThreadsParseError):
            parse_post("not a post")


class TestArchiveRoundTrip:
    def test_parse_html_reads_the_archived_json(self, parser):
        d = parser.parse_html(json.dumps(POST))
        assert d.source_job_id == POST["id"]

    def test_malformed_archive_raises(self, parser):
        with pytest.raises(ThreadsParseError):
            parser.parse_html("<html>not json</html>")

    def test_job_id_comes_off_the_json_gz_key(self, parser):
        key = "details/threads/json/20260801T040000Z/17901234567890123.json.gz"
        assert parser._extract_job_id(key) == "17901234567890123"

    def test_process_one_decompresses_and_writes_parsed_json(self):
        p = ThreadsPostParser.__new__(ThreadsPostParser)
        p.minio = MagicMock()
        p.bucket = "bucket"
        body = gzip.compress(json.dumps(POST).encode("utf-8"))
        p.minio.s3_client.get_object.return_value = {"Body": MagicMock(read=lambda: body)}
        p.minio.s3_client.head_object.side_effect = Exception("not found")

        key = "details/threads/json/run/17901234567890123.json.gz"
        # _exists swallows ClientError only; make head_object look like a miss.
        p._exists = lambda k: False

        out = p.process_one(key)
        assert out == "parsed/details/threads/17901234567890123.json"
        written = json.loads(p.minio.upload_string.call_args.kwargs["content"])
        assert written["source"] == "threads"
        assert written["source_job_id"] == POST["id"]


class TestBatchHelper:
    def test_counts_unparsable_posts(self):
        payloads, failed = parse_posts_to_dicts([POST, {"text": "no id"}])
        assert len(payloads) == 1
        assert failed == 1


class TestSchemaFitIsBad:
    """Documents the consequence rather than papering over it.

    If this test ever starts failing, the schema question was resolved — update
    the parser docstring and the flow's `load_to_warehouse` default with it.
    """

    def test_every_threads_row_is_rejected_by_the_current_validator(self):
        reject = validate(parse_post(POST).to_dict())
        assert reject is not None
        assert reject[0] == "MISSING_TITLE"
