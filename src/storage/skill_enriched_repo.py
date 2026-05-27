"""
Database operations for LLM-extracted skills.

Writes to raw.skill_extraction_log (full JSON extraction log).
DBT handles the raw → silver transformation via silver_skill_enriched view.
"""
from __future__ import annotations

import logging

import psycopg2.extras

from src.extractors.skill_extractor import ExtractionResult

logger = logging.getLogger(__name__)


def upsert_extraction_log(cur, result: ExtractionResult):
    skills_json = psycopg2.extras.Json([
        {
            "name": s.name,
            "name_raw": s.name_raw,
            "category": s.category,
            "importance": s.importance,
            "confidence": s.confidence,
        }
        for s in result.skills
    ])

    cur.execute("""
        INSERT INTO raw.skill_extraction_log
            (source, source_job_id, skills_json, experience_years_min,
             seniority_level, model_used, tokens_used, extracted_at)
        VALUES (%(source)s, %(source_job_id)s, %(skills_json)s,
                %(experience_years_min)s, %(seniority_level)s,
                %(model_used)s, %(tokens_used)s, now())
        ON CONFLICT (source, source_job_id) DO UPDATE SET
            skills_json = EXCLUDED.skills_json,
            experience_years_min = EXCLUDED.experience_years_min,
            seniority_level = EXCLUDED.seniority_level,
            model_used = EXCLUDED.model_used,
            tokens_used = EXCLUDED.tokens_used,
            extracted_at = now()
    """, {
        "source": result.source,
        "source_job_id": result.source_job_id,
        "skills_json": skills_json,
        "experience_years_min": result.experience_years_min,
        "seniority_level": result.seniority_level,
        "model_used": result.model_used,
        "tokens_used": result.tokens_used,
    })


def write_results(conn, results: list[ExtractionResult]) -> dict:
    cur = conn.cursor()
    n_skills = 0
    n_jobs = 0

    for result in results:
        if result.skills:
            upsert_extraction_log(cur, result)
            n_skills += len(result.skills)
            n_jobs += 1

    conn.commit()
    cur.close()

    logger.info("Wrote %d skills from %d jobs", n_skills, n_jobs)
    return {"n_skills": n_skills, "n_jobs": n_jobs}


def get_unprocessed_jobs(cur, limit: int = 100) -> list[dict]:
    cur.execute("""
        SELECT s.source, s.source_job_id,
               s.job_description_text, s.job_requirement_text
        FROM dbt_dev_silver.silver_job_detail s
        WHERE (
                  (s.job_description_text IS NOT NULL AND trim(s.job_description_text) != '')
               OR (s.job_requirement_text IS NOT NULL AND trim(s.job_requirement_text) != '')
              )
          AND NOT EXISTS (
              SELECT 1 FROM raw.skill_extraction_log e
              WHERE e.source = s.source AND e.source_job_id = s.source_job_id
          )
        ORDER BY s.parsed_at DESC NULLS LAST
        LIMIT %(limit)s
    """, {"limit": limit})

    rows = cur.fetchall()
    return [
        {
            "source": r[0],
            "source_job_id": r[1],
            "job_description_text": r[2],
            "job_requirement_text": r[3],
        }
        for r in rows
    ]


def count_unprocessed(cur) -> int:
    cur.execute("""
        SELECT count(*)
        FROM dbt_dev_silver.silver_job_detail s
        WHERE (
                  (s.job_description_text IS NOT NULL AND trim(s.job_description_text) != '')
               OR (s.job_requirement_text IS NOT NULL AND trim(s.job_requirement_text) != '')
              )
          AND NOT EXISTS (
              SELECT 1 FROM raw.skill_extraction_log e
              WHERE e.source = s.source AND e.source_job_id = s.source_job_id
          )
    """)
    return cur.fetchone()[0]
