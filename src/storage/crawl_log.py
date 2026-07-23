"""Postgres-backed crawl log + work queue for detail pages.

v2: composite PK (source, job_id) for multi-source support.
"""
import logging

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

from src.storage.db import pg_connection
from src.utils.config import config

logger = logging.getLogger(__name__)


class CrawlLog:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or config.get_db_uri()

    def _conn(self):
        return pg_connection(self.dsn)

    # ------------------------------------------------------------------ writes

    def enqueue(self, job_id: str, url: str, source: str = "vietnamworks") -> bool:
        """Insert a pending row. Returns True if a new row was added; False if already fresh."""
        if self.is_fresh(job_id, days=config.CRAWLER_RECRAWL_DAYS, source=source):
            return False
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw.crawl_log (job_id, source, status, url)
                VALUES (%s, %s, 'pending', %s)
                ON CONFLICT (source, job_id) DO UPDATE
                  SET status = CASE
                        WHEN raw.crawl_log.status IN ('success','in_progress') THEN raw.crawl_log.status
                        ELSE 'pending'
                      END,
                      url = EXCLUDED.url
                """,
                (job_id, source, url),
            )
            return cur.rowcount > 0

    def enqueue_many(self, items: "list[tuple[str, str]]", source: str = "vietnamworks") -> int:
        """Batch-enqueue (job_id, url) pairs in ONE round-trip.

        The per-row enqueue() opens a fresh connection and runs is_fresh() +
        INSERT for every URL — over the tailnet that made seeding 236 URLs take
        ~13 minutes. This sends a single execute_values INSERT on one
        connection instead. The ON CONFLICT CASE keeps rows already in
        'success'/'in_progress' untouched, so fresh jobs are still not
        re-crawled — same queue outcome as the is_fresh guard, without the
        round-trips. Returns the number of affected rows.
        """
        # Dedup by job_id: the same job often appears under several search
        # keywords, so `items` can carry the same (source, job_id) twice.
        # A single INSERT ... ON CONFLICT DO UPDATE cannot touch one conflict
        # key twice in the same command (CardinalityViolation), so collapse
        # duplicates here, keeping the last url seen.
        deduped = {job_id: url for job_id, url in items}
        rows = [(job_id, source, url) for job_id, url in deduped.items()]
        if not rows:
            return 0
        with self._conn() as conn, conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO raw.crawl_log (job_id, source, status, url)
                VALUES %s
                ON CONFLICT (source, job_id) DO UPDATE
                  SET status = CASE
                        WHEN raw.crawl_log.status IN ('success','in_progress') THEN raw.crawl_log.status
                        ELSE 'pending'
                      END,
                      url = EXCLUDED.url
                """,
                rows,
                template="(%s, %s, 'pending', %s)",
                page_size=500,
            )
            return cur.rowcount

    def claim_next(self, source: str | None = None) -> tuple[str, str] | None:
        """Atomically claim one pending row; returns (job_id, url) or None."""
        source_filter = "AND source = %s" if source else ""
        params = (source,) if source else ()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                WITH next AS (
                    SELECT source, job_id FROM raw.crawl_log
                    WHERE status = 'pending' {source_filter}
                    ORDER BY first_seen_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE raw.crawl_log c
                   SET status = 'in_progress',
                       last_attempt_at = now(),
                       retry_count = retry_count + 1
                  FROM next
                 WHERE c.source = next.source AND c.job_id = next.job_id
                RETURNING c.job_id, c.url
                """,
                params,
            )
            row = cur.fetchone()
            return (row[0], row[1]) if row else None

    def mark_success(self, job_id: str, http_status: int, raw_object_key: str,
                     run_id: str, source: str = "vietnamworks") -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE raw.crawl_log
                   SET status='success', http_status=%s, raw_object_key=%s,
                       crawl_run_id=%s, crawled_at=now(), error_message=NULL
                 WHERE source=%s AND job_id=%s
                """,
                (http_status, raw_object_key, run_id, source, job_id),
            )

    def mark_failed(self, job_id: str, http_status: int | None, error: str,
                    source: str = "vietnamworks") -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE raw.crawl_log
                   SET status='failed', http_status=%s, error_message=%s
                 WHERE source=%s AND job_id=%s
                """,
                (http_status, error[:1000], source, job_id),
            )

    def mark_expired(self, job_id: str, source: str = "vietnamworks") -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE raw.crawl_log
                   SET status='expired', http_status=404, crawled_at=now()
                 WHERE source=%s AND job_id=%s
                """,
                (source, job_id),
            )

    # ------------------------------------------------------------------ reads

    def is_fresh(self, job_id: str, days: int, source: str = "vietnamworks") -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM raw.crawl_log
                 WHERE source=%s AND job_id=%s AND status='success'
                   AND crawled_at >= now() - (%s || ' days')::interval
                """,
                (source, job_id, str(days)),
            )
            return cur.fetchone() is not None

    def stats(self, source: str | None = None) -> dict:
        with self._conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            if source:
                cur.execute(
                    "SELECT status, count(*) AS n FROM raw.crawl_log WHERE source=%s GROUP BY status",
                    (source,),
                )
            else:
                cur.execute("SELECT status, count(*) AS n FROM raw.crawl_log GROUP BY status")
            return {r["status"]: r["n"] for r in cur.fetchall()}
