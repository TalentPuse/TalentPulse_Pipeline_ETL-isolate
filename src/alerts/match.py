"""
Alert matcher — finds new jobs matching user subscriptions, sends Telegram messages.

Run as cron job after `dbt build`:
    python -m src.alerts.match

Flow:
  1. SELECT (subscription, new_job) pairs where filter matches and not yet sent
  2. For each match → format message → send via Telegram Bot API → log
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

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional

import asyncpg
import httpx

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
PER_CHAT_DELAY_SEC = 1.1  # Telegram per-chat rate limit


# ─── Match query — the SQL heart ────────────────────────────────────
# For each subscription, find jobs posted in last N hours that:
#   - match all NULL-or-overlap filter criteria
#   - haven't been sent to this subscription yet
MATCH_SQL = """
WITH job_skills AS (
    SELECT
        source_job_id,
        ARRAY_AGG(DISTINCT lower(skill_name_norm)) AS skills
    FROM dbt_dev_silver.silver_skill_long
    WHERE skill_name_norm IS NOT NULL
    GROUP BY source_job_id
),
new_jobs AS (
    SELECT
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
    LEFT JOIN job_skills s ON s.source_job_id = j.source_job_id
    WHERE j.posted_at > now() - make_interval(hours => $1)
      AND j.is_active
)
SELECT
    sub.id              AS subscription_id,
    sub.chat_id         AS chat_id,
    sub.label           AS sub_label,
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
  -- Filter match: NULL = "any", arrays use overlap (&&), scalars use comparison
  AND (sub.skills      IS NULL OR sub.skills && j.skills)
  AND (sub.cities      IS NULL OR j.city_canonical = ANY(sub.cities))
  AND (sub.job_levels  IS NULL OR j.job_level     = ANY(sub.job_levels))
  AND (sub.companies   IS NULL OR j.company_name  = ANY(sub.companies))
  AND (sub.min_salary_vnd IS NULL
       OR (j.salary_vnd_monthly_avg IS NOT NULL
           AND j.salary_vnd_monthly_avg >= sub.min_salary_vnd))
  -- Dedup: never send same (sub, job) twice
  AND NOT EXISTS (
      SELECT 1 FROM user_alerts.alert_log al
      WHERE al.subscription_id = sub.id
        AND al.source_job_id   = j.source_job_id
  )
ORDER BY sub.chat_id, j.posted_at DESC
"""


@dataclass
class Match:
    subscription_id: int
    chat_id: int
    sub_label: Optional[str]
    source_job_id: str
    title: str
    company_name: Optional[str]
    city_canonical: Optional[str]
    job_level: Optional[str]
    salary_vnd_monthly_avg: Optional[float]
    posted_at: object
    url: Optional[str]
    job_skills: list[str]


# ─── Message formatting ─────────────────────────────────────────────
def format_message(m: Match) -> str:
    """Format a job match as Telegram message (HTML mode)."""
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

    if m.url:
        lines.append("")
        lines.append(f'<a href="{escape_html(m.url)}">View on VietnamWorks ↗</a>')

    return "\n".join(lines)


def escape_html(text: object) -> str:
    """Escape HTML special chars for Telegram parse_mode=HTML."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ─── Telegram API ───────────────────────────────────────────────────
async def send_telegram(
    client: httpx.AsyncClient, token: str, chat_id: int, text: str
) -> tuple[bool, Optional[str]]:
    """Returns (success, error_message)."""
    url = TELEGRAM_API_URL.format(token=token, method="sendMessage")
    try:
        resp = await client.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15.0,
        )
        if resp.status_code == 200:
            return (True, None)
        err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        LOG.warning("Telegram send failed chat_id=%s: %s", chat_id, err)
        return (False, err)
    except httpx.HTTPError as e:
        LOG.exception("Telegram HTTP error chat_id=%s", chat_id)
        return (False, str(e))


# ─── Main flow ──────────────────────────────────────────────────────
async def run_match() -> int:
    if not TELEGRAM_BOT_TOKEN:
        LOG.error("TELEGRAM_BOT_TOKEN env var required")
        return 1

    try:
        conn = await asyncpg.connect(DATABASE_URL)
    except Exception:
        LOG.exception("DB connect failed")
        return 2

    try:
        rows = await conn.fetch(MATCH_SQL, LOOKBACK_HOURS)
    finally:
        # Don't close yet — need it for logging below
        pass

    LOG.info("Found %d match candidates (lookback %dh)", len(rows), LOOKBACK_HOURS)
    if not rows:
        await conn.close()
        return 0

    matches = [Match(**dict(r)) for r in rows]

    # Group by chat_id for rate limiting
    by_chat: dict[int, list[Match]] = {}
    for m in matches:
        by_chat.setdefault(m.chat_id, []).append(m)

    sent_total = 0
    failed_total = 0

    async with httpx.AsyncClient() as http:
        for chat_id, chat_matches in by_chat.items():
            if len(chat_matches) > MAX_ALERTS_PER_USER:
                LOG.warning(
                    "chat_id=%s has %d matches, capping at %d",
                    chat_id,
                    len(chat_matches),
                    MAX_ALERTS_PER_USER,
                )
                # Mark overflow as skipped (still log so we don't retry)
                for m in chat_matches[MAX_ALERTS_PER_USER:]:
                    await conn.execute(
                        """
                        INSERT INTO user_alerts.alert_log
                          (subscription_id, source_job_id, chat_id,
                           delivery_status, error_message)
                        VALUES ($1, $2, $3, 'skipped_rate_limit',
                                'exceeded MAX_ALERTS_PER_USER')
                        ON CONFLICT (subscription_id, source_job_id) DO NOTHING
                        """,
                        m.subscription_id,
                        m.source_job_id,
                        m.chat_id,
                    )
                chat_matches = chat_matches[:MAX_ALERTS_PER_USER]

            for m in chat_matches:
                text = format_message(m)
                success, err = await send_telegram(
                    http, TELEGRAM_BOT_TOKEN, chat_id, text
                )
                status = "sent" if success else "failed"
                await conn.execute(
                    """
                    INSERT INTO user_alerts.alert_log
                      (subscription_id, source_job_id, chat_id,
                       delivery_status, error_message)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (subscription_id, source_job_id) DO NOTHING
                    """,
                    m.subscription_id,
                    m.source_job_id,
                    m.chat_id,
                    status,
                    err,
                )
                if success:
                    sent_total += 1
                else:
                    failed_total += 1
                # Per-chat rate limit
                await asyncio.sleep(PER_CHAT_DELAY_SEC)

    await conn.close()
    LOG.info("Done: sent=%d failed=%d", sent_total, failed_total)
    return 0 if failed_total == 0 else 3


def main() -> None:
    sys.exit(asyncio.run(run_match()))


if __name__ == "__main__":
    main()
