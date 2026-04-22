from unittest.mock import MagicMock

from src.parsers.vietnamworks.detail.detail_parser import DetailParser
from tests.parsers.vietnamworks.detail._fixtures import load_fixture_html


def test_parse_full_fixture():
    p = DetailParser(minio=MagicMock())
    d = p.parse_html(load_fixture_html("2041552"), source_job_id="2041552")
    assert d.source == "vietnamworks"
    assert d.source_job_id == "2041552"
    assert d.title == "CDP Backend / Data Engineer (Laravel, ETL, Olap)"
    assert d.alias == "cdp-backend-data-engineer-laravel-etl-olap"
    assert d.source_url == "https://www.vietnamworks.com/cdp-backend-data-engineer-laravel-etl-olap-2041552-jv"
    assert d.company_name == "Công ty TNHH THƯ VIỆN PHÁP LUẬT"
    assert d.company_id == 123392
    assert d.is_salary_visible is True
    assert d.salary_min == 25000000
    assert d.salary_max == 40000000
    assert d.locations and d.locations[0]["city"] == "Ho Chi Minh"
    assert d.locations[0]["city_vi"] == "Hồ Chí Minh"
    assert len(d.skills) >= 1
    assert all("name" in s for s in d.skills)
    assert d.posted_at and d.expired_at
    assert isinstance(d.num_of_views, int)
    assert d.parsed_at.endswith("Z")

    # v2 Tier-1 fields
    assert d.parser_version == "v2"
    assert d.company_color == "#633403"
    assert d.working_days == "T2 - T7"
    assert d.working_from_hour == "07:45"
    assert d.working_to_hour == "17:30"
    assert d.contact_name and d.contact_email
    assert d.contact_email == "tuyendung@thuvienphapluat.vn"
    assert d.canonical_slug == "cdp-backend-data-engineer-laravel-etl-olap-2041552-jv"
    assert isinstance(d.services, list) and len(d.services) >= 1
    assert d.highest_degree_id is not None
    assert d.pretty_salary_vi and "tr" in d.pretty_salary_vi
    assert d.pretty_salary_en and "m" in d.pretty_salary_en
    assert d.required_resume is True
    assert d.is_active is True
    assert d.online_on  # ISO timestamp string
    assert d.primary_address  # full street address
    assert d.range_age  # always present


def test_parse_hidden_salary():
    p = DetailParser(minio=MagicMock())
    d = p.parse_html(load_fixture_html("2030397"), source_job_id="2030397")
    assert d.is_salary_visible is False
    assert d.salary_min is None
    assert d.salary_max is None
    assert d.title  # still extracted
    assert d.company_name


def test_parse_usd_intern():
    p = DetailParser(minio=MagicMock())
    d = p.parse_html(load_fixture_html("2041384"), source_job_id="2041384")
    # Intern job with 200-300 values; could be USD. At minimum verify values sane.
    assert d.title
    # Salary either visible with small values or hidden - both acceptable
    if d.is_salary_visible:
        assert d.salary_min and d.salary_min <= 1000  # not millions VND


def test_process_one_skips_when_exists():
    from botocore.exceptions import ClientError
    minio = MagicMock()
    # head_object succeeds → exists
    minio.s3_client.head_object.return_value = {}
    p = DetailParser(minio=minio)
    result = p.process_one("details/vietnamworks/html/run/12345.html.gz")
    assert result is None
    minio.s3_client.get_object.assert_not_called()
    minio.upload_string.assert_not_called()


def test_process_one_parses_and_uploads(tmp_path):
    import gzip
    from botocore.exceptions import ClientError

    html = load_fixture_html("2041552")
    gz = gzip.compress(html.encode())

    minio = MagicMock()
    # head_object raises ClientError → doesn't exist
    minio.s3_client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    minio.s3_client.get_object.return_value = {"Body": MagicMock(read=lambda: gz)}

    p = DetailParser(minio=minio)
    parsed_key = p.process_one("details/vietnamworks/html/run/2041552.html.gz")
    assert parsed_key == "parsed/details/vietnamworks/2041552.json"
    minio.upload_string.assert_called_once()
    _, kwargs = minio.upload_string.call_args
    assert kwargs["object_name"] == "parsed/details/vietnamworks/2041552.json"
    assert kwargs["content_type"] == "application/json"
    assert "CDP Backend" in kwargs["content"]
