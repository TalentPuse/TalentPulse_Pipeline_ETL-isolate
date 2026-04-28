-- Migration: raw.crawl_log composite PK (source, job_id) for multi-source support
-- Run once: psql -U admin -d warehouse -f scripts/migrate_crawl_log_v2.sql
-- Idempotent: safe to run multiple times.

DO $$
BEGIN
    -- Drop the old single-column PK if it exists
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'crawl_log_pkey'
          AND conrelid = 'raw.crawl_log'::regclass
    ) THEN
        -- Check if PK is single-column (just job_id) vs already composite
        IF (
            SELECT count(*) FROM pg_attribute a
            JOIN pg_constraint c ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
            WHERE c.conname = 'crawl_log_pkey'
              AND c.conrelid = 'raw.crawl_log'::regclass
        ) = 1 THEN
            ALTER TABLE raw.crawl_log DROP CONSTRAINT crawl_log_pkey;
            ALTER TABLE raw.crawl_log ADD PRIMARY KEY (source, job_id);
            RAISE NOTICE 'Migrated crawl_log PK to (source, job_id)';
        ELSE
            RAISE NOTICE 'crawl_log PK is already composite, skipping';
        END IF;
    END IF;
END $$;
