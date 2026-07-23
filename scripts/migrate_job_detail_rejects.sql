-- Quarantine table for rows that fail loader validation.
-- Append-only — keeps full history for audit/replay.
-- docker exec -i talentpulse-postgres psql -U admin -d warehouse < scripts/migrate_job_detail_rejects.sql

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

-- metabase_ro is created by migrate_metabase_user.sql, which sorts AFTER this
-- file in the migrate_*.sql glob (j < m). Applying the scripts in glob order
-- therefore reaches this GRANT before the role exists, failing the whole run
-- with "role metabase_ro does not exist". Guard it: migrate_metabase_user.sql's
-- own `GRANT SELECT ON ALL TABLES IN SCHEMA raw` covers this table anyway, so
-- the role still ends up able to read it — this line is only a belt-and-braces
-- explicit grant for standalone runs.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metabase_ro') THEN
    GRANT SELECT ON raw.job_detail_rejects TO metabase_ro;
  END IF;
END $$;
