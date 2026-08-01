"""Normalization runner — orchestrates all matchers, reads raw data, writes results.

Usage:
    from src.normalizer.runner import NormalizerRunner
    runner = NormalizerRunner()
    result = runner.run()
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

from src.normalizer.matchers.category import match_category
from src.normalizer.models import DriftEntry, NormResult
from src.utils.config import config

logger = logging.getLogger(__name__)


class NormalizerRunner:
    """Read raw.job_detail, normalize, write to normalization.job_normalization."""

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or config.get_db_uri()

    # ─── Rule loaders ─────────────────────────────────────────────

    def _load_category_rules(self, conn) -> list[dict]:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT pattern, job_category, priority, match_mode, is_active "
                "FROM normalization.category_rule WHERE is_active = true "
                "ORDER BY priority ASC"
            )
            return [dict(r) for r in cur.fetchall()]

    # ─── Main run ─────────────────────────────────────────────────

    def run(self, since: datetime | None = None) -> dict:
        """Run normalization on all rows (or rows loaded after `since`).

        Returns counters: normalized, drift, errors.
        """
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        counters = {"normalized": 0, "drift": 0, "errors": 0, "pruned": 0}

        conn = psycopg2.connect(self.dsn)
        try:
            category_rules = self._load_category_rules(conn)
            logger.info(f"Loaded {len(category_rules)} category rules")

            # Fetch raw rows
            rows = self._fetch_raw_rows(conn, since)
            logger.info(f"Processing {len(rows)} raw rows (run_id={run_id})")

            results: list[NormResult] = []
            drift_entries: list[DriftEntry] = []

            for row in rows:
                try:
                    result = self._normalize_one(row, category_rules)
                    results.append(result)

                    # Drift detection for category
                    if result.category_method == "default":
                        drift_entries.append(DriftEntry(
                            dimension="category",
                            raw_value=row.get("title", "")[:200],
                            source=row["source"],
                        ))
                except Exception as e:
                    logger.error(f"Error normalizing {row['source']}/{row['source_job_id']}: {e}")
                    counters["errors"] += 1

            # Write results
            self._write_results(conn, results, run_id)
            self._write_drift(conn, drift_entries, run_id)
            counters["pruned"] = self._prune_old_runs(conn)
            conn.commit()

            counters["normalized"] = len(results)
            counters["drift"] = len(drift_entries)
            logger.info(f"Normalization run {run_id} done: {counters}")
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return counters

    def _fetch_raw_rows(self, conn, since: datetime | None = None) -> list[dict]:
        """Fetch raw job_detail rows for normalization."""
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            sql = """
                SELECT source, source_job_id, title, job_level, job_function,
                       years_of_experience, locations, skills
                FROM raw.job_detail
            """
            params = []
            if since:
                sql += " WHERE loaded_at >= %s"
                params.append(since)
            sql += " ORDER BY loaded_at"
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def _normalize_one(self, row: dict, category_rules: list[dict]) -> NormResult:
        """Apply all matchers to a single row."""
        title = row.get("title") or ""
        source = row["source"]
        source_job_id = row["source_job_id"]

        # Parse job_function from JSON if needed
        job_function = row.get("job_function")
        if isinstance(job_function, str):
            try:
                job_function = json.loads(job_function)
            except (json.JSONDecodeError, ValueError):
                job_function = None

        # Category
        cat = match_category(title, source, job_function, category_rules)

        return NormResult(
            source=source,
            source_job_id=source_job_id,
            job_category=cat.value,
            category_method=cat.method,
            category_confidence=cat.confidence,
            category_matched_on=cat.matched_on,
        )

    def _write_results(self, conn, results: list[NormResult], run_id: str) -> None:
        """Batch write normalization results in ONE multi-row INSERT.

        Was one execute() per row — thousands of tailnet round-trips that made
        the normalize task blow its 600s Prefect timeout on large runs.
        """
        if not results:
            return
        from psycopg2.extras import execute_values

        rows = [
            (r.source, r.source_job_id, run_id, r.job_category,
             r.category_method, r.category_confidence, r.category_matched_on)
            for r in results
        ]
        sql = (
            "INSERT INTO normalization.job_normalization "
            "(source, source_job_id, run_id, job_category, category_method, "
            "category_confidence, category_matched_on) VALUES %s"
        )
        with conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=500)

    def _prune_old_runs(self, conn, keep_runs: int = 3) -> int:
        """Drop normalization rows from all but the newest `keep_runs` runs.

        This table is append-only and every run re-normalizes EVERY raw row, so
        it grows by one row per job per run forever. Measured on prod
        2026-08-01: 100,186 rows describing 4,383 actual jobs across 48 runs —
        24 MB, already the second-largest table in a 89 MB warehouse, and
        growing by ~2,000 rows on every pipeline run.

        The cost is not just disk. silver_job_detail resolves a category with
        `distinct on (source, source_job_id) ... order by run_at desc` over this
        whole table, so every stale generation makes the silver view slower.

        Safe because a run always writes a row for every current job, so the
        newest run alone is sufficient for the DISTINCT ON; the extra two are
        kept only so a bad rule change can be eyeballed against what it replaced.
        Returns rows deleted.
        """
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM normalization.job_normalization
                 WHERE run_id NOT IN (
                     SELECT run_id
                       FROM normalization.job_normalization
                      GROUP BY run_id
                      ORDER BY max(run_at) DESC
                      LIMIT %s
                 )
                """,
                (keep_runs,),
            )
            return cur.rowcount

    def _write_drift(self, conn, entries: list[DriftEntry], run_id: str) -> None:
        """Batch-upsert drift log entries in ONE INSERT.

        Deduped by (dimension, raw_value, source) first: a single INSERT ...
        ON CONFLICT cannot touch the same conflict key twice, and per-row
        execute() over the tailnet was slow.
        """
        if not entries:
            return
        from psycopg2.extras import execute_values

        rows = list({
            (d.dimension, d.raw_value, d.source): (run_id, d.dimension, d.raw_value, d.source)
            for d in entries
        }.values())
        sql = (
            "INSERT INTO normalization.drift_log "
            "(run_id, dimension, raw_value, source, frequency, first_seen_at, last_seen_at) "
            "VALUES %s "
            "ON CONFLICT (dimension, raw_value, source) DO UPDATE SET "
            "frequency = drift_log.frequency + 1, last_seen_at = now(), "
            "status = CASE WHEN drift_log.status = 'resolved' THEN 'regression' "
            "ELSE drift_log.status END, run_id = EXCLUDED.run_id"
        )
        with conn.cursor() as cur:
            execute_values(
                cur, sql, rows,
                template="(%s, %s, %s, %s, 1, now(), now())", page_size=500,
            )


def main() -> None:
    """CLI entry point for standalone normalization run."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    runner = NormalizerRunner()
    result = runner.run()
    print(f"Result: {result}")


if __name__ == "__main__":
    main()
