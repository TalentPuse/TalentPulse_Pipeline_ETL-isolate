-- Migration: 04-normalization-schema.sql
--
-- Creates `normalization` schema for the production normalization engine.
-- Rules are updatable at runtime via SQL INSERT/UPDATE — no code redeployment needed.
--
-- Idempotent: safe to re-run.

CREATE SCHEMA IF NOT EXISTS normalization;

-- ─────────────────────────────────────────────────────────────────
-- Category rules: keyword patterns → job categories
-- Replaces seeds/job_title_category_map.csv
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS normalization.category_rule (
    id              SERIAL PRIMARY KEY,
    pattern         TEXT NOT NULL,
    job_category    TEXT NOT NULL,
    priority        INT NOT NULL DEFAULT 10,
    match_mode      TEXT NOT NULL DEFAULT 'word_boundary'  CHECK (match_mode IN ('word_boundary', 'exact', 'regex')),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pattern, job_category)
);

CREATE INDEX IF NOT EXISTS ix_category_rule_active ON normalization.category_rule (is_active, priority);

-- ─────────────────────────────────────────────────────────────────
-- Level rules: multi-signal level inference
-- Replaces seeds/job_level_map.csv + hardcoded CASE in silver_job_detail
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS normalization.level_rule (
    id              SERIAL PRIMARY KEY,
    signal_type     TEXT NOT NULL CHECK (signal_type IN ('raw_field_value', 'title_keyword', 'experience_range')),
    pattern         TEXT NOT NULL,
    job_level       TEXT NOT NULL CHECK (job_level IN ('Intern/Student', 'Fresher/Entry level', 'Mid-level', 'Senior', 'Manager', 'Director+')),
    priority        INT NOT NULL DEFAULT 10,
    confidence      NUMERIC(3,2) NOT NULL DEFAULT 0.80 CHECK (confidence >= 0 AND confidence <= 1.0),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (signal_type, pattern, job_level)
);

-- ─────────────────────────────────────────────────────────────────
-- Skill synonym map: variants → canonical skill name
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS normalization.skill_synonym (
    id              SERIAL PRIMARY KEY,
    canonical_name  TEXT NOT NULL,
    synonym         TEXT NOT NULL,
    source          TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (synonym)
);

-- ─────────────────────────────────────────────────────────────────
-- City alias map: city name variants → canonical + region
-- Replaces seeds/city_map.csv
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS normalization.city_alias (
    id              SERIAL PRIMARY KEY,
    city_canonical  TEXT NOT NULL,
    region          TEXT NOT NULL,
    alias           TEXT NOT NULL,
    source          TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (alias, source)
);

-- ─────────────────────────────────────────────────────────────────
-- Normalization results: one row per (source, source_job_id, run_id)
-- Audit trail with confidence scoring and method tracking
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS normalization.job_normalization (
    source              VARCHAR NOT NULL,
    source_job_id       VARCHAR NOT NULL,
    run_id              VARCHAR NOT NULL,
    run_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Category
    job_category        TEXT NOT NULL DEFAULT 'Other',
    category_method     TEXT,
    category_confidence NUMERIC(3,2),
    category_matched_on TEXT,

    -- Level
    job_level           TEXT NOT NULL DEFAULT 'Mid-level',
    level_method        TEXT,
    level_confidence    NUMERIC(3,2),
    level_signals       JSONB DEFAULT '{}',

    -- City
    city_canonical      TEXT,
    city_region         TEXT,
    city_method         TEXT,
    city_confidence     NUMERIC(3,2),

    -- Dedup
    canonical_job_id    UUID,
    dedup_method        TEXT,
    dedup_confidence    NUMERIC(3,2),

    PRIMARY KEY (source, source_job_id, run_id)
);

CREATE INDEX IF NOT EXISTS ix_norm_run ON normalization.job_normalization (run_id);
CREATE INDEX IF NOT EXISTS ix_norm_run_at ON normalization.job_normalization (run_at DESC);
CREATE INDEX IF NOT EXISTS ix_norm_canonical ON normalization.job_normalization (canonical_job_id) WHERE canonical_job_id IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────
-- Drift log: unmapped value detection
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS normalization.drift_log (
    id              BIGSERIAL PRIMARY KEY,
    run_id          VARCHAR NOT NULL,
    dimension       TEXT NOT NULL CHECK (dimension IN ('category', 'level', 'city', 'skill')),
    raw_value       TEXT NOT NULL,
    source          VARCHAR,
    frequency       INT NOT NULL DEFAULT 1,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    status          TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewed', 'resolved', 'regression'))
);

CREATE INDEX IF NOT EXISTS ix_drift_status ON normalization.drift_log (status, dimension);
CREATE UNIQUE INDEX IF NOT EXISTS ix_drift_unique ON normalization.drift_log (dimension, raw_value, source);

-- ─────────────────────────────────────────────────────────────────
-- Cross-source dedup: job clusters
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS normalization.job_cluster (
    canonical_job_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_title    TEXT NOT NULL,
    canonical_company  TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS normalization.job_cluster_member (
    canonical_job_id   UUID NOT NULL REFERENCES normalization.job_cluster(canonical_job_id) ON DELETE CASCADE,
    source             VARCHAR NOT NULL,
    source_job_id      VARCHAR NOT NULL,
    similarity_score   NUMERIC(3,2),
    PRIMARY KEY (canonical_job_id, source, source_job_id)
);
