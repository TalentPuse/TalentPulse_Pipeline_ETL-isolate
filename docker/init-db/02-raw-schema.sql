-- Raw schema: crawl_log + job_detail + job_detail_rejects
-- Runs against the default "warehouse" database.

CREATE SCHEMA IF NOT EXISTS raw;

-- ── crawl_log ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw.crawl_log (
    job_id          VARCHAR PRIMARY KEY,
    source          VARCHAR NOT NULL DEFAULT 'vietnamworks',
    status          VARCHAR NOT NULL,
    url             TEXT NOT NULL,
    first_seen_at   TIMESTAMPTZ DEFAULT now(),
    last_attempt_at TIMESTAMPTZ,
    crawled_at      TIMESTAMPTZ,
    retry_count     INT DEFAULT 0,
    http_status     INT,
    error_message   TEXT,
    raw_object_key  TEXT,
    crawl_run_id    VARCHAR
);

CREATE INDEX IF NOT EXISTS ix_crawl_log_status     ON raw.crawl_log(status);
CREATE INDEX IF NOT EXISTS ix_crawl_log_crawled_at ON raw.crawl_log(crawled_at);

-- ── job_detail ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw.job_detail (
    source              VARCHAR NOT NULL,
    source_job_id       VARCHAR NOT NULL,
    source_url          TEXT,
    parser_version      VARCHAR NOT NULL,
    parsed_at           TIMESTAMPTZ NOT NULL,
    loaded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    title               TEXT,
    alias               VARCHAR,
    company_id          BIGINT,
    company_name        TEXT,
    company_logo_url    TEXT,
    company_profile_text TEXT,

    salary_min          NUMERIC,
    salary_max          NUMERIC,
    salary_currency     VARCHAR(8),
    is_salary_visible   BOOLEAN DEFAULT FALSE,
    pretty_salary       TEXT,

    job_level           VARCHAR,
    years_of_experience INT,
    employment_type     VARCHAR,
    job_function        JSONB,

    locations           JSONB NOT NULL DEFAULT '[]'::jsonb,
    industries          JSONB NOT NULL DEFAULT '[]'::jsonb,
    skills              JSONB NOT NULL DEFAULT '[]'::jsonb,
    benefits            JSONB NOT NULL DEFAULT '[]'::jsonb,

    job_description_text TEXT,
    job_requirement_text TEXT,

    posted_at           TIMESTAMPTZ,
    expired_at          TIMESTAMPTZ,
    last_updated_at     TIMESTAMPTZ,
    is_expired          BOOLEAN DEFAULT FALSE,

    num_of_views        INT,
    num_of_applications INT,

    -- v2: Tier-1 expanded fields
    company_size        VARCHAR,
    company_size_id     INT,
    company_color       VARCHAR,
    num_of_recruits     INT,
    salary_period_id    INT,
    pretty_salary_vi    TEXT,
    pretty_salary_en    TEXT,
    working_days        VARCHAR,
    working_from_hour   VARCHAR,
    working_to_hour     VARCHAR,
    highest_degree_id   INT,
    language_selected   TEXT,
    language_selected_vi TEXT,
    range_age           VARCHAR,
    required_resume     BOOLEAN,
    required_cover_letter BOOLEAN,
    primary_address     TEXT,
    contact_name        VARCHAR,
    contact_email       VARCHAR,
    services            JSONB NOT NULL DEFAULT '[]'::jsonb,
    canonical_slug      TEXT,
    is_active           BOOLEAN,
    online_on           TIMESTAMPTZ,

    PRIMARY KEY (source, source_job_id)
);

CREATE INDEX IF NOT EXISTS ix_job_detail_company    ON raw.job_detail(company_id);
CREATE INDEX IF NOT EXISTS ix_job_detail_posted_at  ON raw.job_detail(posted_at);
CREATE INDEX IF NOT EXISTS ix_job_detail_expired_at ON raw.job_detail(expired_at);
CREATE INDEX IF NOT EXISTS ix_job_detail_skills     ON raw.job_detail USING GIN (skills);
CREATE INDEX IF NOT EXISTS ix_job_detail_locations  ON raw.job_detail USING GIN (locations);

-- ── job_detail_rejects ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw.job_detail_rejects (
    id              BIGSERIAL PRIMARY KEY,
    source          VARCHAR NOT NULL,
    source_job_id   VARCHAR NOT NULL,
    minio_key       TEXT,
    parser_version  VARCHAR,
    reject_reason   VARCHAR NOT NULL,
    reject_detail   TEXT,
    payload         JSONB NOT NULL,
    rejected_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_rejects_source_job ON raw.job_detail_rejects(source, source_job_id);
CREATE INDEX IF NOT EXISTS ix_rejects_reason     ON raw.job_detail_rejects(reject_reason);
CREATE INDEX IF NOT EXISTS ix_rejects_at         ON raw.job_detail_rejects(rejected_at);
