-- Migration v2: add Tier-1 high-value fields to raw.job_detail
-- Idempotent (uses IF NOT EXISTS). Run once after parser bump v1→v2.
-- docker exec -i talentpulse-postgres psql -U admin -d warehouse < scripts/migrate_job_detail_v2.sql

ALTER TABLE raw.job_detail
  ADD COLUMN IF NOT EXISTS company_size           VARCHAR,
  ADD COLUMN IF NOT EXISTS company_size_id        INT,
  ADD COLUMN IF NOT EXISTS company_color          VARCHAR(8),
  ADD COLUMN IF NOT EXISTS num_of_recruits        INT,
  ADD COLUMN IF NOT EXISTS salary_period_id       INT,
  ADD COLUMN IF NOT EXISTS pretty_salary_vi       TEXT,
  ADD COLUMN IF NOT EXISTS pretty_salary_en       TEXT,
  ADD COLUMN IF NOT EXISTS working_days           VARCHAR,
  ADD COLUMN IF NOT EXISTS working_from_hour      VARCHAR(8),
  ADD COLUMN IF NOT EXISTS working_to_hour        VARCHAR(8),
  ADD COLUMN IF NOT EXISTS highest_degree_id      INT,
  ADD COLUMN IF NOT EXISTS language_selected      VARCHAR,
  ADD COLUMN IF NOT EXISTS language_selected_vi   VARCHAR,
  ADD COLUMN IF NOT EXISTS range_age              VARCHAR,
  ADD COLUMN IF NOT EXISTS primary_address        TEXT,
  ADD COLUMN IF NOT EXISTS contact_name           VARCHAR,
  ADD COLUMN IF NOT EXISTS contact_email          VARCHAR,
  ADD COLUMN IF NOT EXISTS required_resume        BOOLEAN,
  ADD COLUMN IF NOT EXISTS required_cover_letter  BOOLEAN,
  ADD COLUMN IF NOT EXISTS services               JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS canonical_slug         VARCHAR,
  ADD COLUMN IF NOT EXISTS is_active              BOOLEAN,
  ADD COLUMN IF NOT EXISTS online_on              TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_job_detail_company_size ON raw.job_detail(company_size_id);
CREATE INDEX IF NOT EXISTS ix_job_detail_degree       ON raw.job_detail(highest_degree_id);
