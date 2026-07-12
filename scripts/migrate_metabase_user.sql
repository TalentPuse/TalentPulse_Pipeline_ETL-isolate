-- Read-only role for Metabase (and the dashboard backend's analytics queries).
--
-- Named migrate_* on purpose: pipeline-migrate.yml globs scripts/migrate_*.sql
-- and psql's each one. The old scripts/create_metabase_user.sql did NOT match
-- that glob, so it only ever ran if a human remembered to — and on a fresh VPS
-- nobody does. This file supersedes it.
--
-- Idempotent: safe to re-run on every migrate.

-- The password comes from psql's -v, never from this file: a credential written
-- into a committed .sql is one you cannot rotate without a commit.
--
-- Note the \gexec rather than a DO $$ ... $$ block. psql does NOT substitute
-- :'variables' inside dollar-quoted strings, so the obvious version silently sets
-- the password to the literal text ":'ro_password'" and every login then fails
-- for a reason nothing in the logs explains.
--
-- Create if absent, then always set the password — so this doubles as the
-- rotation path.
SELECT format('CREATE ROLE metabase_ro LOGIN PASSWORD %L', :'ro_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metabase_ro')
\gexec

SELECT format('ALTER ROLE metabase_ro WITH LOGIN PASSWORD %L', :'ro_password')
\gexec

GRANT CONNECT ON DATABASE warehouse TO metabase_ro;

-- Grant across every schema the BI layer actually reads. The old script only
-- covered `raw`, which meant Metabase and the backend both hit
-- "permission denied for schema dbt_dev_gold" the moment they queried anything
-- useful — dbt builds its models into dbt_dev_bronze/silver/gold/features.
--
-- The dbt_dev_* schemas do not exist until the first dbt run, so this loops over
-- whatever is present now rather than naming them statically. Re-running after a
-- new schema appears picks it up.
DO $$
DECLARE
  s text;
BEGIN
  FOR s IN
    SELECT nspname
    FROM pg_namespace
    WHERE nspname IN ('raw', 'normalization')
       OR nspname LIKE 'dbt_dev_%'
  LOOP
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO metabase_ro', s);
    EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO metabase_ro', s);
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT ON TABLES TO metabase_ro', s
    );
  END LOOP;
END
$$;
