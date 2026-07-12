"""Warehouse backup to Cloudflare R2.

Two artifacts, on purpose — they answer different questions:

  db/<date>/warehouse-<ts>.dump
      pg_dump custom format (-Fc, compressed). This is THE backup: it restores
      the database exactly — schema, indexes, constraints, sequences — with a
      single `pg_restore`. Parquet cannot do that; it stores column values and
      nothing else, so a Parquet-only "backup" leaves you rebuilding DDL by hand.

  parquet/<date>/<schema>.<table>.parquet
      Analytics archive of the dbt gold/feature models, zstd-compressed. Query
      it straight off R2 with DuckDB without restoring anything. A convenience,
      NOT a backup — do not plan a recovery around it.

Restore:
    pg_restore -h HOST -U USER -d warehouse --clean --if-exists warehouse-<ts>.dump
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import boto3
import botocore
import pandas as pd
import psycopg2

log = logging.getLogger(__name__)

# Objects are keyed <prefix>/<YYYY-MM-DD>/<file>; retention reads the date back
# out of the key rather than trusting S3 LastModified, which a re-upload resets.
_DATE_IN_KEY = re.compile(r"/(\d{4}-\d{2}-\d{2})/")


@dataclass(frozen=True)
class BackupConfig:
    db_host: str
    db_port: str
    db_user: str
    db_password: str
    db_name: str
    s3_endpoint_url: str
    s3_access_key: str
    s3_secret_key: str
    bucket: str
    parquet_schemas: list[str]
    # Grandfather-father-son retention.
    keep_daily_days: int
    keep_weekly_weeks: int
    keep_monthly_months: int
    min_keep_dumps: int
    # Local-dev escape hatch: run pg_dump inside the postgres container instead
    # of from PATH, so this can be exercised on a machine with no postgres
    # client installed. Empty in CI, where the image ships a real pg_dump.
    pgdump_via_docker: str

    @classmethod
    def from_env(cls) -> "BackupConfig":
        return cls(
            db_host=os.environ.get("DB_HOST", "localhost"),
            db_port=os.environ.get("DB_PORT", "5432"),
            db_user=os.environ["DB_USER"],
            db_password=os.environ["DB_PASSWORD"],
            db_name=os.environ.get("DB_NAME", "warehouse"),
            s3_endpoint_url=os.environ["S3_ENDPOINT_URL"],
            s3_access_key=os.environ["S3_ACCESS_KEY"],
            s3_secret_key=os.environ["S3_SECRET_KEY"],
            bucket=os.environ.get("BACKUP_BUCKET", "talentpulse-backup"),
            parquet_schemas=[
                s.strip()
                for s in os.environ.get(
                    "PARQUET_SCHEMAS", "dbt_dev_gold,dbt_dev_feature"
                ).split(",")
                if s.strip()
            ],
            keep_daily_days=int(os.environ.get("KEEP_DAILY_DAYS", "14")),
            keep_weekly_weeks=int(os.environ.get("KEEP_WEEKLY_WEEKS", "8")),
            keep_monthly_months=int(os.environ.get("KEEP_MONTHLY_MONTHS", "12")),
            min_keep_dumps=int(os.environ.get("MIN_KEEP_DUMPS", "7")),
            pgdump_via_docker=os.environ.get("BACKUP_PGDUMP_VIA_DOCKER", "").strip(),
        )


def s3_client(cfg: BackupConfig):
    return boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint_url,
        aws_access_key_id=cfg.s3_access_key,
        aws_secret_access_key=cfg.s3_secret_key,
        region_name="auto",
    )


def ensure_bucket(s3, cfg: BackupConfig) -> None:
    try:
        s3.head_bucket(Bucket=cfg.bucket)
    except botocore.exceptions.ClientError as e:
        raise RuntimeError(f"backup bucket {cfg.bucket!r} not reachable: {e}") from e


def run_pg_dump(cfg: BackupConfig, dest: Path) -> int:
    """pg_dump -Fc the whole database into dest. Returns bytes written."""
    host = "localhost" if cfg.pgdump_via_docker else cfg.db_host
    args = [
        "--host", host,
        "--port", cfg.db_port,
        "--username", cfg.db_user,
        "--dbname", cfg.db_name,
        "--format=custom",
        "--compress=9",
        "--no-owner",
        "--no-privileges",
    ]

    if cfg.pgdump_via_docker:
        cmd = ["docker", "exec", "-e", f"PGPASSWORD={cfg.db_password}",
               cfg.pgdump_via_docker, "pg_dump", *args]
        env = None
    else:
        cmd = ["pg_dump", *args]
        env = {**os.environ, "PGPASSWORD": cfg.db_password}

    with dest.open("wb") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, env=env)

    if proc.returncode != 0:
        raise RuntimeError(
            f"pg_dump failed: {proc.stderr.decode(errors='replace').strip()}"
        )
    size = dest.stat().st_size
    if size == 0:
        raise RuntimeError("pg_dump produced an empty file")
    return size


def export_parquet(cfg: BackupConfig, tmpdir: Path) -> list[tuple[Path, str, int]]:
    """Dump every table in the configured schemas to zstd Parquet.

    Returns (path, object_name, row_count) per table.
    """
    conn = psycopg2.connect(
        host=cfg.db_host, port=cfg.db_port, user=cfg.db_user,
        password=cfg.db_password, dbname=cfg.db_name,
    )
    out: list[tuple[Path, str, int]] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema = ANY(%s) AND table_type = 'BASE TABLE'
                ORDER BY table_schema, table_name
                """,
                (cfg.parquet_schemas,),
            )
            tables = cur.fetchall()

        if not tables:
            log.warning("no tables in schemas %s — parquet export is empty",
                        cfg.parquet_schemas)

        for schema, table in tables:
            df = pd.read_sql_query(f'SELECT * FROM "{schema}"."{table}"', conn)
            # jsonb columns arrive as dict/list objects; pyarrow cannot infer a
            # type for a mixed object column, so serialise those to text. The
            # pg_dump keeps the real jsonb, so nothing is lost overall.
            for col in df.columns:
                if df[col].dtype == object and df[col].map(
                    lambda v: isinstance(v, (dict, list))
                ).any():
                    df[col] = df[col].map(lambda v: None if v is None else str(v))

            path = tmpdir / f"{schema}.{table}.parquet"
            df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
            out.append((path, f"{schema}.{table}.parquet", len(df)))
    finally:
        conn.close()
    return out


