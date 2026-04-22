-- Migration: raw.crawl_log table for VietnamWorks detail crawler
-- Run once: psql -U admin -d warehouse -f scripts/migrate_crawl_log.sql

CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.crawl_log (
    job_id          VARCHAR PRIMARY KEY,
    source          VARCHAR NOT NULL DEFAULT 'vietnamworks',
    status          VARCHAR NOT NULL,            -- pending|in_progress|success|failed|expired
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

CREATE INDEX IF NOT EXISTS ix_crawl_log_status ON raw.crawl_log(status);
CREATE INDEX IF NOT EXISTS ix_crawl_log_crawled_at ON raw.crawl_log(crawled_at);
