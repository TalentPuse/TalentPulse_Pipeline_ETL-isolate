"""
Alert matcher — finds new jobs matching user subscriptions, sends Telegram messages.

Run as cron job after `dbt build`:
    python -m src.alerts.match

Flow:
  1. SELECT (subscription, new_job) pairs where filter matches and not yet sent
  2. For each match -> format message -> send via Telegram Bot API -> log
  3. Idempotent: alert_log dedup ensures no double-send

Window: jobs posted in last 24 hours (configurable via LOOKBACK_HOURS env var).

Rate limiting:
  - Telegram limit: 30 msg/sec global, 1 msg/sec per chat_id
  - We chunk by chat_id, sleep 1.1s between sends to same user
  - Max 50 alerts per chat per run (avoid spam)

Exit codes:
  0 = success (some or zero matches)
  1 = config error (missing TELEGRAM_BOT_TOKEN)
  2 = DB connection error
  3 = partial failure (some sends failed but logged)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

LOG = logging.getLogger("alerts.match")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://admin:password@localhost:5432/warehouse",
)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "24"))
MAX_ALERTS_PER_USER = int(os.getenv("MAX_ALERTS_PER_USER", "50"))
PER_CHAT_DELAY_SEC = 1.1

# ─── Match query ────────────────────────────────────────────────────
MATCH_SQL = text("""
WITH job_skills AS (
    SELECT
        source,
        source_job_id,
        ARRAY_AGG(DISTINCT lower(skill_name_norm)) AS skills
    FROM dbt_dev_silver.silver_skill_long
    WHERE skill_name_norm IS NOT NULL
    GROUP BY source, source_job_id
),
new_jobs AS (
    SELECT
        j.source,
        j.source_job_id,
        j.title,
        j.company_name,
        j.city_canonical,
        j.job_level,
        j.salary_vnd_monthly_avg,
        j.posted_at,
        j.source_url AS url,
        COALESCE(s.skills, ARRAY[]::text[]) AS skills
    FROM dbt_dev_silver.silver_job_detail j
    LEFT JOIN job_skills s
        ON s.source = j.source AND s.source_job_id = j.source_job_id
    WHERE j.posted_at > now() - make_interval(hours => :lookback)
      AND j.is_active
)
SELECT
    sub.id              AS subscription_id,
    sub.chat_id         AS chat_id,
    sub.label           AS sub_label,
    j.source,
    j.source_job_id,
    j.title,
    j.company_name,
    j.city_canonical,
    j.job_level,
    j.salary_vnd_monthly_avg,
    j.posted_at,
    j.url,
    j.skills            AS job_skills
FROM user_alerts.subscriptions sub
JOIN user_alerts.subscribers sb ON sb.chat_id = sub.chat_id
CROSS JOIN new_jobs j
WHERE sub.active
  AND (sb.paused_until IS NULL OR sb.paused_until < now())
  AND (sub.skills      IS NULL OR sub.skills && j.skills)
  AND (sub.cities      IS NULL OR j.city_canonical = ANY(sub.cities))
  AND (sub.job_levels  IS NULL OR j.job_level     = ANY(sub.job_levels))
  AND (sub.companies   IS NULL OR j.company_name  = ANY(sub.companies))
  AND (sub.min_salary_vnd IS NULL
       OR (j.salary_vnd_monthly_avg IS NOT NULL
           AND j.salary_vnd_monthly_avg >= sub.min_salary_vnd))
  AND NOT EXISTS (
      SELECT 1 FROM user_alerts.alert_log al
      WHERE al.subscription_id = sub.id
        AND al.source_job_id   = j.source_job_id
  )
ORDER BY sub.chat_id, j.posted_at DESC
""")

LOG_INSERT = text("""
    INSERT INTO user_alerts.alert_log
      (subscription_id, source_job_id, chat_id, delivery_status, error_message)
    VALUES (:sub_id, :job_id, :chat_id, :status, :error)
    ON CONFLICT (subscription_id, source_job_id) DO NOTHING