def upload(s3, cfg: BackupConfig, path: Path, key: str) -> int:
    """Upload, then verify by re-reading the size R2 actually stored."""
    s3.upload_file(str(path), cfg.bucket, key)
    remote = s3.head_object(Bucket=cfg.bucket, Key=key)["ContentLength"]
    local = path.stat().st_size
    if remote != local:
        raise RuntimeError(
            f"upload size mismatch for {key}: local={local} remote={remote}"
        )
    return remote


def keep_reason(d: date, today: date, cfg: BackupConfig) -> str | None:
    """Grandfather-father-son: why this date's backup survives, or None to drop.

    Daily backups thin into weeklies and then monthlies rather than falling off a
    single cliff, so corruption noticed two months late is still recoverable —
    that is the failure this scheme exists for. A flat "delete after 30 days"
    gives you nothing once the bad data is 31 days old.
    """
    age_days = (today - d).days
    if age_days < cfg.keep_daily_days:
        return "daily"
    if d.weekday() == 6 and age_days < cfg.keep_weekly_weeks * 7:
        return "weekly"
    if d.day == 1 and age_days < cfg.keep_monthly_months * 31:
        return "monthly"
    return None


def prune(s3, cfg: BackupConfig, prefix: str, today: date | None = None) -> list[str]:
    """Delete objects under prefix that no retention tier keeps.

    Returns the keys deleted.
    """
    today = today or datetime.now(timezone.utc).date()

    objs: list[dict] = []
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=cfg.bucket, Prefix=prefix
    ):
        objs.extend(page.get("Contents", []))
    if not objs:
        return []

    # Keys embed the date, so a reverse sort is newest-first. That makes the
    # min-keep floor below protect the *most recent* backups, not random ones.
    objs.sort(key=lambda o: o["Key"], reverse=True)

    doomed: list[str] = []
    for i, obj in enumerate(objs):
        if i < cfg.min_keep_dumps:
            continue  # a backup system that can prune itself to zero is not one
        m = _DATE_IN_KEY.search(obj["Key"])
        if not m:
            continue  # unrecognised layout: leave it alone rather than guess
        if keep_reason(date.fromisoformat(m.group(1)), today, cfg) is None:
            doomed.append(obj["Key"])

    for i in range(0, len(doomed), 1000):  # DeleteObjects caps at 1000 keys
        s3.delete_objects(
            Bucket=cfg.bucket,
            Delete={"Objects": [{"Key": k} for k in doomed[i:i + 1000]]},
        )
    return doomed
