-- User alerts schema for Telegram bot.

CREATE SCHEMA IF NOT EXISTS user_alerts;

CREATE TABLE IF NOT EXISTS user_alerts.subscribers (
    chat_id       bigint        PRIMARY KEY,
    username      text,
    created_at    timestamptz   NOT NULL DEFAULT now(),
    last_seen_at  timestamptz   NOT NULL DEFAULT now(),
    paused_until  timestamptz,
    link_token    text,
    locale        text          NOT NULL DEFAULT 'vi'
);

CREATE TABLE IF NOT EXISTS user_alerts.pending_links (
    token         text          PRIMARY KEY,
    created_at    timestamptz   NOT NULL DEFAULT now(),
    expires_at    timestamptz   NOT NULL DEFAULT (now() + interval '10 minutes'),
    consumed_by   bigint        REFERENCES user_alerts.subscribers(chat_id)
);

CREATE TABLE IF NOT EXISTS user_alerts.subscriptions (
    id              serial        PRIMARY KEY,
    chat_id         bigint        NOT NULL REFERENCES user_alerts.subscribers(chat_id) ON DELETE CASCADE,
    label           text,
    skills          text[],
    cities          text[],
    job_levels      text[],
    min_salary_vnd  bigint        CHECK (min_salary_vnd IS NULL OR min_salary_vnd >= 0),
    companies       text[],
    created_at      timestamptz   NOT NULL DEFAULT now(),
    active          boolean       NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_chat_id
    ON user_alerts.subscriptions (chat_id) WHERE active;

CREATE INDEX IF NOT EXISTS idx_subscriptions_skills_gin
    ON user_alerts.subscriptions USING gin (skills);

CREATE INDEX IF NOT EXISTS idx_subscriptions_cities_gin
    ON user_alerts.subscriptions USING gin (cities);

CREATE TABLE IF NOT EXISTS user_alerts.alert_log (
    id                bigserial     PRIMARY KEY,
    subscription_id   int           NOT NULL REFERENCES user_alerts.subscriptions(id) ON DELETE CASCADE,
    source_job_id     text          NOT NULL,
    chat_id           bigint        NOT NULL,
    sent_at           timestamptz   NOT NULL DEFAULT now(),
    delivery_status   text          NOT NULL DEFAULT 'sent'
                      CHECK (delivery_status IN ('sent','failed','skipped_rate_limit')),
    error_message     text,
    UNIQUE (subscription_id, source_job_id)
);

CREATE INDEX IF NOT EXISTS idx_alert_log_chat_recent
    ON user_alerts.alert_log (chat_id, sent_at DESC);
