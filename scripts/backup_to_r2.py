"""Run a warehouse backup to R2 without Prefect.

The scheduled path is the Prefect flow (orchestration/flows/backup_pipeline.py,
driven by .github/workflows/pipeline-backup.yml). This CLI exists for ad-hoc runs
— before a risky migration, or to take a dump from a laptop — and shares the same
code, so the two cannot drift.

    DB_USER=... DB_PASSWORD=... S3_ENDPOINT_URL=... S3_ACCESS_KEY=... \
    S3_SECRET_KEY=... python scripts/backup_to_r2.py
"""

from __future__ import annotations

import logging
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backup import r2_backup  # noqa: E402
from src.backup.r2_backup import BackupConfig  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backup")


def main() -> int:
    cfg = BackupConfig.from_env()
    s3 = r2_backup.s3_client(cfg)
    r2_backup.ensure_bucket(s3, cfg)

    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        dump = tmp / f"warehouse-{stamp}.dump"
        size = r2_backup.run_pg_dump(cfg, dump)
        key = f"db/{day}/{dump.name}"
        r2_backup.upload(s3, cfg, dump, key)
        log.info("dump  %s -> %s (%s bytes, verified)", cfg.db_name, key, f"{size:,}")

        for path, name, rows in r2_backup.export_parquet(cfg, tmp):
            pkey = f"parquet/{day}/{name}"
            psize = r2_backup.upload(s3, cfg, path, pkey)
            log.info("parquet %s -> %d rows, %s bytes", name, rows, f"{psize:,}")

    deleted = r2_backup.prune(s3, cfg, "db/") + r2_backup.prune(s3, cfg, "parquet/")
    log.info("pruned %d object(s)", len(deleted))
    log.info("backup complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
