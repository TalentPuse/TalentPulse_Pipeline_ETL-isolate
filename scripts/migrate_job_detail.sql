-- Migration: raw.job_detail table for VietnamWorks (and future sources)
-- Run once: docker exec -i talentpulse-postgres psql -U admin -d warehouse < scripts/migrate_job_detail.sql

CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.job_detail (
    -- Identity
    source              VARCHAR NOT NULL,
    source_job_id       VARCHAR NOT NULL,
    source_url          TEXT,
    parser_version      VARCHAR NOT NULL,
    parsed_at           TIMESTAMPTZ NOT NULL,
    loaded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Core
    title               TEXT,
    alias               VARCHAR,
    company_id          BIGINT,
    company_name        TEXT,
    company_logo_url    TEXT,
    company_profile_text TEXT,

    -- Compensation
    salary_min          NUMERIC,
    salary_max          NUMERIC,
    salary_currency     VARCHAR(8),
    is_salary_visible   BOOLEAN DEFAULT FALSE,
    pretty_salary       TEXT,

    -- Job profile
    job_level           VARCHAR,
    years_of_experience INT,
    employment_type     VARCHAR,
    job_function        JSONB,

    -- Lists (kept as JSONB; dbt will unpack downstream)
    locations           JSONB NOT NULL DEFAULT '[]'::jsonb,
    industries          JSONB NOT NULL DEFAULT '[]'::jsonb,
    skills              JSONB NOT NULL DEFAULT '[]'::jsonb,
    benefits            JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Long text (already stripped to plain by parser)
    job_description_text TEXT,
    job_requirement_text TEXT,

    -- Lifecycle
    posted_at           TIMESTAMPTZ,
    expired_at          TIMESTAMPTZ,
    last_updated_at     TIMESTAMPTZ,
    is_expired          BOOLEAN DEFAULT FALSE,

    -- Engagement
    num_of_views        INT,
    num_of_applications INT,

    PRIMARY KEY (source, source_job_id)
);

CREATE INDEX IF NOT EXISTS ix_job_detail_company    ON raw.job_detail(company_id);
CREATE INDEX IF NOT EXISTS ix_job_detail_posted_at  ON raw.job_detail(posted_at);
CREATE INDEX IF NOT EXISTS ix_job_detail_expired_at ON raw.job_detail(expired_at);
CREATE INDEX IF NOT EXISTS ix_job_detail_skills     ON raw.job_detail USING GIN (skills);
CREATE INDEX IF NOT EXISTS ix_job_detail_locations  ON raw.job_detail USING GIN (locations);
