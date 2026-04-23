-- Migration: 001_user_alerts_schema.sql
--
-- Creates `user_alerts` schema for Telegram bot:
--   - subscribers: one row per Telegram chat (anonymous, identified by chat_id)
--   - subscriptions: filter criteria (skills, city, salary, etc.)
--   - alert_log: dedup — never send same (sub, job) twice
--
-- Idempotent: safe to re-run.

CREATE SCHEMA IF NOT EXISTS user_alerts;

-- ─────────────────────────────────────────────────────────────────
-- subscribers: 1 row per Telegram chat
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_alerts.subscribers (
    chat_id       bigint        PRIMARY KEY,
    username      text,
    created_at    timestamptz   NOT NULL DEFAULT now(),
    last_seen_at  timestamptz   NOT NULL DEFAULT now(),
    paused_until  timestamptz,      -- NULL = active; future ts = paused
    link_token    text,             -- one-time token from dashboard for binding (nullable)
    locale        text          NOT NULL DEFAULT 'vi'
);

-- Pending link tokens from dashboard (expire 10 min)
CREATE TABLE IF NOT EXISTS user_alerts.pending_links (
    token         text          PRIMARY KEY,
    created_at    timestamptz   NOT NULL DEFAULT now(),
    expires_at    timestamptz   NOT NULL DEFAULT (now() + interval '10 minutes'),
    consumed_by   bigint        REFERENCES user_alerts.subscribers(chat_id)
);

-- ─────────────────────────────────────────────────────────────────
-- subscriptions: filter criteria per user
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_alerts.subscriptions (
    id              serial        PRIMARY KEY,
    chat_id         bigint        NOT NULL REFERENCES user_alerts.subscribers(chat_id) ON DELETE CASCADE,
    label           text,                                  -- user-friendly name
    -- Filter criteria — NULL means "any"
    skills          text[],                                -- ARRAY['python','sql'] lowercase; ANY match
    cities          text[],                                -- ARRAY['HCMC','Hanoi']; canonical from city_map
    job_levels      text[],                                -- e.g. ['Senior','Manager']
    min_salary_vnd  bigint        CHECK (min_salary_vnd IS NULL OR min_salary_vnd >= 0),
    companies       text[],                                -- exact company name match
    created_at      timestamptz   NOT NULL DEFAULT now(),
    active          boolean       NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_chat_id
    ON user_alerts.subscriptions (chat_id)
    WHERE active;

CREATE INDEX IF NOT EXISTS idx_subscriptions_skills_gin
    ON user_alerts.subscriptions USING gin (skills);

CREATE INDEX IF NOT EXISTS idx_subscriptions_cities_gin
    ON user_alerts.subscriptions USING gin (cities);

-- ─────────────────────────────────────────────────────────────────
-- alert_log: dedup — prevent sending same job twice to same sub
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_alerts.alert_log (
    id                bigserial     PRIMARY KEY,
    subscription_id   int           NOT NULL REFERENCES user_alerts.subscriptions(id) ON DELETE CASCADE,
    source_job_id     text          NOT NULL,
    chat_id           bigint        NOT NULL,
    sent_at           timestamptz   NOT NULL DEFAULT now(),
    delivery_status   text          NOT NULL DEFAULT 'sent' CHECK (delivery_status IN ('sent','failed','skipped_rate_limit')),
    error_message     text,
    UNIQUE (subscription_id, source_job_id)
);

CREATE INDEX IF NOT EXISTS idx_alert_log_chat_recent
    ON user_alerts.alert_log (chat_id, sent_at DESC);
