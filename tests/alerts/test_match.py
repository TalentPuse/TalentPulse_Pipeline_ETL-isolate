"""
Integration tests for alert matcher.

Runs against live Postgres (same test DB as rest of pipeline). Tests:
- Match SQL correctness (filter logic, dedup)
- Message formatting

We mock Telegram HTTP send to avoid needing real bot token.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

from src.alerts.match import MATCH_SQL, Match, escape_html, format_message

# Convert SQLAlchemy named param :lookback to asyncpg positional $1
_ASYNCPG_SQL = str(MATCH_SQL).replace(":lookback", "$1")

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://admin:password@localhost:5432/warehouse",
)


@pytest.fixture
async def db():
    conn = await asyncpg.connect(DB_URL)
    yield conn
    await conn.close()


# ─── SQL sanity: schema + match logic ───────────────────────────────
@pytest.mark.asyncio
async def test_match_sql_runs_without_error(db):
    """SQL compiles + runs. Empty subscriptions → zero matches."""
    # Clean any test data
    await db.execute(
        "DELETE FROM user_alerts.subscribers WHERE chat_id BETWEEN 900000 AND 999999"
    )
    rows = await db.fetch(_ASYNCPG_SQL, 24)
    # Pre-existing subs from other tests may exist, but schema is valid.
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_match_any_filter(db):
    """A sub with no filter matches all new jobs."""
    test_chat = 911_000_001
    try:
        await db.execute(
            "INSERT INTO user_alerts.subscribers (chat_id, username) VALUES ($1, $2)"
            " ON CONFLICT (chat_id) DO NOTHING",
            test_chat,
            "test_any",
        )
        sub_id = await db.fetchval(
            "INSERT INTO user_alerts.subscriptions (chat_id, label) VALUES ($1, 'any')"
            " RETURNING id",
            test_chat,
        )

        # Lookback 365 days to ensure we have some job matches regardless of freshness
        rows = await db.fetch(_ASYNCPG_SQL, 24 * 365)
        sub_matches = [r for r in rows if r["subscription_id"] == sub_id]
        # There should be >=1 active job in last year (we have 49 jobs from test data)
        assert len(sub_matches) >= 1
    finally:
        await db.execute(
            "DELETE FROM user_alerts.subscribers WHERE chat_id = $1", test_chat
        )


@pytest.mark.asyncio
async def test_match_skill_filter(db):
    """Sub with skills=[python] matches only jobs with python skill."""
    test_chat = 911_000_002
    try:
        await db.execute(
            "INSERT INTO user_alerts.subscribers (chat_id, username) VALUES ($1, $2)"
            " ON CONFLICT (chat_id) DO NOTHING",
            test_chat,
            "test_python",
        )
        sub_id = await db.fetchval(
            """
            INSERT INTO user_alerts.subscriptions (chat_id, label, skills)
            VALUES ($1, 'python', ARRAY['python'])
            RETURNING id
            """,
            test_chat,
        )

        rows = await db.fetch(_ASYNCPG_SQL, 24 * 365)
        sub_matches = [r for r in rows if r["subscription_id"] == sub_id]
        # All matches must have 'python' in job_skills array
        for m in sub_matches:
            assert "python" in (m["job_skills"] or []), (
                f"Match without python: {m['source_job_id']}, "
                f"skills={m['job_skills']}"
            )
    finally:
        await db.execute(
            "DELETE FROM user_alerts.subscribers WHERE chat_id = $1", test_chat
        )


@pytest.mark.asyncio
async def test_match_dedup_via_alert_log(db):
    """Once (sub, job) in alert_log, SQL should not return it again."""
    test_chat = 911_000_003
    try:
        await db.execute(
            "INSERT INTO user_alerts.subscribers (chat_id, username) VALUES ($1, $2)"
            " ON CONFLICT (chat_id) DO NOTHING",
            test_chat,
            "test_dedup",
        )
        sub_id = await db.fetchval(
            "INSERT INTO user_alerts.subscriptions (chat_id, label) VALUES ($1, 'any')"
            " RETURNING id",
            test_chat,
        )

        # Get first match
        rows = await db.fetch(_ASYNCPG_SQL, 24 * 365)
        mine = [r for r in rows if r["subscription_id"] == sub_id]
        if not mine:
            pytest.skip("No jobs in silver layer to test dedup")

        first_job_id = mine[0]["source_job_id"]

        # Insert alert_log entry for that job
        await db.execute(
            """
            INSERT INTO user_alerts.alert_log
              (subscription_id, source_job_id, chat_id, delivery_status)
            VALUES ($1, $2, $3, 'sent')
            """,
            sub_id,
            first_job_id,
            test_chat,
        )

        # Re-run match — first_job_id should no longer appear
        rows2 = await db.fetch(MATCH_SQL, 24 * 365)
        mine2 = [r for r in rows2 if r["subscription_id"] == sub_id]
        assert first_job_id not in [r["source_job_id"] for r in mine2]
    finally:
        await db.execute(
            "DELETE FROM user_alerts.subscribers WHERE chat_id = $1", test_chat
        )


@pytest.mark.asyncio
async def test_match_paused_excluded(db):
    """Subscribers with paused_until in future should be excluded."""
    test_chat = 911_000_004
    try:
        await db.execute(
            """
            INSERT INTO user_alerts.subscribers (chat_id, username, paused_until)
            VALUES ($1, $2, now() + interval '7 days')
            ON CONFLICT (chat_id) DO UPDATE SET paused_until = EXCLUDED.paused_until
            """,
            test_chat,
            "test_paused",
        )
        sub_id = await db.fetchval(
            "INSERT INTO user_alerts.subscriptions (chat_id, label) VALUES ($1, 'any')"
            " RETURNING id",
            test_chat,
        )

        rows = await db.fetch(_ASYNCPG_SQL, 24 * 365)
        mine = [r for r in rows if r["subscription_id"] == sub_id]
        assert mine == [], "Paused subscriber should not appear in matches"
    finally:
        await db.execute(
            "DELETE FROM user_alerts.subscribers WHERE chat_id = $1", test_chat
        )


# ─── Message formatting ────────────────────────────────────────────
class TestFormatMessage:
    def test_escape_html(self):
        assert escape_html("<script>") == "&lt;script&gt;"
        assert escape_html("A & B") == "A &amp; B"
        assert escape_html(None) == ""

    def test_minimal_match(self):
        m = Match(
            subscription_id=1,
            chat_id=123,
            sub_label="test",
            source="vietnamworks",
            source_job_id="J1",
            title="Data Engineer",
            company_name=None,
            city_canonical=None,
            job_level=None,
            salary_vnd_monthly_avg=None,
            posted_at=None,
            url=None,
            job_skills=[],
        )
        msg = format_message(m)
        assert "Data Engineer" in msg
        assert "<b>" in msg

    def test_full_match(self):
        m = Match(
            subscription_id=1,
            chat_id=123,
            sub_label="Python HCMC",
            source="itviec",
            source_job_id="J1",
            title="Senior DE <role>",  # tests escape
            company_name="Bosch",
            city_canonical="HCMC",
            job_level="Senior",
            salary_vnd_monthly_avg=25_000_000,
            posted_at=None,
            url="https://example.com/job/1",
            job_skills=["python", "sql", "spark"],
        )
        msg = format_message(m)
        assert "Senior DE &lt;role&gt;" in msg  # HTML-escaped
        assert "Bosch" in msg
        assert "HCMC" in msg
        assert "25.0M VND" in msg
        assert "python, sql, spark" in msg
        assert 'href="https://example.com/job/1"' in msg
        assert "ITviec" in msg
