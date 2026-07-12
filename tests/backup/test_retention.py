"""Retention is the only part of the backup that DELETES things, so it gets tests.

The failure this guards against is not "we kept too much" — storage is free at
this scale — it is pruning something we still needed.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.backup.r2_backup import BackupConfig, keep_reason, prune

TODAY = date(2026, 7, 12)  # a Sunday


def make_cfg(**over) -> BackupConfig:
    base = dict(
        db_host="h", db_port="5432", db_user="u", db_password="p", db_name="warehouse",
        s3_endpoint_url="https://r2", s3_access_key="a", s3_secret_key="s",
        bucket="talentpulse-backup", parquet_schemas=["dbt_dev_gold"],
        keep_daily_days=14, keep_weekly_weeks=8, keep_monthly_months=12,
        min_keep_dumps=0, pgdump_via_docker="",
    )
    base.update(over)
    return BackupConfig(**base)


class FakeS3:
    """Just enough S3 to exercise prune()."""

    def __init__(self, keys: list[str]):
        self.keys = list(keys)
        self.deleted: list[str] = []

    def get_paginator(self, _op):
        contents = [{"Key": k} for k in self.keys]
        return type(
            "P", (), {"paginate": lambda _s, **kw: iter([{"Contents": contents}])}
        )()

    def delete_objects(self, Bucket, Delete):  # noqa: N803 - boto3 casing
        self.deleted.extend(o["Key"] for o in Delete["Objects"])


def test_keeps_every_backup_inside_the_daily_window():
    cfg = make_cfg()
    for age in range(0, 14):
        d = TODAY - timedelta(days=age)
        assert keep_reason(d, TODAY, cfg) == "daily", f"dropped a {age}-day-old backup"


def test_thins_to_weekly_after_the_daily_window():
    cfg = make_cfg()
    sunday = date(2026, 6, 21)      # 21 days old, a Sunday
    wednesday = date(2026, 6, 24)   # 18 days old, mid-week

    assert keep_reason(sunday, TODAY, cfg) == "weekly"
    assert keep_reason(wednesday, TODAY, cfg) is None


def test_keeps_first_of_month_long_after_weeklies_expire():
    cfg = make_cfg()
    first = date(2026, 2, 1)        # >8 weeks old, but the 1st
    other = date(2026, 2, 12)       # >8 weeks old, nothing special

    assert keep_reason(first, TODAY, cfg) == "monthly"
    assert keep_reason(other, TODAY, cfg) is None


def test_drops_everything_past_the_monthly_horizon():
    cfg = make_cfg()
    assert keep_reason(date(2024, 1, 1), TODAY, cfg) is None


def test_prune_deletes_only_what_no_tier_keeps():
    cfg = make_cfg()
    keys = [
        "db/2026-07-12/warehouse-a.dump",  # today             -> daily
        "db/2026-07-05/warehouse-b.dump",  # 7d                -> daily
        "db/2026-06-21/warehouse-c.dump",  # 21d, Sunday       -> weekly
        "db/2026-06-24/warehouse-d.dump",  # 18d, Wednesday    -> DROP
        "db/2026-02-01/warehouse-e.dump",  # 1st of month      -> monthly
        "db/2026-02-12/warehouse-f.dump",  # old, unremarkable -> DROP
    ]
    s3 = FakeS3(keys)

    deleted = prune(s3, cfg, "db/", today=TODAY)

    assert sorted(deleted) == sorted([
        "db/2026-06-24/warehouse-d.dump",
        "db/2026-02-12/warehouse-f.dump",
    ])
    assert s3.deleted == deleted


def test_min_keep_floor_protects_the_newest_backups():
    """Even if every backup is ancient, the floor must not let prune empty the bucket."""
    cfg = make_cfg(min_keep_dumps=3)
    keys = [f"db/2020-0{m}-15/warehouse.dump" for m in range(1, 7)]  # all long expired
    s3 = FakeS3(keys)

    deleted = prune(s3, cfg, "db/", today=TODAY)

    assert len(deleted) == 3, "floor should have spared the 3 newest"
    survivors = set(keys) - set(deleted)
    assert survivors == {
        "db/2020-06-15/warehouse.dump",
        "db/2020-05-15/warehouse.dump",
        "db/2020-04-15/warehouse.dump",
    }, "the floor must spare the NEWEST, not an arbitrary three"


def test_prune_ignores_keys_it_cannot_date():
    """An unrecognised layout is left alone rather than guessed at and deleted."""
    cfg = make_cfg()
    s3 = FakeS3(["db/not-a-date/whatever.dump", "db/README.txt"])

    assert prune(s3, cfg, "db/", today=TODAY) == []
    assert s3.deleted == []


@pytest.mark.parametrize("prefix", ["db/", "parquet/"])
def test_same_rules_apply_to_both_prefixes(prefix):
    cfg = make_cfg()
    s3 = FakeS3([f"{prefix}2026-06-24/x", f"{prefix}2026-07-12/y"])

    assert prune(s3, cfg, prefix, today=TODAY) == [f"{prefix}2026-06-24/x"]
