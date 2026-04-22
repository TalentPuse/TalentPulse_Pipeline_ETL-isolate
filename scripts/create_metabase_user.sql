-- Read-only role for Metabase (and any other BI tool)
-- Grants SELECT on raw schema only. No DDL, no writes.
-- Run once: docker exec -i talentpulse-postgres psql -U admin -d warehouse < scripts/create_metabase_user.sql

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metabase_ro') THEN
    CREATE ROLE metabase_ro WITH LOGIN PASSWORD 'metabase_ro_pw';
  END IF;
END
$$;

GRANT CONNECT ON DATABASE warehouse TO metabase_ro;
GRANT USAGE   ON SCHEMA   raw       TO metabase_ro;
GRANT SELECT  ON ALL TABLES IN SCHEMA raw TO metabase_ro;

-- Future tables also auto-grant
ALTER DEFAULT PRIVILEGES IN SCHEMA raw
  GRANT SELECT ON TABLES TO metabase_ro;
