"""Warehouse backup flow — pg_dump + Parquet to Cloudflare R2.

Runs after every other pipeline has finished for the day (VNW 09:00, ITviec
11:00, LinkedIn 13:00, skill-extraction 15:00, alerts 14:00/19:00 VN), so each
backup captures a full day's work rather than a half-built warehouse.

See src/backup/r2_backup.py for why there are two artifacts and why the Parquet
one is not the backup.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.artifacts import create_markdown_artifact

from src.backup import r2_backup
from src.backup.r2_backup import BackupConfig


@task(name="pg_dump", retries=1, retry_delay_seconds=60, timeout_seconds=1800)
def dump_and_upload(cfg: BackupConfig, tmpdir: str, day: str, stamp: str) -> dict:
    log = get_run_logger()
    s3 = r2_backup.s3_client(cfg)
    r2_backup.ensure_bucket(s3, cfg)

    dest = Path(tmpdir) / f"warehouse-{stamp}.dump"
    size = r2_backup.run_pg_dump(cfg, dest)
    key = f"db/{day}/{dest.name}"
    uploaded = r2_backup.upload(s3, cfg, dest, key)
    log.info("pg_dump %s bytes -> s3://%s/%s (verified)", f"{size:,}", cfg.bucket, key)
    return {"key": key, "bytes": uploaded}


@task(name="export_parquet", retries=1, retry_delay_seconds=30, timeout_seconds=1800)
def parquet_and_upload(cfg: BackupConfig, tmpdir: str, day: str) -> list[dict]:
    log = get_run_logger()
    s3 = r2_backup.s3_client(cfg)

    results: list[dict] = []
    for path, name, rows in r2_backup.export_parquet(cfg, Path(tmpdir)):
        key = f"parquet/{day}/{name}"
        size = r2_backup.upload(s3, cfg, path, key)
        log.info("parquet %s: %d rows, %s bytes", name, rows, f"{size:,}")
        results.append({"table": name, "rows": rows, "bytes": size})
    return results


@task(name="prune_old_backups", retries=1, retry_delay_seconds=30)
def prune_old(cfg: BackupConfig) -> dict:
    log = get_run_logger()
    s3 = r2_backup.s3_client(cfg)

    deleted_db = r2_backup.prune(s3, cfg, "db/")
    deleted_pq = r2_backup.prune(s3, cfg, "parquet/")
    log.info("pruned %d dump(s), %d parquet file(s)", len(deleted_db), len(deleted_pq))
    return {"dumps_deleted": len(deleted_db), "parquet_deleted": len(deleted_pq)}


@flow(name="warehouse-backup")
def backup_flow() -> dict:
    """pg_dump + Parquet the warehouse to R2, then apply GFS retention."""
    log = get_run_logger()
    cfg = BackupConfig.from_env()

    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    # One tempdir for both tasks: the dump grows with the warehouse and there is
    # no reason to hold it on disk after the upload has been verified.
    with tempfile.TemporaryDirectory() as td:
        dump = dump_and_upload(cfg, td, day, stamp)
        parquet = parquet_and_upload(cfg, td, day)

    pruned = prune_old(cfg)

    total_parquet = sum(p["bytes"] for p in parquet)
    create_markdown_artifact(
        key="warehouse-backup",
        markdown=(
            f"# Warehouse backup {day}\n\n"
            f"**Dump:** `{dump['key']}` — {dump['bytes']:,} bytes "
            f"(restore with `pg_restore --clean --if-exists`)\n\n"
            f"**Parquet:** {len(parquet)} table(s), {total_parquet:,} bytes total\n\n"
            + "\n".join(f"- `{p['table']}` — {p['rows']:,} rows" for p in parquet)
            + f"\n\n**Pruned:** {pruned['dumps_deleted']} dump(s), "
            f"{pruned['parquet_deleted']} parquet file(s)\n"
        ),
        description="Daily warehouse backup to R2",
    )

    result = {"dump": dump, "parquet": parquet, "pruned": pruned}
    log.info("backup complete: %s", result["dump"]["key"])
    return result


if __name__ == "__main__":
    backup_flow()