""")


# ─── Match result ───────────────────────────────────────────────────

@dataclass
class Match:
    subscription_id: int
    chat_id: int
    sub_label: Optional[str]
    source: str
    source_job_id: str
    title: str
    company_name: Optional[str]
    city_canonical: Optional[str]
    job_level: Optional[str]
    salary_vnd_monthly_avg: Optional[float]
    posted_at: object
    url: Optional[str]
    job_skills: list


# ─── Source labels ──────────────────────────────────────────────────

SOURCE_LABEL = {
    "vietnamworks": "VietnamWorks",
    "itviec": "ITviec",
    "linkedin": "LinkedIn",
}

FALLBACK_URL = {
    "vietnamworks": "https://www.vietnamworks.com",
    "itviec": "https://itviec.com",
    "linkedin": "https://www.linkedin.com",
}


def format_message(m: Match) -> str:
    source_label = SOURCE_LABEL.get(m.source, m.source)
    fallback_url = FALLBACK_URL.get(m.source, "")

    lines = [
        f"🆕 <b>New job match</b>"
        + (f" — <i>{escape_html(m.sub_label)}</i>" if m.sub_label else ""),
        "",
        f"💼 <b>{escape_html(m.title)}</b>",
    ]
    if m.company_name:
        lines.append(f"🏢 {escape_html(m.company_name)}")

    meta_parts = []
    if m.city_canonical:
        meta_parts.append(f"📍 {escape_html(m.city_canonical)}")
    if m.job_level:
        meta_parts.append(f"🎯 {escape_html(m.job_level)}")
    if m.salary_vnd_monthly_avg:
        meta_parts.append(f"💰 ~{m.salary_vnd_monthly_avg / 1_000_000:.1f}M VND")
    if meta_parts:
        lines.append(" · ".join(meta_parts))

    if m.job_skills:
        skill_str = ", ".join(escape_html(s) for s in m.job_skills[:8])
        lines.append(f"🔧 {skill_str}")

    link_url = m.url or fallback_url
    if link_url:
        lines.append("")
        lines.append(f'<a href="{escape_html(link_url)}">View on {escape_html(source_label)} ↗</a>')

    return "\n".join(lines)


def escape_html(text: object) -> str:
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─── Telegram API (sync) ────────────────────────────────────────────

def send_telegram(
    client: httpx.Client, token: str, chat_id: int, msg: str
) -> tuple[bool, Optional[str]]:
    url = TELEGRAM_API_URL.format(token=token, method="sendMessage")
    try:
        resp = client.post(
            url,
            json={
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15.0,
        )
        if resp.status_code == 200:
            return True, None
        err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        LOG.warning("Telegram send failed chat_id=%s: %s", chat_id, err)
        return False, err
    except httpx.HTTPError:
        LOG.exception("Telegram HTTP error chat_id=%s", chat_id)
        return False, "http_error"


# ─── Main flow ──────────────────────────────────────────────────────

def run_match() -> int:
    if not TELEGRAM_BOT_TOKEN:
        LOG.error("TELEGRAM_BOT_TOKEN env var required")
        return 1

    try:
        engine = create_engine(DATABASE_URL, pool_size=3, max_overflow=0)
    except Exception:
        LOG.exception("Engine creation failed")
        return 2

    try:
        with Session(engine) as session:
            result = session.execute(MATCH_SQL, {"lookback": LOOKBACK_HOURS})
            rows = result.mappings().all()
    except Exception:
        LOG.exception("Match query failed")
        engine.dispose()
        return 2

    LOG.info("Found %d match candidates (lookback %dh)", len(rows), LOOKBACK_HOURS)
    if not rows:
        engine.dispose()
        return 0

    matches = [Match(**dict(r)) for r in rows]

    by_chat: dict[int, list[Match]] = {}
    for m in matches:
        by_chat.setdefault(m.chat_id, []).append(m)

    sent_total = 0
    failed_total = 0

    with httpx.Client() as http, Session(engine) as session:
        for chat_id, chat_matches in by_chat.items():
            if len(chat_matches) > MAX_ALERTS_PER_USER:
                LOG.warning(
                    "chat_id=%s has %d matches, capping at %d",
                    chat_id, len(chat_matches), MAX_ALERTS_PER_USER,
                )
                for m in chat_matches[MAX_ALERTS_PER_USER:]:
                    session.execute(LOG_INSERT, {
                        "sub_id": m.subscription_id,
                        "job_id": m.source_job_id,
                        "chat_id": m.chat_id,
                        "status": "skipped_rate_limit",
                        "error": "exceeded MAX_ALERTS_PER_USER",
                    })
                session.commit()
                chat_matches = chat_matches[:MAX_ALERTS_PER_USER]

            for m in chat_matches:
                msg = format_message(m)
                success, err = send_telegram(http, TELEGRAM_BOT_TOKEN, chat_id, msg)
                status = "sent" if success else "failed"
                session.execute(LOG_INSERT, {
                    "sub_id": m.subscription_id,
                    "job_id": m.source_job_id,
                    "chat_id": m.chat_id,
                    "status": status,
                    "error": err,
                })
                session.commit()
                if success:
                    sent_total += 1
                else:
                    failed_total += 1
                time.sleep(PER_CHAT_DELAY_SEC)

    engine.dispose()
    LOG.info("Done: sent=%d failed=%d", sent_total, failed_total)
    return 0 if failed_total == 0 else 3


def main() -> None:
    sys.exit(run_match())


if __name__ == "__main__":
    main()
