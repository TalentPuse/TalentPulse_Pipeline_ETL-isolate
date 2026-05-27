-- Skill Enrichment Schema
-- Stores LLM-extracted skills from job descriptions

-- ─── Extraction Log (raw layer) ───

CREATE TABLE IF NOT EXISTS raw.skill_extraction_log (
    source          VARCHAR NOT NULL,
    source_job_id   VARCHAR NOT NULL,
    skills_json     JSONB NOT NULL DEFAULT '[]'::jsonb,
    experience_years_min INT,
    seniority_level VARCHAR,
    model_used      VARCHAR,
    tokens_used     INT,
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, source_job_id)
);

CREATE INDEX IF NOT EXISTS ix_skill_extraction_extracted_at
    ON raw.skill_extraction_log (extracted_at);
