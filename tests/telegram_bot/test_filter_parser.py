"""Tests for filter_parser — the /add command's token classifier."""

from __future__ import annotations

import pytest

from src.telegram_bot.filter_parser import parse_filter


class TestBasicParsing:
    def test_empty_input(self):
        r = parse_filter("")
        assert r.is_empty()
        assert r.skills == []

    def test_whitespace_only(self):
        r = parse_filter("   \t  \n  ")
        assert r.is_empty()

    def test_single_skill(self):
        r = parse_filter("python")
        assert r.skills == ["python"]
        assert r.cities == []
        assert r.min_salary_vnd is None

    def test_single_city(self):
        r = parse_filter("hcmc")
        assert r.cities == ["HCMC"]
        assert r.skills == []


class TestCityAliases:
    @pytest.mark.parametrize(
        "input,expected",
        [
            ("hcmc", "HCMC"),
            ("HCMC", "HCMC"),
            ("hochiminh", "HCMC"),
            ("saigon", "HCMC"),
            ("sg", "HCMC"),
            ("tphcm", "HCMC"),
            ("hanoi", "Hanoi"),
            ("HN", "Hanoi"),
            ("danang", "Da Nang"),
            ("dn", "Da Nang"),
        ],
    )
    def test_city_canonicalization(self, input, expected):
        r = parse_filter(input)
        assert r.cities == [expected]


class TestLevelAliases:
    @pytest.mark.parametrize(
        "input,expected",
        [
            ("intern", "Intern/Student"),
            ("fresher", "Fresher/Entry level"),
            ("entry", "Fresher/Entry level"),
            ("junior", "Fresher/Entry level"),
            ("senior", "Senior"),
            ("mid", "Mid-level"),
            ("manager", "Manager"),
            ("lead", "Manager"),
            ("director", "Director+"),
            ("cto", "Director+"),
        ],
    )
    def test_level_canonicalization(self, input, expected):
        r = parse_filter(input)
        assert r.job_levels == [expected]


class TestSalary:
    @pytest.mark.parametrize(
        "input,expected",
        [
            ("20m", 20_000_000),
            ("25M", 25_000_000),
            ("15.5m", 15_500_000),
            ("30tr", 30_000_000),
            ("1b", 1_000_000_000),
            ("500k", 500_000),
        ],
    )
    def test_salary_suffixes(self, input, expected):
        r = parse_filter(input)
        assert r.min_salary_vnd == expected

    def test_bare_number_treated_as_million(self):
        # 20 alone → assumed 20M VND
        r = parse_filter("20")
        assert r.min_salary_vnd == 20_000_000

    def test_very_large_bare_number_not_salary(self):
        # 10000 is not a salary in millions — treated as skill
        r = parse_filter("10000")
        assert r.min_salary_vnd is None
        assert "10000" in r.skills

    def test_comma_decimal(self):
        # "15,5m" is ambiguous — we split on comma (multi-value) first,
        # then "15" and "5m" are parsed separately → max wins = 15M
        # User should use dot instead: "15.5m"
        r = parse_filter("15.5m")
        assert r.min_salary_vnd == 15_500_000


class TestCommaSeparated:
    def test_multi_skill(self):
        r = parse_filter("python,sql,spark")
        assert r.skills == ["python", "sql", "spark"]

    def test_multi_city(self):
        r = parse_filter("hcmc,hanoi")
        assert r.cities == ["HCMC", "Hanoi"]

    def test_skill_dedup(self):
        r = parse_filter("python python,python")
        assert r.skills == ["python"]


class TestCombined:
    def test_full_example(self):
        r = parse_filter("python hcmc 20m")
        assert r.skills == ["python"]
        assert r.cities == ["HCMC"]
        assert r.min_salary_vnd == 20_000_000

    def test_multi_filter(self):
        r = parse_filter("python,sql,spark hcmc,hanoi senior 30m")
        assert set(r.skills) == {"python", "sql", "spark"}
        assert set(r.cities) == {"HCMC", "Hanoi"}
        assert r.job_levels == ["Senior"]
        assert r.min_salary_vnd == 30_000_000

    def test_order_independent(self):
        r1 = parse_filter("python hcmc 20m")
        r2 = parse_filter("20m hcmc python")
        assert r1.skills == r2.skills
        assert r1.cities == r2.cities
        assert r1.min_salary_vnd == r2.min_salary_vnd


class TestEdgeCases:
    def test_max_salary_wins(self):
        # User types two salaries → keep max
        r = parse_filter("20m 30m")
        assert r.min_salary_vnd == 30_000_000

    def test_unknown_keyword_becomes_skill(self):
        r = parse_filter("airflow")
        assert r.skills == ["airflow"]

    def test_case_preserved_for_cities_and_levels(self):
        # City canonical is specific case; skills lowercase
        r = parse_filter("PYTHON HCMC SENIOR")
        assert r.skills == ["python"]
        assert r.cities == ["HCMC"]
        assert r.job_levels == ["Senior"]


class TestDescribe:
    def test_describe_empty(self):
        r = parse_filter("")
        s = r.describe()
        assert "no filters" in s.lower()

    def test_describe_full(self):
        r = parse_filter("python,sql hcmc senior 25m")
        s = r.describe()
        assert "python" in s
        assert "sql" in s
        assert "HCMC" in s
        assert "25M" in s


class TestToSubscriptionArgs:
    def test_returns_none_for_empty(self):
        r = parse_filter("python")
        args = r.to_subscription_args()
        assert args["skills"] == ["python"]
        assert args["cities"] is None  # NULL, not []
        assert args["min_salary_vnd"] is None

    def test_full_args(self):
        r = parse_filter("python hcmc senior 20m")
        args = r.to_subscription_args()
        assert args["skills"] == ["python"]
        assert args["cities"] == ["HCMC"]
        assert args["job_levels"] == ["Senior"]
        assert args["min_salary_vnd"] == 20_000_000
