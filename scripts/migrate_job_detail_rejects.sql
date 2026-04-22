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

GRANT SELECT ON raw.job_detail_rejects TO metabase_ro;
