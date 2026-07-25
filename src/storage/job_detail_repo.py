"""Postgres repository for raw.job_detail (upsert from parsed JSON)."""
import logging
from datetime import datetime
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import Json, RealDictCursor, execute_values

from src.storage.db import pg_connection
from src.utils.config import config

logger = logging.getLogger(__name__)


# Columns that get UPSERTed (excluding the PK and loaded_at which is server-side)
_UPDATABLE_COLS = [
    "source_url", "parser_version", "parsed_at",
    "title", "alias", "company_id", "company_name", "company_logo_url",
    "company_profile_text",
    "salary_min", "salary_max", "salary_currency", "is_salary_visible",
    "pretty_salary",
    "job_level", "years_of_experience", "employment_type", "job_function",
    "locations", "industries", "skills", "benefits",
    "job_description_text", "job_requirement_text",
    "posted_at", "expired_at", "last_updated_at", "is_expired",
    "num_of_views", "num_of_applications",
    # v2 Tier-1 fields
    "company_size", "company_size_id", "company_color",
    "num_of_recruits", "salary_period_id",
    "pretty_salary_vi", "pretty_salary_en",
    "working_days", "working_from_hour", "working_to_hour",
    "highest_degree_id", "language_selected", "language_selected_vi",
    "range_age", "required_resume", "required_cover_letter",
    "primary_address", "contact_name", "contact_email",
    "services", "canonical_slug", "is_active", "online_on",
]
_ALL_COLS = ["source", "source_job_id"] + _UPDATABLE_COLS

_JSON_COLS = {"job_function", "locations", "industries", "skills", "benefits", "services"}

# Long-text/JSON cols that should not be coerced
_TIMESTAMP_COLS = {"parsed_at", "posted_at", "expired_at", "last_updated_at", "online_on"}


def _coerce(col: str, value: Any) -> Any:
    if value is None:
        return None
    if col in _JSON_COLS:
        return Json(value)
    if col in _TIMESTAMP_COLS:
        # psycopg2 accepts ISO strings or datetimes; pass through.
        return value
    return value


def _build_upsert_sql() -> str:
    cols_sql = ", ".join(_ALL_COLS)
    placeholders = ", ".join(f"%({c})s" for c in _ALL_COLS)
    set_clauses = ",\n  ".join(f"{c} = EXCLUDED.{c}" for c in _UPDATABLE_COLS)
    return f"""
INSERT INTO raw.job_detail ({cols_sql}, loaded_at)
VALUES ({placeholders}, now())
ON CONFLICT (source, source_job_id) DO UPDATE SET
  {set_clauses},
  loaded_at = now()
WHERE raw.job_detail.parsed_at <= EXCLUDED.parsed_at
""".strip()


_UPSERT_SQL = _build_upsert_sql()


class JobDetailRepo:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or config.get_db_uri()

    def _conn(self):
        return pg_connection(self.dsn)

    def _row_from_payload(self, payload: dict) -> dict:
        row = {c: _coerce(c, payload.get(c)) for c in _ALL_COLS}
        # Required fields
        if not row["source"] or not row["source_job_id"]:
            raise ValueError("payload missing source or source_job_id")
        if not row["parsed_at"]:
            raise ValueError("payload missing parsed_at")
        if not row["parser_version"]:
            row["parser_version"] = "unknown"
        return row

    def upsert(self, payload: dict) -> bool:
        row = self._row_from_payload(payload)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(_UPSERT_SQL, row)
            return cur.rowcount > 0

    def upsert_many(self, payloads: Iterable[dict]) -> int:
        rows = [self._row_from_payload(p) for p in payloads]
        if not rows:
            return 0
        # A single INSERT ... ON CONFLICT DO UPDATE cannot touch one conflict
        # key twice in the same command, so collapse any duplicate
        # (source, source_job_id) in this batch, keeping the last payload.
        rows = list({(r["source"], r["source_job_id"]): r for r in rows}.values())
        # executemany() sends one INSERT per row — thousands of tailnet
        # round-trips, which is what made load_warehouse crawl. execute_values
        # batches rows into a single multi-row INSERT (one round-trip per page).
        set_clauses = ", ".join(f"{c} = EXCLUDED.{c}" for c in _UPDATABLE_COLS)
        sql = (
            f"INSERT INTO raw.job_detail ({', '.join(_ALL_COLS)}) VALUES %s "
            f"ON CONFLICT (source, source_job_id) DO UPDATE SET {set_clauses}"
        )
        values = [tuple(r[c] for c in _ALL_COLS) for r in rows]
        with self._conn() as conn, conn.cursor() as cur:
            execute_values(cur, sql, values, page_size=200)
            return cur.rowcount

    def count(self, source: str = "vietnamworks") -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM raw.job_detail WHERE source = %s", (source,))
            return cur.fetchone()[0]

    def get(self, source: str, source_job_id: str) -> dict | None:
        with self._conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM raw.job_detail WHERE source = %s AND source_job_id = %s",
                (source, source_job_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def record_reject(
        self,
        payload: dict,
        reason: str,
        detail: str = "",
        minio_key: str | None = None,
    ) -> None:
        """Append-only insert into raw.job_detail_rejects for audit/replay."""
        row = {
            "source": payload.get("source"),
            "source_job_id": payload.get("source_job_id"),
            "minio_key": minio_key,
            "parser_version": payload.get("parser_version"),
            "reject_reason": reason,
            "reject_detail": detail,
            "payload": Json(payload),
        }
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw.job_detail_rejects
                    (source, source_job_id, minio_key, parser_version,
                     reject_reason, reject_detail, payload)
                VALUES
                    (%(source)s, %(source_job_id)s, %(minio_key)s, %(parser_version)s,
                     %(reject_reason)s, %(reject_detail)s, %(payload)s)
                """,
                row,
            )

    def record_reject_many(
        self, rejects: "Iterable[tuple[dict, str, str, str | None]]"
    ) -> int:
        """Batch-insert rejects in ONE round-trip.

        `rejects` is an iterable of (payload, reason, detail, minio_key). Like
        upsert_many, this avoids opening a connection + INSERT per rejected row
        (linkedin can reject hundreds per run), which otherwise storms the
        warehouse's small connection pool.
        """
        rows = [
            (
                p.get("source"), p.get("source_job_id"), minio_key,
                p.get("parser_version"), reason, detail, Json(p),
            )
            for (p, reason, detail, minio_key) in rejects
        ]
        if not rows:
            return 0
        sql = (
            "INSERT INTO raw.job_detail_rejects "
            "(source, source_job_id, minio_key, parser_version, "
            "reject_reason, reject_detail, payload) VALUES %s"
        )
        with self._conn() as conn, conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=200)
            return cur.rowcount
